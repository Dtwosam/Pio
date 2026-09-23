from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .paper_audit import PaperLedgerAuditReport, audit_paper_ledger
from .paper_endurance import (
    PaperEnduranceCriteria,
    PaperEnduranceReport,
    build_paper_endurance_report,
)
from .phase_promotion import PHASE3, PHASE3_EVIDENCE_TYPE
from .storage import Storage


@dataclass(frozen=True)
class Phase5PromotionCriteria:
    min_runtime_hours: float = 72.0
    min_terminal_ticks: int = 500
    min_success_rate_pct: float = 99.0
    max_dependency_blocked_pct: float = 5.0
    max_consecutive_failures: int = 1
    max_stale_running_ticks: int = 0
    stale_running_after_seconds: int = 900
    min_applied_chain_valuations: int = 100
    min_distinct_positions_valued: int = 3
    min_closed_positions: int = 3
    min_distinct_pools: int = 2

    def validate(self) -> None:
        PaperEnduranceCriteria(
            min_runtime_hours=self.min_runtime_hours,
            min_terminal_ticks=self.min_terminal_ticks,
            min_success_rate_pct=self.min_success_rate_pct,
            max_dependency_blocked_pct=self.max_dependency_blocked_pct,
            max_consecutive_failures=self.max_consecutive_failures,
            max_stale_running_ticks=self.max_stale_running_ticks,
            stale_running_after_seconds=self.stale_running_after_seconds,
            min_applied_chain_valuations=self.min_applied_chain_valuations,
            min_distinct_positions_valued=self.min_distinct_positions_valued,
        ).validate()
        if self.min_closed_positions < 0:
            raise ValueError("min_closed_positions cannot be negative")
        if self.min_distinct_pools < 0:
            raise ValueError("min_distinct_pools cannot be negative")

    def endurance_criteria(self) -> PaperEnduranceCriteria:
        self.validate()
        return PaperEnduranceCriteria(
            min_runtime_hours=self.min_runtime_hours,
            min_terminal_ticks=self.min_terminal_ticks,
            min_success_rate_pct=self.min_success_rate_pct,
            max_dependency_blocked_pct=self.max_dependency_blocked_pct,
            max_consecutive_failures=self.max_consecutive_failures,
            max_stale_running_ticks=self.max_stale_running_ticks,
            stale_running_after_seconds=self.stale_running_after_seconds,
            min_applied_chain_valuations=self.min_applied_chain_valuations,
            min_distinct_positions_valued=self.min_distinct_positions_valued,
        )


@dataclass(frozen=True)
class Phase5PromotionReport:
    account_id: str
    phase3_promoted: bool
    endurance: PaperEnduranceReport
    ledger_audit: PaperLedgerAuditReport
    closed_positions: int
    distinct_valued_pools: int
    criteria: Phase5PromotionCriteria
    promotion_ready: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase5_promotion(
    storage: Storage,
    *,
    account_id: str,
    criteria: Phase5PromotionCriteria = Phase5PromotionCriteria(),
    as_of: str | None = None,
) -> Phase5PromotionReport:
    criteria.validate()

    phase3_promoted = storage.phase_is_promoted(
        PHASE3,
        evidence_type=PHASE3_EVIDENCE_TYPE,
    )
    endurance = build_paper_endurance_report(
        storage,
        account_id=account_id,
        criteria=criteria.endurance_criteria(),
        as_of=as_of,
    )
    audit = audit_paper_ledger(storage, account_id=account_id)

    with storage.connect() as conn:
        closed_positions = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM paper_positions
                WHERE account_id = ? AND status = 'CLOSED'
                """,
                (account_id,),
            ).fetchone()[0]
        )
        distinct_valued_pools = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT p.pool_address)
                FROM paper_chain_valuations v
                JOIN paper_positions p ON p.position_id = v.position_id
                WHERE p.account_id = ? AND v.status = 'APPLIED'
                """,
                (account_id,),
            ).fetchone()[0]
        )

    reasons: list[str] = []
    if not phase3_promoted:
        reasons.append(
            "Phase 3 must be persistently promoted before Phase 5"
        )
    if not endurance.passing:
        reasons.extend(
            f"endurance: {reason}" for reason in endurance.reasons
        )
    if not audit.passing:
        reasons.extend(
            f"ledger audit: {reason}" for reason in audit.reasons
        )
    if closed_positions < criteria.min_closed_positions:
        reasons.append(
            f"closed positions {closed_positions} are below "
            f"{criteria.min_closed_positions}"
        )
    if distinct_valued_pools < criteria.min_distinct_pools:
        reasons.append(
            f"distinct valued pools {distinct_valued_pools} are below "
            f"{criteria.min_distinct_pools}"
        )

    return Phase5PromotionReport(
        account_id=account_id,
        phase3_promoted=phase3_promoted,
        endurance=endurance,
        ledger_audit=audit,
        closed_positions=closed_positions,
        distinct_valued_pools=distinct_valued_pools,
        criteria=criteria,
        promotion_ready=not reasons,
        reasons=tuple(reasons),
    )
