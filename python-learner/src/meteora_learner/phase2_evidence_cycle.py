from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable

from .calibration_queue import build_calibration_work_queue
from .calibration_status import build_phase2_calibration_evidence
from .phase2_calibration_reinspection import (
    run_phase2_calibration_reinspection,
)
from .phase2_position_observation import (
    collect_phase2_position_observations,
)
from .phase2_prestate_verification_runner import (
    run_phase2_prestate_verifications,
)
from .phase2_research_quotes import collect_phase2_research_quotes
from .settings import Settings
from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class Phase2EvidenceCycleStage:
    name: str
    status: str
    failure_category: str | None
    result: dict[str, Any] | None


@dataclass(frozen=True)
class Phase2ReadOnlyEvidenceCycleReport:
    pool_address: str
    started_at: str
    finished_at: str
    stages_successful: int
    stages_partial: int
    stages_failed: int
    stages: tuple[Phase2EvidenceCycleStage, ...]
    calibration_evidence: dict[str, Any] | None
    work_queue: dict[str, Any] | None
    read_only: bool
    actionable: bool
    live_authorized: bool
    phase_promotion_performed: bool
    detector_cursor_untouched: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _stage(
    *,
    name: str,
    status: str,
    result: Any | None = None,
    failure_category: str | None = None,
) -> Phase2EvidenceCycleStage:
    record = None
    if result is not None:
        record = (
            result.to_record()
            if hasattr(result, "to_record")
            else dict(result)
        )
    return Phase2EvidenceCycleStage(
        name=name,
        status=status,
        failure_category=failure_category,
        result=record,
    )


def run_phase2_read_only_evidence_cycle(
    storage: Storage,
    *,
    pool_address: str,
    executor_path: str | Path,
    max_positions_per_run: int = 50,
    max_reinspection_tasks: int = 25,
    max_prestate_tasks: int = 10,
    position_timeout_seconds: int = 120,
    reinspection_timeout_seconds: int = 120,
    prestate_timeout_seconds: int = 180,
    fetch_quote: Callable[[str], Any] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: Callable[[], str] = utc_now_iso,
) -> Phase2ReadOnlyEvidenceCycleReport:
    """
    Run one bounded, read-only Phase-2 evidence collection step.

    The stages are intentionally independent: a failure in one stage is recorded
    categorically and does not suppress later read-only collection. The cycle
    never promotes a phase, selects an action, moves capital, touches the
    detector cursor, or controls services.
    """
    if not pool_address.strip():
        raise ValueError("pool_address is required")

    started_at = now()
    stages: list[Phase2EvidenceCycleStage] = []

    try:
        quotes = collect_phase2_research_quotes(
            storage,
            pool_address=pool_address,
            fetch_quote=fetch_quote,
            now=now,
        )
    except Exception:
        stages.append(
            _stage(
                name="RESEARCH_QUOTES",
                status="FAILED",
                failure_category="QUOTE_STAGE_FAILED",
            )
        )
    else:
        stages.append(
            _stage(
                name="RESEARCH_QUOTES",
                status=(
                    "PARTIAL" if quotes.quotes_failed else "SUCCESS"
                ),
                result=quotes,
            )
        )

    try:
        positions = collect_phase2_position_observations(
            storage,
            pool_address=pool_address,
            executor_path=executor_path,
            max_positions_per_run=max_positions_per_run,
            timeout_seconds=position_timeout_seconds,
            observed_at=now(),
            runner=runner,
        )
    except Exception:
        stages.append(
            _stage(
                name="POSITION_OBSERVATIONS",
                status="FAILED",
                failure_category="POSITION_STAGE_FAILED",
            )
        )
    else:
        stages.append(
            _stage(
                name="POSITION_OBSERVATIONS",
                status=(
                    "PARTIAL"
                    if positions.failures or positions.discovery_truncated
                    else "SUCCESS"
                ),
                result=positions,
            )
        )

    try:
        reinspection = run_phase2_calibration_reinspection(
            storage,
            executor_path=executor_path,
            max_tasks=max_reinspection_tasks,
            timeout_seconds=reinspection_timeout_seconds,
            observed_at=now(),
            runner=runner,
        )
    except Exception:
        stages.append(
            _stage(
                name="TRANSACTION_REINSPECTION",
                status="FAILED",
                failure_category="REINSPECTION_STAGE_FAILED",
            )
        )
    else:
        stages.append(
            _stage(
                name="TRANSACTION_REINSPECTION",
                status=(
                    "PARTIAL"
                    if reinspection.signatures_failed
                    else "SUCCESS"
                ),
                result=reinspection,
            )
        )

    try:
        verification = run_phase2_prestate_verifications(
            storage,
            executor_path=executor_path,
            max_tasks=max_prestate_tasks,
            timeout_seconds=prestate_timeout_seconds,
            observed_at=now(),
            runner=runner,
        )
    except Exception:
        stages.append(
            _stage(
                name="PRESTATE_VERIFICATION",
                status="FAILED",
                failure_category="PRESTATE_STAGE_FAILED",
            )
        )
    else:
        stages.append(
            _stage(
                name="PRESTATE_VERIFICATION",
                status=(
                    "PARTIAL" if verification.failures else "SUCCESS"
                ),
                result=verification,
            )
        )

    calibration_record = None
    try:
        calibration = build_phase2_calibration_evidence(
            str(storage.path)
        )
    except Exception:
        stages.append(
            _stage(
                name="CALIBRATION_EVIDENCE",
                status="FAILED",
                failure_category="CALIBRATION_EVIDENCE_FAILED",
            )
        )
    else:
        calibration_record = calibration.to_record()
        stages.append(
            _stage(
                name="CALIBRATION_EVIDENCE",
                status="SUCCESS",
                result=calibration,
            )
        )

    queue_record = None
    try:
        queue = build_calibration_work_queue(str(storage.path))
    except Exception:
        stages.append(
            _stage(
                name="CALIBRATION_WORK_QUEUE",
                status="FAILED",
                failure_category="WORK_QUEUE_FAILED",
            )
        )
    else:
        queue_record = queue.to_record()
        stages.append(
            _stage(
                name="CALIBRATION_WORK_QUEUE",
                status="SUCCESS",
                result=queue,
            )
        )

    return Phase2ReadOnlyEvidenceCycleReport(
        pool_address=pool_address,
        started_at=started_at,
        finished_at=now(),
        stages_successful=sum(
            item.status == "SUCCESS" for item in stages
        ),
        stages_partial=sum(
            item.status == "PARTIAL" for item in stages
        ),
        stages_failed=sum(
            item.status == "FAILED" for item in stages
        ),
        stages=tuple(stages),
        calibration_evidence=calibration_record,
        work_queue=queue_record,
        read_only=True,
        actionable=False,
        live_authorized=False,
        phase_promotion_performed=False,
        detector_cursor_untouched=True,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run one bounded read-only Phase-2 evidence collection step."
        )
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
    parser.add_argument("--max-positions-per-run", type=int, default=50)
    parser.add_argument("--max-reinspection-tasks", type=int, default=25)
    parser.add_argument("--max-prestate-tasks", type=int, default=10)
    parser.add_argument("--position-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--reinspection-timeout-seconds",
        type=int,
        default=120,
    )
    parser.add_argument(
        "--prestate-timeout-seconds",
        type=int,
        default=180,
    )
    parser.add_argument("--database")
    args = parser.parse_args()

    if not args.pool:
        parser.error("--pool or PIO_PHASE2_POSITION_POOL is required")

    settings = Settings.from_env()
    storage = Storage(
        Path(args.database)
        if args.database
        else settings.database_path
    )
    report = run_phase2_read_only_evidence_cycle(
        storage,
        pool_address=args.pool,
        executor_path=args.executor,
        max_positions_per_run=args.max_positions_per_run,
        max_reinspection_tasks=args.max_reinspection_tasks,
        max_prestate_tasks=args.max_prestate_tasks,
        position_timeout_seconds=args.position_timeout_seconds,
        reinspection_timeout_seconds=args.reinspection_timeout_seconds,
        prestate_timeout_seconds=args.prestate_timeout_seconds,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.stages_partial or report.stages_failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
