from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .phase9_policy_authorization import (
    audit_persisted_phase9_policy_authorization,
)
from .phase9_policy_controlled_validation import (
    audit_persisted_phase9_policy_controlled_validation,
)
from .phase9_policy_rollout_simulation import (
    audit_persisted_phase9_policy_rollout_simulation,
)
from .phase9_policy_rollback_simulation import (
    audit_persisted_phase9_policy_rollback_simulation,
)
from .phase9_policy_prewire import evaluate_phase9_policy_prewire_audit
from .phase9_policy_manifest import (
    audit_persisted_phase9_policy_prewire_manifest,
)
from .storage import Storage


@dataclass(frozen=True)
class Phase9PolicyStatus:
    research_only: bool
    simulation_only: bool
    policy_actionable: bool
    execution_wired: bool
    authorization_current: bool
    controlled_validation_current: bool
    rollout_simulation_current: bool
    rollback_simulation_current: bool
    rollback_required: bool | None
    rollback_status: str | None
    prewire_ready: bool
    prewire_manifest_current: bool
    prewire_manifest_sha256: str | None
    authorization: dict[str, Any]
    controlled_validation: dict[str, Any]
    rollout_simulation: dict[str, Any]
    rollback_simulation: dict[str, Any]
    prewire: dict[str, Any]
    prewire_manifest: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase9_policy_status(
    storage: Storage,
) -> Phase9PolicyStatus:
    authorization = audit_persisted_phase9_policy_authorization(storage)
    controlled = audit_persisted_phase9_policy_controlled_validation(
        storage,
    )
    rollout = audit_persisted_phase9_policy_rollout_simulation(storage)
    rollback = audit_persisted_phase9_policy_rollback_simulation(storage)
    prewire = evaluate_phase9_policy_prewire_audit(storage)
    manifest = audit_persisted_phase9_policy_prewire_manifest(storage)

    return Phase9PolicyStatus(
        research_only=True,
        simulation_only=True,
        policy_actionable=False,
        execution_wired=False,
        authorization_current=authorization.current,
        controlled_validation_current=controlled.current,
        rollout_simulation_current=rollout.current,
        rollback_simulation_current=rollback.current,
        rollback_required=rollback.rollback_required,
        rollback_status=rollback.status,
        prewire_ready=prewire.ready,
        prewire_manifest_current=manifest.current,
        prewire_manifest_sha256=manifest.manifest_sha256,
        authorization=authorization.to_record(),
        controlled_validation=controlled.to_record(),
        rollout_simulation=rollout.to_record(),
        rollback_simulation=rollback.to_record(),
        prewire=prewire.to_record(),
        prewire_manifest=manifest.to_record(),
    )
