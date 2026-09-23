from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .phase9_policy_authorization import (
    audit_persisted_phase9_policy_authorization,
)
from .phase9_policy_controlled_validation import (
    audit_persisted_phase9_policy_controlled_validation,
)
from .storage import Storage


@dataclass(frozen=True)
class Phase9PolicyReadinessReport:
    research_only: bool
    simulation_only: bool
    policy_actionable: bool
    execution_wired: bool
    authorization_current: bool
    controlled_validation_current: bool
    ready: bool
    authorization_audit: dict[str, Any]
    controlled_validation_audit: dict[str, Any]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase9_policy_readiness(
    storage: Storage,
) -> Phase9PolicyReadinessReport:
    authorization = audit_persisted_phase9_policy_authorization(
        storage,
    )
    controlled = (
        audit_persisted_phase9_policy_controlled_validation(
            storage,
        )
    )

    reasons: list[str] = []
    if not authorization.current:
        reasons.extend(
            f"authorization: {reason}"
            for reason in authorization.reasons
        )
    if not controlled.current:
        reasons.extend(
            f"controlled validation: {reason}"
            for reason in controlled.reasons
        )

    ready = authorization.current and controlled.current and not reasons

    return Phase9PolicyReadinessReport(
        research_only=True,
        simulation_only=True,
        policy_actionable=False,
        execution_wired=False,
        authorization_current=authorization.current,
        controlled_validation_current=controlled.current,
        ready=ready,
        authorization_audit=authorization.to_record(),
        controlled_validation_audit=controlled.to_record(),
        reasons=tuple(reasons),
    )
