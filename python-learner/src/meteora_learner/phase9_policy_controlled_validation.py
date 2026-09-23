from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any

from .phase9_policy_authorization import (
    PHASE9_POLICY_GATE_EVIDENCE_TYPE,
    PHASE9_POLICY_GATE_SCOPE,
    audit_persisted_phase9_policy_authorization,
)
from .phase9_shadow import (
    Phase9ShadowCriteria,
    evaluate_phase9_shadow,
)
from .storage import Storage


PHASE9_POLICY_CONTROLLED_VALIDATION_EVIDENCE_TYPE = (
    "PHASE9_POLICY_CONTROLLED_VALIDATION_V1"
)
PHASE9_POLICY_CONTROLLED_VALIDATION_SCOPE = (
    "__PHASE9_POLICY_CONTROLLED_VALIDATION__"
)


@dataclass(frozen=True)
class Phase9PolicyControlledValidationCriteria:
    warmup_decisions_per_context: int = 2
    exploration_bonus_bps: float = 50.0
    min_decisions: int = 50
    min_pools: int = 3
    min_selected_arms: int = 2
    min_mean_uplift_vs_baseline_bps: float = 0.0
    max_mean_regret_vs_oracle_bps: float = 250.0
    min_post_authorization_seconds: int = 1

    def validate(self) -> None:
        if self.min_post_authorization_seconds < 1:
            raise ValueError(
                "min_post_authorization_seconds must be positive"
            )
        self.shadow_criteria().validate()

    def shadow_criteria(self) -> Phase9ShadowCriteria:
        return Phase9ShadowCriteria(
            warmup_decisions_per_context=self.warmup_decisions_per_context,
            exploration_bonus_bps=self.exploration_bonus_bps,
            min_decisions=self.min_decisions,
            min_pools=self.min_pools,
            min_selected_arms=self.min_selected_arms,
            min_mean_uplift_vs_baseline_bps=(
                self.min_mean_uplift_vs_baseline_bps
            ),
            max_mean_regret_vs_oracle_bps=(
                self.max_mean_regret_vs_oracle_bps
            ),
            min_post_promotion_seconds=1,
        )


@dataclass(frozen=True)
class Phase9PolicyControlledValidationReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    simulation_only: bool
    authorization_current: bool
    authorization_evidence_id: int | None
    authorization_created_at: str | None
    cycle_id: str
    dataset_version: str | None
    dataset_sha256: str | None
    dataset_cutoff: str | None
    cycle_independent: bool
    dataset_independent: bool
    cutoff_after_authorization: bool
    seconds_after_authorization: float | None
    shadow_ready: bool
    controlled_validation_ready: bool
    criteria: Phase9PolicyControlledValidationCriteria
    authorization_audit: dict[str, Any] | None
    shadow_report: dict[str, Any] | None
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PolicyControlledValidationAudit:
    exists: bool
    qualified: bool
    boundary_valid: bool
    criteria_valid: bool
    current_validation_ready: bool
    persisted_matches_current: bool
    current: bool
    evidence_id: int | None
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(
            "controlled validation timestamps must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def _authorization_lineage(
    evidence: dict[str, Any],
) -> tuple[set[str], set[str]]:
    qualifying_ids: set[int] = set()
    raw_ids = evidence.get("qualifying_evidence_ids", ())
    if isinstance(raw_ids, (list, tuple)):
        for value in raw_ids:
            try:
                qualifying_ids.add(int(value))
            except (TypeError, ValueError):
                continue

    cycles: set[str] = set()
    dataset_hashes: set[str] = set()
    shadow_evidence = evidence.get("shadow_evidence")
    if not isinstance(shadow_evidence, list):
        return cycles, dataset_hashes

    for item in shadow_evidence:
        if not isinstance(item, dict):
            continue
        try:
            evidence_id = int(item.get("evidence_id"))
        except (TypeError, ValueError):
            continue
        if evidence_id not in qualifying_ids:
            continue
        cycle_id = str(item.get("cycle_id", "")).strip()
        dataset_sha = str(item.get("dataset_sha256", "")).strip()
        if cycle_id:
            cycles.add(cycle_id)
        if dataset_sha:
            dataset_hashes.add(dataset_sha)
    return cycles, dataset_hashes


def evaluate_phase9_policy_controlled_validation(
    storage: Storage,
    *,
    cycle_id: str,
    criteria: Phase9PolicyControlledValidationCriteria = (
        Phase9PolicyControlledValidationCriteria()
    ),
) -> Phase9PolicyControlledValidationReport:
    if not cycle_id.strip():
        raise ValueError("cycle_id is required")
    criteria.validate()

    reasons: list[str] = []
    authorization = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_GATE_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_GATE_SCOPE,
    )
    authorization_audit = (
        audit_persisted_phase9_policy_authorization(storage)
    )
    authorization_current = authorization_audit.current
    authorization_evidence_id = (
        int(authorization["id"])
        if authorization is not None
        else None
    )
    authorization_created_at = (
        str(authorization["created_at"])
        if authorization is not None
        else None
    )
    if not authorization_current:
        reasons.extend(
            f"authorization currentness: {reason}"
            for reason in authorization_audit.reasons
        )

    authorized_cycles: set[str] = set()
    authorized_hashes: set[str] = set()
    if authorization is not None:
        evidence = authorization.get("evidence")
        if isinstance(evidence, dict):
            authorized_cycles, authorized_hashes = (
                _authorization_lineage(evidence)
            )

    shadow = None
    try:
        shadow = evaluate_phase9_shadow(
            storage,
            cycle_id=cycle_id,
            criteria=criteria.shadow_criteria(),
        )
    except (ValueError, FileNotFoundError) as exc:
        reasons.append(
            f"controlled holdout replay unavailable: {exc}"
        )

    dataset_version = None
    dataset_sha256 = None
    dataset_cutoff = None
    cycle_independent = False
    dataset_independent = False
    cutoff_after_authorization = False
    seconds_after_authorization = None
    shadow_ready = False

    if shadow is not None:
        dataset_version = shadow.dataset_version
        dataset_sha256 = shadow.dataset_sha256
        dataset_cutoff = shadow.dataset_cutoff
        shadow_ready = shadow.shadow_ready
        cycle_independent = cycle_id not in authorized_cycles
        dataset_independent = (
            bool(dataset_sha256)
            and dataset_sha256 not in authorized_hashes
        )

        if not cycle_independent:
            reasons.append(
                "controlled validation cycle was already used by "
                "authorization evidence"
            )
        if not dataset_independent:
            reasons.append(
                "controlled validation dataset was already used by "
                "authorization evidence"
            )
        if not shadow_ready:
            reasons.extend(
                f"controlled shadow: {reason}"
                for reason in shadow.reasons
            )

        if (
            authorization_created_at is not None
            and dataset_cutoff is not None
        ):
            try:
                seconds_after_authorization = (
                    _parse_time(dataset_cutoff)
                    - _parse_time(authorization_created_at)
                ).total_seconds()
                cutoff_after_authorization = (
                    seconds_after_authorization
                    >= criteria.min_post_authorization_seconds
                )
            except ValueError as exc:
                reasons.append(
                    f"authorization cutoff comparison unavailable: {exc}"
                )
        if not cutoff_after_authorization:
            reasons.append(
                "controlled validation dataset cutoff is not sufficiently "
                "after authorization evidence creation"
            )

    controlled_validation_ready = (
        authorization_current
        and shadow is not None
        and shadow_ready
        and cycle_independent
        and dataset_independent
        and cutoff_after_authorization
        and not reasons
    )

    return Phase9PolicyControlledValidationReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        simulation_only=True,
        authorization_current=authorization_current,
        authorization_evidence_id=authorization_evidence_id,
        authorization_created_at=authorization_created_at,
        cycle_id=cycle_id,
        dataset_version=dataset_version,
        dataset_sha256=dataset_sha256,
        dataset_cutoff=dataset_cutoff,
        cycle_independent=cycle_independent,
        dataset_independent=dataset_independent,
        cutoff_after_authorization=cutoff_after_authorization,
        seconds_after_authorization=seconds_after_authorization,
        shadow_ready=shadow_ready,
        controlled_validation_ready=controlled_validation_ready,
        criteria=criteria,
        authorization_audit=authorization_audit.to_record(),
        shadow_report=(
            shadow.to_record()
            if shadow is not None
            else None
        ),
        reasons=tuple(reasons),
    )


def persist_phase9_policy_controlled_validation(
    storage: Storage,
    *,
    report: Phase9PolicyControlledValidationReport,
) -> int:
    if report.policy_actionable:
        raise ValueError(
            "controlled Phase 9 validation must not grant LIVE policy "
            "authority"
        )
    if report.execution_wired:
        raise ValueError(
            "controlled Phase 9 validation must not be wired to execution"
        )
    if not report.simulation_only:
        raise ValueError(
            "controlled Phase 9 validation must remain simulation-only"
        )
    if not report.research_only:
        raise ValueError(
            "controlled Phase 9 validation must remain research-only"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_CONTROLLED_VALIDATION_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_CONTROLLED_VALIDATION_SCOPE,
        as_of=report.dataset_cutoff,
        status=(
            "CONTROLLED_VALIDATION_READY"
            if report.controlled_validation_ready
            else "CONTROLLED_VALIDATION_NOT_READY"
        ),
        qualified=report.controlled_validation_ready,
        evidence=report.to_record(),
    )


def audit_persisted_phase9_policy_controlled_validation(
    storage: Storage,
) -> Phase9PolicyControlledValidationAudit:
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_CONTROLLED_VALIDATION_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_CONTROLLED_VALIDATION_SCOPE,
    )
    if latest is None:
        return Phase9PolicyControlledValidationAudit(
            exists=False,
            qualified=False,
            boundary_valid=False,
            criteria_valid=False,
            current_validation_ready=False,
            persisted_matches_current=False,
            current=False,
            evidence_id=None,
            reasons=(
                "persisted Phase 9 controlled validation evidence is missing",
            ),
        )

    evidence = latest["evidence"]
    reasons: list[str] = []
    qualified = bool(latest["qualified"])
    boundary_valid = (
        isinstance(evidence, dict)
        and evidence.get("research_only") is True
        and evidence.get("policy_actionable") is False
        and evidence.get("execution_wired") is False
        and evidence.get("simulation_only") is True
    )
    if not boundary_valid:
        reasons.append(
            "persisted controlled validation evidence violates the "
            "simulation-only boundary"
        )
    if not qualified:
        reasons.append(
            "latest persisted controlled validation evidence is not qualified"
        )

    criteria = None
    cycle_id = ""
    if isinstance(evidence, dict):
        criteria_raw = evidence.get("criteria")
        cycle_id = str(evidence.get("cycle_id", "")).strip()
        if isinstance(criteria_raw, dict):
            try:
                criteria = Phase9PolicyControlledValidationCriteria(
                    **criteria_raw
                )
                criteria.validate()
            except (TypeError, ValueError):
                criteria = None
    criteria_valid = criteria is not None and bool(cycle_id)
    if not criteria_valid:
        reasons.append(
            "persisted controlled validation criteria or cycle are invalid"
        )

    current_report = (
        evaluate_phase9_policy_controlled_validation(
            storage,
            cycle_id=cycle_id,
            criteria=criteria,
        )
        if criteria is not None and cycle_id
        else None
    )
    current_ready = bool(
        current_report is not None
        and current_report.controlled_validation_ready
    )
    if not current_ready:
        reasons.append(
            "current Phase 9 controlled validation no longer passes"
        )

    persisted_matches_current = False
    if isinstance(evidence, dict) and current_report is not None:
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
            "persisted controlled validation evidence is stale versus "
            "current replay"
        )

    return Phase9PolicyControlledValidationAudit(
        exists=True,
        qualified=qualified,
        boundary_valid=boundary_valid,
        criteria_valid=criteria_valid,
        current_validation_ready=current_ready,
        persisted_matches_current=persisted_matches_current,
        current=not reasons,
        evidence_id=int(latest["id"]),
        reasons=tuple(reasons),
    )
