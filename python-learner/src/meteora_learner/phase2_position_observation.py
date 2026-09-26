from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Sequence

from .position_ingest import ingest_position_snapshot
from .reconciliation_corpus import build_reconciliation_corpus
from .settings import Settings
from .storage import Storage, utc_now_iso


ExecutorRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Phase2ReconciliationProgress:
    positions_seen: int
    amount_positions_eligible: int
    amount_positions_exact: int
    amount_positions_provenance_ineligible: int
    amount_bins_checked: int
    amount_mismatched_bins: int
    fee_intervals_seen: int
    fee_intervals_eligible: int
    fee_intervals_exact: int
    fee_intervals_provenance_ineligible: int
    fee_bins_checked: int
    fee_mismatched_bins: int
    reward_intervals_seen: int
    reward_intervals_eligible: int
    reward_intervals_exact: int
    reward_intervals_provenance_ineligible: int
    reward_bins_checked: int
    reward_bins_with_checkpoint_growth: int
    reward_mismatched_bins: int
    strict_math_gate_passed: bool


@dataclass(frozen=True)
class Phase2PositionFailure:
    position_address: str
    category: str


@dataclass(frozen=True)
class Phase2PositionObservationResult:
    pool_address: str
    observed_at: str
    positions_found: int
    positions_returned: int
    positions_selected: int
    discovery_truncated: bool
    snapshots_saved: int
    bins_saved: int
    failures: int
    failed_positions: tuple[str, ...]
    failure_details: tuple[Phase2PositionFailure, ...]
    reconciliation_progress: Phase2ReconciliationProgress | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _run_executor_json(
    executor_path: str | Path,
    args: Sequence[str],
    *,
    timeout_seconds: int,
    runner: ExecutorRunner = subprocess.run,
) -> Any:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    command = [str(executor_path), *[str(value) for value in args]]
    completed = runner(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(
            f"executor failed with status {completed.returncode}: {stderr[:500]}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("executor returned invalid JSON") from exc


def _require_single_capture_slot(snapshot: dict[str, Any]) -> int:
    start_raw = snapshot.get("capture_slot_start")
    end_raw = snapshot.get("capture_slot_end")
    if start_raw is None or end_raw is None:
        raise ValueError(
            "position inspection is missing capture-slot provenance"
        )
    start = int(start_raw)
    end = int(end_raw)
    if start < 0 or end < 0:
        raise ValueError("position capture slots cannot be negative")
    if start != end:
        raise ValueError(
            f"position inspection is not single-context: {start}..{end}"
        )
    return start


def collect_phase2_position_observations(
    storage: Storage,
    *,
    pool_address: str,
    executor_path: str | Path,
    max_positions_per_run: int = 50,
    timeout_seconds: int = 120,
    observed_at: str | None = None,
    runner: ExecutorRunner = subprocess.run,
) -> Phase2PositionObservationResult:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if max_positions_per_run <= 0 or max_positions_per_run > 5_000:
        raise ValueError("max_positions_per_run must be between 1 and 5000")

    timestamp = observed_at or utc_now_iso()
    discovery = _run_executor_json(
        executor_path,
        ("discover-pool-positions-env", pool_address, "5000"),
        timeout_seconds=timeout_seconds,
        runner=runner,
    )
    if not isinstance(discovery, dict):
        raise ValueError("position discovery must return a JSON object")
    positions = discovery.get("positions")
    if not isinstance(positions, list):
        raise ValueError("position discovery is missing positions")

    positions_found = int(discovery.get("positions_found", len(positions)))
    positions_returned = int(discovery.get("positions_returned", len(positions)))
    truncated = bool(discovery.get("truncated", positions_found > positions_returned))

    latest_by_position: dict[str, float] = (
        storage.latest_phase2_position_observation_attempts(
            pool_address=pool_address,
        )
    )
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT position_address, MAX(julianday(observed_at)) AS latest_jd
            FROM chain_position_snapshots
            WHERE pool_address = ?
            GROUP BY position_address
            """,
            (pool_address,),
        ).fetchall()
    for row in rows:
        if row[1] is None:
            continue
        address = str(row[0])
        snapshot_jd = float(row[1])
        latest_by_position[address] = max(
            latest_by_position.get(address, snapshot_jd),
            snapshot_jd,
        )

    unique_positions: dict[str, dict[str, Any]] = {}
    for item in positions:
        if not isinstance(item, dict):
            raise ValueError("position discovery contains a non-object entry")
        position_address = str(item.get("position_address", "")).strip()
        if not position_address:
            raise ValueError("position discovery entry is missing position_address")
        unique_positions.setdefault(position_address, item)

    selected_positions = sorted(
        unique_positions.values(),
        key=lambda item: (
            str(item["position_address"]) in latest_by_position,
            latest_by_position.get(str(item["position_address"]), 0.0),
            str(item["position_address"]),
        ),
    )[:max_positions_per_run]

    snapshots_saved = 0
    bins_saved = 0
    failure_details: list[Phase2PositionFailure] = []

    def fail(position_address: str, category: str) -> None:
        storage.save_phase2_position_observation_attempt(
            pool_address=pool_address,
            position_address=position_address,
            attempted_at=timestamp,
            succeeded=False,
            failure_category=category,
        )
        failure_details.append(
            Phase2PositionFailure(
                position_address=position_address,
                category=category,
            )
        )

    for item in selected_positions:
        position_address = str(item["position_address"])

        try:
            snapshot = _run_executor_json(
                executor_path,
                ("inspect-position-env", position_address),
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
        except subprocess.TimeoutExpired:
            fail(position_address, "EXECUTOR_TIMEOUT")
            continue
        except RuntimeError:
            fail(position_address, "EXECUTOR_FAILED")
            continue
        except ValueError:
            fail(position_address, "INVALID_EXECUTOR_JSON")
            continue

        if not isinstance(snapshot, dict):
            fail(position_address, "INVALID_INSPECTION_PAYLOAD")
            continue
        if str(snapshot.get("position_address", "")) != position_address:
            fail(position_address, "POSITION_MISMATCH")
            continue
        if str(snapshot.get("pool_address", "")) != pool_address:
            fail(position_address, "POOL_MISMATCH")
            continue
        try:
            capture_slot = _require_single_capture_slot(snapshot)
        except (TypeError, ValueError):
            fail(position_address, "CAPTURE_PROVENANCE")
            continue

        try:
            result = ingest_position_snapshot(
                storage,
                snapshot,
                observed_at=timestamp,
            )
        except Exception:
            fail(position_address, "INGEST_FAILED")
            continue

        storage.save_phase2_position_observation_attempt(
            pool_address=pool_address,
            position_address=position_address,
            attempted_at=timestamp,
            succeeded=True,
            capture_slot=capture_slot,
        )
        snapshots_saved += 1
        bins_saved += result.bins

    reconciliation_progress = None
    try:
        corpus = build_reconciliation_corpus(str(storage.path))
    except ValueError:
        corpus = None
    if corpus is not None:
        reconciliation_progress = Phase2ReconciliationProgress(
            positions_seen=corpus.positions_seen,
            amount_positions_eligible=corpus.amount_positions_eligible,
            amount_positions_exact=corpus.amount_positions_exact,
            amount_positions_provenance_ineligible=getattr(
                corpus,
                "amount_positions_provenance_ineligible",
                0,
            ),
            amount_bins_checked=corpus.amount_bins_checked,
            amount_mismatched_bins=corpus.amount_mismatched_bins,
            fee_intervals_seen=corpus.fee_intervals_seen,
            fee_intervals_eligible=corpus.fee_intervals_eligible,
            fee_intervals_exact=corpus.fee_intervals_exact,
            fee_intervals_provenance_ineligible=getattr(
                corpus,
                "fee_intervals_provenance_ineligible",
                0,
            ),
            fee_bins_checked=corpus.fee_bins_checked,
            fee_mismatched_bins=corpus.fee_mismatched_bins,
            reward_intervals_seen=corpus.reward_intervals_seen,
            reward_intervals_eligible=corpus.reward_intervals_eligible,
            reward_intervals_exact=corpus.reward_intervals_exact,
            reward_intervals_provenance_ineligible=getattr(
                corpus,
                "reward_intervals_provenance_ineligible",
                0,
            ),
            reward_bins_checked=corpus.reward_bins_checked,
            reward_bins_with_checkpoint_growth=(
                corpus.reward_bins_with_checkpoint_growth
            ),
            reward_mismatched_bins=corpus.reward_mismatched_bins,
            strict_math_gate_passed=corpus.strict_math_gate_passed,
        )

    return Phase2PositionObservationResult(
        pool_address=pool_address,
        observed_at=timestamp,
        positions_found=positions_found,
        positions_returned=positions_returned,
        positions_selected=len(selected_positions),
        discovery_truncated=truncated,
        snapshots_saved=snapshots_saved,
        bins_saved=bins_saved,
        failures=len(failure_details),
        failed_positions=tuple(
            item.position_address for item in failure_details
        ),
        failure_details=tuple(failure_details),
        reconciliation_progress=reconciliation_progress,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect read-only Phase 2 position snapshots without economic filtering."
    )
    parser.add_argument(
        "--pool",
        default=os.getenv("PIO_PHASE2_POSITION_POOL"),
        help="Meteora DLMM pool address (or PIO_PHASE2_POSITION_POOL)",
    )
    parser.add_argument(
        "--executor",
        default="/opt/pio/rust-executor/target/release/meteora-executor",
    )
    parser.add_argument(
        "--max-positions-per-run",
        type=int,
        default=int(os.getenv("PIO_PHASE2_POSITION_MAX_PER_RUN", "50")),
    )
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--database")
    args = parser.parse_args()

    if not args.pool:
        parser.error("--pool or PIO_PHASE2_POSITION_POOL is required")

    settings = Settings.from_env()
    storage = Storage(Path(args.database) if args.database else settings.database_path)
    result = collect_phase2_position_observations(
        storage,
        pool_address=args.pool,
        executor_path=args.executor,
        max_positions_per_run=args.max_positions_per_run,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result.to_record(), indent=2))
    if result.failures or result.discovery_truncated:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
