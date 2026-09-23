from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .phase9_replay_audit import evaluate_phase9_replay_audit
from .phase9_storage_integrity import evaluate_phase9_storage_integrity
from .phase9_source_freshness import (
    evaluate_phase9_source_freshness,
)
from .phase9_validation import (
    Phase9ResearchBundleCriteria,
    audit_persisted_phase9_promotion,
    evaluate_phase9_promotion,
)
from .storage import Storage


@dataclass(frozen=True)
class Phase9OperationalAuditReport:
    research_only: bool
    policy_actionable: bool
    storage_integrity_verified: bool
    replay_verified: bool
    promotion_current: bool
    source_freshness_current: bool
    verified: bool
    storage_integrity: dict[str, Any]
    replay_audit: dict[str, Any]
    promotion_currentness: dict[str, Any]
    source_freshness: dict[str, Any]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase9_operational_audit(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
) -> Phase9OperationalAuditReport:
    storage_integrity = evaluate_phase9_storage_integrity(storage)
    replay = evaluate_phase9_replay_audit(
        storage,
        criteria=criteria,
    )
    promotion_report = evaluate_phase9_promotion(
        storage,
        criteria=criteria,
    )
    promotion = audit_persisted_phase9_promotion(
        storage,
        criteria=criteria,
        current_report=promotion_report,
    )

    source_freshness = evaluate_phase9_source_freshness(storage)

    reasons = tuple(
        [
            *(
                f"storage integrity: {reason}"
                for reason in storage_integrity.reasons
            ),
            *(
                f"replay audit: {reason}"
                for reason in replay.reasons
            ),
            *(
                f"promotion currentness: {reason}"
                for reason in promotion.reasons
            ),
        ]
    )
    verified = (
        storage_integrity.verified
        and replay.verified
        and promotion.current
    )

    return Phase9OperationalAuditReport(
        research_only=True,
        policy_actionable=False,
        storage_integrity_verified=storage_integrity.verified,
        replay_verified=replay.verified,
        promotion_current=promotion.current,
        source_freshness_current=source_freshness.current,
        verified=verified,
        storage_integrity=storage_integrity.to_record(),
        replay_audit=replay.to_record(),
        promotion_currentness=promotion.to_record(),
        source_freshness=source_freshness.to_record(),
        reasons=reasons,
    )
