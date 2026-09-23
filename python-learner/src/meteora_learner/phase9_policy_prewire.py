from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .phase9_policy_readiness import evaluate_phase9_policy_readiness
from .phase9_policy_rollout_simulation import (
    audit_persisted_phase9_policy_rollout_simulation,
)
from .phase9_policy_rollback_simulation import (
    audit_persisted_phase9_policy_rollback_simulation,
)
from .phase9_storage_integrity import evaluate_phase9_storage_integrity
from .storage import Storage


@dataclass(frozen=True)
class Phase9PolicyPrewireAudit:
    research_only: bool
    simulation_only: bool
    policy_actionable: bool
    execution_wired: bool
    storage_integrity_verified: bool
    policy_readiness_ready: bool
    rollout_simulation_current: bool
    rollback_simulation_current: bool
    rollback_clear: bool
    ready: bool
    storage_integrity: dict[str, Any]
    policy_readiness: dict[str, Any]
    rollout_audit: dict[str, Any]
    rollback_audit: dict[str, Any]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase9_policy_prewire_audit(
    storage: Storage,
) -> Phase9PolicyPrewireAudit:
    storage_integrity = evaluate_phase9_storage_integrity(storage)
    readiness = evaluate_phase9_policy_readiness(storage)
    rollout = audit_persisted_phase9_policy_rollout_simulation(storage)
    rollback = audit_persisted_phase9_policy_rollback_simulation(storage)

    reasons: list[str] = []
    if not storage_integrity.verified:
        reasons.extend(
            f"storage integrity: {reason}"
            for reason in storage_integrity.reasons
        )
    if not readiness.ready:
        reasons.extend(
            f"policy readiness: {reason}"
            for reason in readiness.reasons
        )
    if not rollout.current:
        reasons.extend(
            f"rollout simulation: {reason}"
            for reason in rollout.reasons
        )
    if not rollback.current:
        reasons.extend(
            f"rollback simulation: {reason}"
            for reason in rollback.reasons
        )

    rollback_clear = (
        rollback.current
        and rollback.rollback_required is False
        and rollback.status == "NO_ROLLBACK_TRIGGER"
    )
    if rollback.current and not rollback_clear:
        reasons.append(
            "rollback simulation is current but has not resolved to "
            "NO_ROLLBACK_TRIGGER"
        )

    ready = (
        storage_integrity.verified
        and readiness.ready
        and rollout.current
        and rollback.current
        and rollback_clear
        and not reasons
    )

    return Phase9PolicyPrewireAudit(
        research_only=True,
        simulation_only=True,
        policy_actionable=False,
        execution_wired=False,
        storage_integrity_verified=storage_integrity.verified,
        policy_readiness_ready=readiness.ready,
        rollout_simulation_current=rollout.current,
        rollback_simulation_current=rollback.current,
        rollback_clear=rollback_clear,
        ready=ready,
        storage_integrity=storage_integrity.to_record(),
        policy_readiness=readiness.to_record(),
        rollout_audit=rollout.to_record(),
        rollback_audit=rollback.to_record(),
        reasons=tuple(reasons),
    )
