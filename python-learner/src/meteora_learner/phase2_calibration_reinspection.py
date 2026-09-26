from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Sequence

from .calibration_queue import build_calibration_work_queue
from .settings import Settings
from .storage import Storage, utc_now_iso
from .transaction_event_ingest import ingest_transaction_events


ExecutorRunner = Callable[..., subprocess.CompletedProcess[str]]

REINSPECTION_TASK_TYPES = frozenset(
    {
        "INSPECT_TRANSACTION",
        "REINSPECT_TRANSACTION",
        "REINSPECT_REBALANCE_TRANSACTION",
    }
)


@dataclass(frozen=True)
class Phase2CalibrationReinspectionFailure:
    signature: str
    category: str


@dataclass(frozen=True)
class Phase2CalibrationReinspectionReport:
    observed_at: str
    queue_items_seen: int
    reinspection_items_seen: int
    unique_signatures_selected: int
    signatures_succeeded: int
    signatures_failed: int
    events_ingested: int
    selected_signatures: tuple[str, ...]
    failure_details: tuple[Phase2CalibrationReinspectionFailure, ...]
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
        raise RuntimeError(
            f"executor failed with status {completed.returncode}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("executor returned invalid JSON") from exc


def run_phase2_calibration_reinspection(
    storage: Storage,
    *,
    executor_path: str | Path,
    max_tasks: int = 25,
    timeout_seconds: int = 120,
    observed_at: str | None = None,
    runner: ExecutorRunner = subprocess.run,
) -> Phase2CalibrationReinspectionReport:
    """
    Re-run only read-only transaction decode tasks from the Phase-2 work queue.

    This function never executes VERIFY_PRESTATE, future-sample tasks, mismatch
    review tasks, service control, git operations, or detector cursor changes.
    RPC configuration stays in the executor environment rather than argv.
    """
    if max_tasks <= 0:
        raise ValueError("max_tasks must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    timestamp = observed_at or utc_now_iso()
    queue = build_calibration_work_queue(str(storage.path))
    eligible_items = [
        item
        for item in queue.items
        if item.task_type in REINSPECTION_TASK_TYPES
        and item.signature is not None
    ]

    selected: list[str] = []
    seen: set[str] = set()
    for item in eligible_items:
        signature = str(item.signature)
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(signature)
        if len(selected) >= max_tasks:
            break

    failures: list[Phase2CalibrationReinspectionFailure] = []
    succeeded = 0
    events_ingested = 0

    def fail(signature: str, category: str) -> None:
        failures.append(
            Phase2CalibrationReinspectionFailure(
                signature=signature,
                category=category,
            )
        )

    for signature in selected:
        try:
            payload = _run_executor_json(
                executor_path,
                ("inspect-transaction-events-env", signature),
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
        except subprocess.TimeoutExpired:
            fail(signature, "EXECUTOR_TIMEOUT")
            continue
        except RuntimeError:
            fail(signature, "EXECUTOR_FAILED")
            continue
        except ValueError:
            fail(signature, "INVALID_EXECUTOR_JSON")
            continue

        if not isinstance(payload, dict):
            fail(signature, "INVALID_INSPECTION_PAYLOAD")
            continue
        if str(payload.get("signature", "")) != signature:
            fail(signature, "SIGNATURE_MISMATCH")
            continue

        try:
            result = ingest_transaction_events(
                storage,
                payload,
                observed_at=timestamp,
            )
        except Exception:
            fail(signature, "INGEST_FAILED")
            continue

        succeeded += 1
        events_ingested += result.events

    return Phase2CalibrationReinspectionReport(
        observed_at=timestamp,
        queue_items_seen=len(queue.items),
        reinspection_items_seen=len(eligible_items),
        unique_signatures_selected=len(selected),
        signatures_succeeded=succeeded,
        signatures_failed=len(failures),
        events_ingested=events_ingested,
        selected_signatures=tuple(selected),
        failure_details=tuple(failures),
        read_only=True,
        detector_cursor_untouched=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run bounded read-only Phase-2 transaction reinspections from "
            "the calibration work queue."
        )
    )
    parser.add_argument(
        "--executor",
        default="/opt/pio/rust-executor/target/release/meteora-executor",
    )
    parser.add_argument("--max-tasks", type=int, default=25)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--database")
    args = parser.parse_args()

    settings = Settings.from_env()
    storage = Storage(
        Path(args.database)
        if args.database
        else settings.database_path
    )
    report = run_phase2_calibration_reinspection(
        storage,
        executor_path=args.executor,
        max_tasks=args.max_tasks,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.signatures_failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
