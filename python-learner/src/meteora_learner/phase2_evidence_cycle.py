from __future__ import annotations

import argparse
from collections import Counter
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
from .phase2_rpc_guard import Phase2RpcRateLimited
from .reconciliation_corpus import build_reconciliation_corpus
from .settings import Settings
from .storage import Storage, utc_now_iso


PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE = (
    "PHASE2_EVIDENCE_CYCLE_PROGRESS_V1"
)


@dataclass(frozen=True)
class Phase2EvidenceCycleTaskCount:
    task_type: str
    count: int


@dataclass(frozen=True)
class Phase2EvidenceCycleProgress:
    pool_address: str
    finished_at: str
    overall_status: str
    stages_successful: int
    stages_partial: int
    stages_failed: int
    stages_skipped: int
    stage_statuses: tuple[tuple[str, str], ...]
    stage_outcomes: tuple[tuple[str, str, str | None], ...]
    rpc_rate_limited: bool
    rpc_circuit_open: bool
    reconciliation: dict[str, Any] | None
    calibration: dict[str, Any] | None
    work_queue_items: int
    work_queue_task_counts: tuple[Phase2EvidenceCycleTaskCount, ...]
    read_only: bool
    actionable: bool
    live_authorized: bool
    promotion_gate_evaluated: bool
    phase_promotion_performed: bool
    qualified: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase2EvidenceCycleStage:
    name: str
    status: str
    failure_category: str | None
    result: dict[str, Any] | None


@dataclass(frozen=True)
class Phase2ReadOnlyEvidenceCycleReport:
    pool_address: str
    collection_scope: str
    final_evidence_scope: str
    started_at: str
    finished_at: str
    stages_successful: int
    stages_partial: int
    stages_failed: int
    stages_skipped: int
    stages: tuple[Phase2EvidenceCycleStage, ...]
    reconciliation_corpus: dict[str, Any] | None
    calibration_evidence: dict[str, Any] | None
    work_queue: dict[str, Any] | None
    read_only: bool
    actionable: bool
    live_authorized: bool
    promotion_gate_evaluated: bool
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

    Ordinary stage failures are isolated so later read-only collection can
    continue. A confirmed Solana RPC rate-limit response opens a cycle-local
    circuit and skips only the remaining Solana RPC stages; local reconciliation
    and calibration still run. The cycle never promotes a phase, selects an
    action, moves capital, touches the detector cursor, or controls services.
    """
    if not pool_address.strip():
        raise ValueError("pool_address is required")

    started_at = now()
    stages: list[Phase2EvidenceCycleStage] = []
    rpc_circuit_open = False

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
    except Phase2RpcRateLimited:
        rpc_circuit_open = True
        stages.append(
            _stage(
                name="POSITION_OBSERVATIONS",
                status="FAILED",
                failure_category="RPC_RATE_LIMITED",
            )
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

    if rpc_circuit_open:
        stages.append(
            _stage(
                name="TRANSACTION_REINSPECTION",
                status="SKIPPED",
                failure_category="RPC_CIRCUIT_OPEN",
            )
        )
    else:
        try:
            reinspection = run_phase2_calibration_reinspection(
                storage,
                executor_path=executor_path,
                max_tasks=max_reinspection_tasks,
                timeout_seconds=reinspection_timeout_seconds,
                observed_at=now(),
                runner=runner,
            )
        except Phase2RpcRateLimited:
            rpc_circuit_open = True
            stages.append(
                _stage(
                    name="TRANSACTION_REINSPECTION",
                    status="FAILED",
                    failure_category="RPC_RATE_LIMITED",
                )
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

    if rpc_circuit_open:
        stages.append(
            _stage(
                name="PRESTATE_VERIFICATION",
                status="SKIPPED",
                failure_category="RPC_CIRCUIT_OPEN",
            )
        )
    else:
        try:
            verification = run_phase2_prestate_verifications(
                storage,
                executor_path=executor_path,
                max_tasks=max_prestate_tasks,
                timeout_seconds=prestate_timeout_seconds,
                observed_at=now(),
                runner=runner,
            )
        except Phase2RpcRateLimited:
            rpc_circuit_open = True
            stages.append(
                _stage(
                    name="PRESTATE_VERIFICATION",
                    status="FAILED",
                    failure_category="RPC_RATE_LIMITED",
                )
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

    reconciliation_record = None
    try:
        reconciliation = build_reconciliation_corpus(
            str(storage.path)
        )
    except Exception:
        stages.append(
            _stage(
                name="RECONCILIATION_CORPUS",
                status="FAILED",
                failure_category="RECONCILIATION_CORPUS_FAILED",
            )
        )
    else:
        reconciliation_record = reconciliation.to_record()
        stages.append(
            _stage(
                name="RECONCILIATION_CORPUS",
                status=(
                    "SUCCESS"
                    if reconciliation.strict_math_gate_passed
                    else "PARTIAL"
                ),
                result=reconciliation,
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
                status=(
                    "PARTIAL"
                    if calibration.evidence_gaps
                    else "SUCCESS"
                ),
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
                status=(
                    "PARTIAL"
                    if queue.items
                    else "SUCCESS"
                ),
                result=queue,
            )
        )

    return Phase2ReadOnlyEvidenceCycleReport(
        pool_address=pool_address,
        collection_scope="POOL",
        final_evidence_scope="DATABASE_GLOBAL",
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
        stages_skipped=sum(
            item.status == "SKIPPED" for item in stages
        ),
        stages=tuple(stages),
        reconciliation_corpus=reconciliation_record,
        calibration_evidence=calibration_record,
        work_queue=queue_record,
        read_only=True,
        actionable=False,
        live_authorized=False,
        promotion_gate_evaluated=False,
        phase_promotion_performed=False,
        detector_cursor_untouched=True,
        service_control_performed=False,
    )


def _compact_reconciliation(
    record: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if record is None:
        return None
    keys = (
        "positions_seen",
        "amount_positions_eligible",
        "amount_positions_exact",
        "amount_positions_provenance_ineligible",
        "amount_bins_checked",
        "amount_mismatched_bins",
        "fee_intervals_seen",
        "fee_intervals_eligible",
        "fee_intervals_exact",
        "fee_intervals_provenance_ineligible",
        "fee_bins_checked",
        "fee_mismatched_bins",
        "reward_intervals_seen",
        "reward_intervals_eligible",
        "reward_intervals_exact",
        "reward_intervals_provenance_ineligible",
        "reward_bins_checked",
        "reward_bins_with_checkpoint_growth",
        "reward_mismatched_bins",
        "strict_math_gate_passed",
    )
    return {
        key: record[key]
        for key in keys
        if key in record
    }


def _compact_calibration(
    record: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if record is None:
        return None
    keys = (
        "add_positions",
        "composition_add_events",
        "composition_eligible_samples",
        "composition_exact_samples",
        "composition_mismatched_samples",
        "composition_ineligible_samples",
        "add_execution_events",
        "add_execution_request_decodes",
        "add_execution_matched_events",
        "add_execution_unmatched_samples",
        "add_active_guard_samples",
        "rebalance_execution_events",
        "rebalance_execution_request_decodes",
        "rebalance_execution_matched_events",
        "rebalance_active_guard_samples",
        "transaction_fee_samples",
    )
    compact = {
        key: record[key]
        for key in keys
        if key in record
    }
    gaps = record.get("evidence_gaps")
    compact["evidence_gap_count"] = (
        len(gaps) if isinstance(gaps, (list, tuple)) else 0
    )
    return compact


def build_phase2_evidence_cycle_progress(
    report: Phase2ReadOnlyEvidenceCycleReport,
) -> Phase2EvidenceCycleProgress:
    if (
        not report.read_only
        or report.actionable
        or report.live_authorized
        or report.promotion_gate_evaluated
        or report.phase_promotion_performed
        or not report.detector_cursor_untouched
        or report.service_control_performed
    ):
        raise ValueError(
            "Phase-2 evidence-cycle progress crossed the read-only boundary"
        )

    if report.stages_failed:
        overall_status = "FAILED"
    elif report.stages_partial or report.stages_skipped:
        overall_status = "PARTIAL"
    else:
        overall_status = "SUCCESS"

    queue_items: list[dict[str, Any]] = []
    if isinstance(report.work_queue, dict):
        raw_items = report.work_queue.get("items")
        if isinstance(raw_items, (list, tuple)):
            queue_items = [
                item for item in raw_items if isinstance(item, dict)
            ]
    task_counts = Counter(
        str(item.get("task_type", "UNKNOWN"))
        for item in queue_items
    )

    return Phase2EvidenceCycleProgress(
        pool_address=report.pool_address,
        finished_at=report.finished_at,
        overall_status=overall_status,
        stages_successful=report.stages_successful,
        stages_partial=report.stages_partial,
        stages_failed=report.stages_failed,
        stages_skipped=report.stages_skipped,
        stage_statuses=tuple(
            (stage.name, stage.status)
            for stage in report.stages
        ),
        stage_outcomes=tuple(
            (
                stage.name,
                stage.status,
                stage.failure_category,
            )
            for stage in report.stages
        ),
        rpc_rate_limited=any(
            stage.failure_category == "RPC_RATE_LIMITED"
            for stage in report.stages
        ),
        rpc_circuit_open=any(
            stage.failure_category in {
                "RPC_RATE_LIMITED",
                "RPC_CIRCUIT_OPEN",
            }
            for stage in report.stages
        ),
        reconciliation=_compact_reconciliation(
            report.reconciliation_corpus
        ),
        calibration=_compact_calibration(
            report.calibration_evidence
        ),
        work_queue_items=len(queue_items),
        work_queue_task_counts=tuple(
            Phase2EvidenceCycleTaskCount(
                task_type=task_type,
                count=count,
            )
            for task_type, count in sorted(
                task_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        read_only=True,
        actionable=False,
        live_authorized=False,
        promotion_gate_evaluated=False,
        phase_promotion_performed=False,
        qualified=False,
    )


def persist_phase2_evidence_cycle_progress(
    storage: Storage,
    *,
    report: Phase2ReadOnlyEvidenceCycleReport,
) -> int:
    progress = build_phase2_evidence_cycle_progress(report)
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE2_EVIDENCE_CYCLE_PROGRESS_TYPE,
        pool_address=progress.pool_address,
        as_of=progress.finished_at,
        status=f"COLLECTION_{progress.overall_status}",
        qualified=False,
        evidence=progress.to_record(),
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
    parser.add_argument(
        "--persist-progress",
        action="store_true",
        help=(
            "Persist a compact non-qualified technical progress record "
            "after the collection step"
        ),
    )
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
    output = report.to_record()
    if args.persist_progress:
        output["progress_evidence_id"] = (
            persist_phase2_evidence_cycle_progress(
                storage,
                report=report,
            )
        )
    print(json.dumps(output, indent=2))
    if report.stages_partial or report.stages_failed or report.stages_skipped:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
