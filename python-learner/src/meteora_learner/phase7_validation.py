from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .live_execution_audit import (
    LiveExecutionLedgerAudit,
    audit_live_execution_ledger,
)
from .phase_promotion import PHASE6, PHASE6_EVIDENCE_TYPE
from .storage import Storage


@dataclass(frozen=True)
class Phase7PromotionCriteria:
    min_closed_positions: int = 3
    min_distinct_pools: int = 2
    min_confirmed_receipts: int = 6
    max_failed_receipts: int = 0
    max_open_positions_at_validation: int = 0

    def validate(self) -> None:
        if self.min_closed_positions < 1:
            raise ValueError("min_closed_positions must be positive")
        if self.min_distinct_pools < 1:
            raise ValueError("min_distinct_pools must be positive")
        if self.min_confirmed_receipts < 1:
            raise ValueError("min_confirmed_receipts must be positive")
        if self.max_failed_receipts < 0:
            raise ValueError("max_failed_receipts cannot be negative")
        if self.max_open_positions_at_validation < 0:
            raise ValueError(
                "max_open_positions_at_validation cannot be negative"
            )


@dataclass(frozen=True)
class Phase7PromotionReport:
    phase6_promoted: bool
    ledger_audit: LiveExecutionLedgerAudit
    confirmed_receipts: int
    failed_receipts: int
    closed_positions: int
    open_positions: int
    distinct_closed_pools: int
    valued_closed_positions: int
    labeled_closed_positions: int
    criteria: Phase7PromotionCriteria
    promotion_ready: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase7_promotion(
    storage: Storage,
    *,
    criteria: Phase7PromotionCriteria = Phase7PromotionCriteria(),
) -> Phase7PromotionReport:
    criteria.validate()

    phase6_promoted = storage.phase_is_promoted(
        PHASE6,
        evidence_type=PHASE6_EVIDENCE_TYPE,
    )
    audit = audit_live_execution_ledger(storage)

    with storage.connect() as conn:
        confirmed_receipts = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM live_execution_receipts
                WHERE intent_status = 'CONFIRMED' AND succeeded = 1
                """
            ).fetchone()[0]
        )
        failed_receipts = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM live_execution_receipts
                WHERE intent_status = 'FAILED' OR succeeded = 0
                """
            ).fetchone()[0]
        )
        position_counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END),
                SUM(
                    CASE
                        WHEN status IN ('OPEN', 'LIQUIDITY_REMOVED')
                        THEN 1 ELSE 0
                    END
                )
            FROM live_positions
            """
        ).fetchone()
        closed_positions = int(position_counts[0] or 0)
        open_positions = int(position_counts[1] or 0)
        distinct_closed_pools = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT pool_address)
                FROM live_positions
                WHERE status = 'CLOSED'
                """
            ).fetchone()[0]
        )
        valued_closed_positions = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM live_positions p
                JOIN live_position_outcomes o
                  ON o.position_address = p.position_address
                JOIN live_position_valuations v
                  ON v.position_address = p.position_address
                WHERE p.status = 'CLOSED'
                  AND o.label_status = 'VALUED'
                """
            ).fetchone()[0]
        )
        labeled_closed_positions = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM live_positions p
                JOIN live_learning_labels l
                  ON l.position_address = p.position_address
                WHERE p.status = 'CLOSED'
                """
            ).fetchone()[0]
        )

    reasons: list[str] = []
    if not phase6_promoted:
        reasons.append(
            "Phase 6 must be persistently promoted before Phase 7"
        )
    if not audit.clean:
        reasons.append("live execution ledger audit is not clean")
    if closed_positions < criteria.min_closed_positions:
        reasons.append(
            f"closed live positions {closed_positions} are below "
            f"{criteria.min_closed_positions}"
        )
    if distinct_closed_pools < criteria.min_distinct_pools:
        reasons.append(
            f"distinct closed pools {distinct_closed_pools} are below "
            f"{criteria.min_distinct_pools}"
        )
    if confirmed_receipts < criteria.min_confirmed_receipts:
        reasons.append(
            f"confirmed successful receipts {confirmed_receipts} are below "
            f"{criteria.min_confirmed_receipts}"
        )
    if failed_receipts > criteria.max_failed_receipts:
        reasons.append(
            f"failed live receipts {failed_receipts} exceed "
            f"{criteria.max_failed_receipts}"
        )
    if open_positions > criteria.max_open_positions_at_validation:
        reasons.append(
            f"open/unsettled live positions {open_positions} exceed "
            f"{criteria.max_open_positions_at_validation}"
        )
    if valued_closed_positions != closed_positions:
        reasons.append(
            f"valued closed positions {valued_closed_positions} do not "
            f"cover all {closed_positions} closed positions"
        )
    if labeled_closed_positions != closed_positions:
        reasons.append(
            f"learning-labeled closed positions {labeled_closed_positions} "
            f"do not cover all {closed_positions} closed positions"
        )

    return Phase7PromotionReport(
        phase6_promoted=phase6_promoted,
        ledger_audit=audit,
        confirmed_receipts=confirmed_receipts,
        failed_receipts=failed_receipts,
        closed_positions=closed_positions,
        open_positions=open_positions,
        distinct_closed_pools=distinct_closed_pools,
        valued_closed_positions=valued_closed_positions,
        labeled_closed_positions=labeled_closed_positions,
        criteria=criteria,
        promotion_ready=not reasons,
        reasons=tuple(reasons),
    )
