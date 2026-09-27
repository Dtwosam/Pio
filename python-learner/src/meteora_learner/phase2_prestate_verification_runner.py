from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Sequence

from .calibration_queue import build_calibration_work_queue
from .composition_prestate import (
    build_composition_prestate_candidates,
    ingest_prestate_verification,
)
from .settings import Settings
from .storage import Storage, utc_now_iso


ExecutorRunner = Callable[..., subprocess.CompletedProcess[str]]

PRESTATE_VERIFICATION_STAGE = "PRESTATE_VERIFICATION"


@dataclass(frozen=True)
class Phase2PrestateVerificationFailure:
    position_address: str
    signature: str
    instruction_index: int
    category: str


@dataclass(frozen=True)
class Phase2PrestateVerificationRunReport:
    queue_items_seen: int
    verification_items_seen: int
    candidates_selected: int
    verdicts_ingested: int
    eligible_verdicts: int
    ineligible_verdicts: int
    failures: int
    failure_details: tuple[Phase2PrestateVerificationFailure, ...]
    read_only: bool
    detector_cursor_untouched: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _run_executor_json(
    executor_path: str | Path,
    args: Sequence[str],
    *,
    timeout_seconds: int,
    runner: ExecutorRunner,
) -> Any:
    command = [str(executor_path), *[str(value) for value in args]]
    completed = runner(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"executor failed with status {completed.returncode}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("executor returned invalid JSON") from exc


def run_phase2_prestate_verifications(
    storage: Storage,
    *,
    executor_path: str | Path,
    max_tasks: int = 10,
    timeout_seconds: int = 180,
    observed_at: str | None = None,
    runner: ExecutorRunner = subprocess.run,
) -> Phase2PrestateVerificationRunReport:
    """
    Run only strict, single-context VERIFY_PRESTATE tasks.

    Missing/ambiguous historical prestates are never reconstructed. A verifier
    verdict of eligible=false is persisted as valid negative evidence.
    """
    if max_tasks <= 0:
        raise ValueError("max_tasks must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    timestamp = observed_at or utc_now_iso()
    queue = build_calibration_work_queue(str(storage.path))
    verify_items = [
        item
        for item in queue.items
        if item.task_type == "VERIFY_PRESTATE"
        and item.signature is not None
        and item.instruction_index is not None
    ]

    def task_key(item: Any) -> str:
        return f"{item.signature}:{int(item.instruction_index)}"

    unique_items: dict[str, Any] = {}
    for item in verify_items:
        unique_items.setdefault(task_key(item), item)
    latest_attempts = storage.latest_phase2_collection_task_attempts(
        stage=PRESTATE_VERIFICATION_STAGE,
    )
    ordered_items = sorted(
        unique_items.values(),
        key=lambda item: (
            task_key(item) in latest_attempts,
            latest_attempts.get(task_key(item), 0.0),
            task_key(item),
        ),
    )[:max_tasks]

    selected: list[tuple[Any, Any]] = []
    selected_candidate_keys: set[tuple[str, str]] = set()
    failures: list[Phase2PrestateVerificationFailure] = []

    def fail(item: Any, category: str) -> None:
        storage.save_phase2_collection_task_attempt(
            stage=PRESTATE_VERIFICATION_STAGE,
            task_key=task_key(item),
            attempted_at=timestamp,
            succeeded=False,
            outcome_category=category,
        )
        failures.append(
            Phase2PrestateVerificationFailure(
                position_address=str(item.position_address),
                signature=str(item.signature),
                instruction_index=int(item.instruction_index),
                category=category,
            )
        )

    reports_by_position: dict[str, Any] = {}
    for item in ordered_items:
        position = str(item.position_address)
        if position not in reports_by_position:
            try:
                reports_by_position[position] = (
                    build_composition_prestate_candidates(
                        str(storage.path),
                        position_address=position,
                    )
                )
            except ValueError:
                reports_by_position[position] = None

        candidate_report = reports_by_position[position]
        if candidate_report is None:
            fail(item, "CANDIDATE_REPORT_UNAVAILABLE")
            continue

        candidate = next(
            (
                value
                for value in candidate_report.candidates
                if value.signature == str(item.signature)
                and value.parent_ix_index == int(item.instruction_index)
            ),
            None,
        )
        if candidate is None:
            fail(item, "CANDIDATE_MISSING")
            continue
        if not candidate.eligible_for_verification:
            fail(item, "CANDIDATE_NO_LONGER_ELIGIBLE")
            continue
        if (
            candidate.capture_slot_start is None
            or candidate.capture_slot_end is None
            or candidate.capture_slot_start != candidate.capture_slot_end
        ):
            fail(item, "CANDIDATE_NOT_SINGLE_CONTEXT")
            continue
        if (
            candidate.transaction_slot is None
            or candidate.snapshot_observed_at is None
            or not candidate.verification_addresses
        ):
            fail(item, "CANDIDATE_METADATA_INCOMPLETE")
            continue

        key = (
            candidate.signature,
            candidate.snapshot_observed_at,
        )
        if key in selected_candidate_keys:
            continue
        selected_candidate_keys.add(key)
        selected.append((item, candidate))

    ingested = 0
    eligible_verdicts = 0
    ineligible_verdicts = 0

    for item, candidate in selected:
        args = (
            "verify-prestate-env",
            candidate.signature,
            str(candidate.capture_slot_start),
            str(candidate.capture_slot_end),
            *candidate.verification_addresses,
        )
        try:
            payload = _run_executor_json(
                executor_path,
                args,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
        except subprocess.TimeoutExpired:
            fail(item, "EXECUTOR_TIMEOUT")
            continue
        except RuntimeError:
            fail(item, "EXECUTOR_FAILED")
            continue
        except ValueError:
            fail(item, "INVALID_EXECUTOR_JSON")
            continue

        if not isinstance(payload, dict):
            fail(item, "INVALID_VERIFIER_PAYLOAD")
            continue
        if str(payload.get("signature", "")) != candidate.signature:
            fail(item, "SIGNATURE_MISMATCH")
            continue

        try:
            transaction_slot = int(payload["transaction_slot"])
            capture_start = int(payload["capture_slot_start"])
            capture_end = int(payload["capture_slot_end"])
        except (KeyError, TypeError, ValueError):
            fail(item, "VERIFIER_SLOT_METADATA_INVALID")
            continue
        if transaction_slot != candidate.transaction_slot:
            fail(item, "TRANSACTION_SLOT_MISMATCH")
            continue
        if (
            capture_start != candidate.capture_slot_start
            or capture_end != candidate.capture_slot_end
        ):
            fail(item, "CAPTURE_SLOT_MISMATCH")
            continue

        try:
            result = ingest_prestate_verification(
                storage,
                payload,
                snapshot_observed_at=candidate.snapshot_observed_at,
                pool_address=candidate.pool_address,
            )
        except Exception:
            fail(item, "INGEST_FAILED")
            continue

        storage.save_phase2_collection_task_attempt(
            stage=PRESTATE_VERIFICATION_STAGE,
            task_key=task_key(item),
            attempted_at=timestamp,
            succeeded=True,
            outcome_category=(
                "VERDICT_ELIGIBLE"
                if result.eligible
                else "VERDICT_INELIGIBLE"
            ),
        )
        ingested += 1
        if result.eligible:
            eligible_verdicts += 1
        else:
            ineligible_verdicts += 1

    return Phase2PrestateVerificationRunReport(
        queue_items_seen=len(queue.items),
        verification_items_seen=len(verify_items),
        candidates_selected=len(selected),
        verdicts_ingested=ingested,
        eligible_verdicts=eligible_verdicts,
        ineligible_verdicts=ineligible_verdicts,
        failures=len(failures),
        failure_details=tuple(failures),
        read_only=True,
        detector_cursor_untouched=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run bounded read-only strict prestate verifications from the "
            "Phase-2 calibration queue."
        )
    )
    parser.add_argument(
        "--executor",
        default="/opt/pio/rust-executor/target/release/meteora-executor",
    )
    parser.add_argument("--max-tasks", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--database")
    args = parser.parse_args()

    settings = Settings.from_env()
    storage = Storage(
        Path(args.database)
        if args.database
        else settings.database_path
    )
    report = run_phase2_prestate_verifications(
        storage,
        executor_path=args.executor,
        max_tasks=args.max_tasks,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
