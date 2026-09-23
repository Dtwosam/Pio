from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

from .phase9_policy_authorization import (
    PHASE9_POLICY_GATE_EVIDENCE_TYPE,
    PHASE9_POLICY_GATE_SCOPE,
)
from .phase9_policy_controlled_validation import (
    PHASE9_POLICY_CONTROLLED_VALIDATION_EVIDENCE_TYPE,
    PHASE9_POLICY_CONTROLLED_VALIDATION_SCOPE,
)
from .phase9_policy_rollout_simulation import (
    PHASE9_POLICY_ROLLOUT_SIMULATION_EVIDENCE_TYPE,
    PHASE9_POLICY_ROLLOUT_SIMULATION_SCOPE,
)
from .phase9_policy_rollback_simulation import (
    PHASE9_POLICY_ROLLBACK_SIMULATION_EVIDENCE_TYPE,
    PHASE9_POLICY_ROLLBACK_SIMULATION_SCOPE,
)
from .phase9_policy_prewire import evaluate_phase9_policy_prewire_audit
from .storage import Storage


PHASE9_POLICY_PREWIRE_MANIFEST_EVIDENCE_TYPE = (
    "PHASE9_POLICY_PREWIRE_MANIFEST_V1"
)
PHASE9_POLICY_PREWIRE_MANIFEST_SCOPE = (
    "__PHASE9_POLICY_PREWIRE_MANIFEST__"
)


@dataclass(frozen=True)
class Phase9PolicyEvidenceRef:
    evidence_id: int
    created_at: str
    edge_type: str
    scope: str
    status: str
    qualified: bool
    evidence_sha256: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyPrewireManifestReport:
    research_only: bool
    simulation_only: bool
    policy_actionable: bool
    execution_wired: bool
    prewire_ready: bool
    manifest_ready: bool
    authorization: Phase9PolicyEvidenceRef | None
    controlled_validation: Phase9PolicyEvidenceRef | None
    rollout_simulation: Phase9PolicyEvidenceRef | None
    rollback_simulation: Phase9PolicyEvidenceRef | None
    manifest_sha256: str | None
    prewire: dict[str, Any]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyPrewireManifestAudit:
    exists: bool
    qualified: bool
    boundary_valid: bool
    current_manifest_ready: bool
    persisted_matches_current: bool
    current: bool
    evidence_id: int | None
    manifest_sha256: str | None
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_sha256(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evidence_ref(
    storage: Storage,
    *,
    edge_type: str,
    scope: str,
) -> Phase9PolicyEvidenceRef | None:
    latest = storage.latest_advanced_edge_evidence(
        edge_type=edge_type,
        pool_address=scope,
    )
    if latest is None:
        return None
    evidence = latest.get("evidence")
    if not isinstance(evidence, dict):
        return None
    return Phase9PolicyEvidenceRef(
        evidence_id=int(latest["id"]),
        created_at=str(latest["created_at"]),
        edge_type=str(latest["edge_type"]),
        scope=str(latest["pool_address"]),
        status=str(latest["status"]),
        qualified=bool(latest["qualified"]),
        evidence_sha256=_canonical_sha256(evidence),
    )


def evaluate_phase9_policy_prewire_manifest(
    storage: Storage,
) -> Phase9PolicyPrewireManifestReport:
    prewire = evaluate_phase9_policy_prewire_audit(storage)
    authorization = _evidence_ref(
        storage,
        edge_type=PHASE9_POLICY_GATE_EVIDENCE_TYPE,
        scope=PHASE9_POLICY_GATE_SCOPE,
    )
    controlled = _evidence_ref(
        storage,
        edge_type=PHASE9_POLICY_CONTROLLED_VALIDATION_EVIDENCE_TYPE,
        scope=PHASE9_POLICY_CONTROLLED_VALIDATION_SCOPE,
    )
    rollout = _evidence_ref(
        storage,
        edge_type=PHASE9_POLICY_ROLLOUT_SIMULATION_EVIDENCE_TYPE,
        scope=PHASE9_POLICY_ROLLOUT_SIMULATION_SCOPE,
    )
    rollback = _evidence_ref(
        storage,
        edge_type=PHASE9_POLICY_ROLLBACK_SIMULATION_EVIDENCE_TYPE,
        scope=PHASE9_POLICY_ROLLBACK_SIMULATION_SCOPE,
    )

    reasons: list[str] = []
    if not prewire.ready:
        reasons.extend(
            f"prewire: {reason}"
            for reason in prewire.reasons
        )
    for label, value in (
        ("authorization", authorization),
        ("controlled validation", controlled),
        ("rollout simulation", rollout),
        ("rollback simulation", rollback),
    ):
        if value is None:
            reasons.append(f"latest {label} evidence is missing")

    refs = (authorization, controlled, rollout, rollback)
    manifest_ready = prewire.ready and all(
        value is not None for value in refs
    ) and not reasons

    manifest_sha256 = None
    if all(value is not None for value in refs):
        manifest_sha256 = _canonical_sha256(
            {
                "authorization": authorization.to_record(),
                "controlled_validation": controlled.to_record(),
                "rollout_simulation": rollout.to_record(),
                "rollback_simulation": rollback.to_record(),
            }
        )

    return Phase9PolicyPrewireManifestReport(
        research_only=True,
        simulation_only=True,
        policy_actionable=False,
        execution_wired=False,
        prewire_ready=prewire.ready,
        manifest_ready=manifest_ready,
        authorization=authorization,
        controlled_validation=controlled,
        rollout_simulation=rollout,
        rollback_simulation=rollback,
        manifest_sha256=manifest_sha256,
        prewire=prewire.to_record(),
        reasons=tuple(reasons),
    )


def persist_phase9_policy_prewire_manifest(
    storage: Storage,
    *,
    report: Phase9PolicyPrewireManifestReport,
) -> int:
    if report.policy_actionable:
        raise ValueError(
            "Phase 9 pre-wiring manifest must not grant LIVE policy authority"
        )
    if report.execution_wired:
        raise ValueError(
            "Phase 9 pre-wiring manifest must not be wired to execution"
        )
    if not report.simulation_only:
        raise ValueError(
            "Phase 9 pre-wiring manifest must remain simulation-only"
        )
    if not report.research_only:
        raise ValueError(
            "Phase 9 pre-wiring manifest must remain research-only"
        )
    if not report.manifest_ready:
        raise ValueError(
            "Phase 9 pre-wiring manifest cannot persist before all evidence "
            "is current and ready"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_PREWIRE_MANIFEST_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_PREWIRE_MANIFEST_SCOPE,
        as_of=None,
        status="PREWIRE_MANIFEST_READY",
        qualified=True,
        evidence=report.to_record(),
    )


def audit_persisted_phase9_policy_prewire_manifest(
    storage: Storage,
) -> Phase9PolicyPrewireManifestAudit:
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_PREWIRE_MANIFEST_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_PREWIRE_MANIFEST_SCOPE,
    )
    if latest is None:
        return Phase9PolicyPrewireManifestAudit(
            exists=False,
            qualified=False,
            boundary_valid=False,
            current_manifest_ready=False,
            persisted_matches_current=False,
            current=False,
            evidence_id=None,
            manifest_sha256=None,
            reasons=("persisted Phase 9 pre-wiring manifest is missing",),
        )

    evidence = latest.get("evidence")
    reasons: list[str] = []
    qualified = bool(latest["qualified"])
    boundary_valid = (
        isinstance(evidence, dict)
        and evidence.get("research_only") is True
        and evidence.get("simulation_only") is True
        and evidence.get("policy_actionable") is False
        and evidence.get("execution_wired") is False
    )
    if not boundary_valid:
        reasons.append(
            "persisted pre-wiring manifest violates the non-actionable boundary"
        )
    if not qualified:
        reasons.append("persisted pre-wiring manifest is not qualified")

    current_report = evaluate_phase9_policy_prewire_manifest(storage)
    current_ready = current_report.manifest_ready
    if not current_ready:
        reasons.append(
            "current Phase 9 pre-wiring evidence chain no longer passes"
        )

    persisted_matches_current = False
    if isinstance(evidence, dict):
        persisted_normalized = json.loads(
            json.dumps(evidence, sort_keys=True)
        )
        current_normalized = json.loads(
            json.dumps(current_report.to_record(), sort_keys=True)
        )
        persisted_matches_current = (
            persisted_normalized == current_normalized
        )
    if not persisted_matches_current:
        reasons.append(
            "persisted pre-wiring manifest is stale versus current evidence"
        )

    return Phase9PolicyPrewireManifestAudit(
        exists=True,
        qualified=qualified,
        boundary_valid=boundary_valid,
        current_manifest_ready=current_ready,
        persisted_matches_current=persisted_matches_current,
        current=not reasons,
        evidence_id=int(latest["id"]),
        manifest_sha256=(
            current_report.manifest_sha256
            if current_report.manifest_sha256 is not None
            else (
                str(evidence.get("manifest_sha256"))
                if isinstance(evidence, dict)
                and evidence.get("manifest_sha256") is not None
                else None
            )
        ),
        reasons=tuple(reasons),
    )
