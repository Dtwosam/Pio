from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Sequence

from .position_ingest import ingest_position_snapshot
from .settings import Settings
from .storage import Storage, utc_now_iso


ExecutorRunner = Callable[..., subprocess.CompletedProcess[str]]


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

    latest_by_position: dict[str, float] = {}
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
        if row[1] is not None:
            latest_by_position[str(row[0])] = float(row[1])

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
    failed: list[str] = []
    for item in selected_positions:
        position_address = str(item["position_address"])

        try:
            snapshot = _run_executor_json(
                executor_path,
                ("inspect-position-env", position_address),
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            if not isinstance(snapshot, dict):
                raise ValueError("position inspection must return a JSON object")
            if str(snapshot.get("position_address", "")) != position_address:
                raise ValueError("position inspection returned a different position")
            if str(snapshot.get("pool_address", "")) != pool_address:
                raise ValueError("position inspection returned a different pool")
            result = ingest_position_snapshot(
                storage,
                snapshot,
                observed_at=timestamp,
            )
            snapshots_saved += 1
            bins_saved += result.bins
        except Exception:
            failed.append(position_address)

    return Phase2PositionObservationResult(
        pool_address=pool_address,
        observed_at=timestamp,
        positions_found=positions_found,
        positions_returned=positions_returned,
        positions_selected=len(selected_positions),
        discovery_truncated=truncated,
        snapshots_saved=snapshots_saved,
        bins_saved=bins_saved,
        failures=len(failed),
        failed_positions=tuple(failed),
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
