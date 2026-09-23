from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any

from .contextual_bandit import ContextualBanditCriteria
from .contextual_bandit_cycle import (
    CycleContextualBanditResult,
    evaluate_cycle_contextual_bandit,
)
from .phase9_validation import (
    Phase9ResearchBundleCriteria,
    audit_persisted_phase9_promotion,
)
from .phase_promotion import PHASE9, PHASE9_EVIDENCE_TYPE
from .storage import Storage


PHASE9_SHADOW_EVIDENCE_TYPE = "PHASE9_POST_PROMOTION_SHADOW_V1"
PHASE9_SHADOW_SCOPE = "__PHASE9_SHADOW__"


@dataclass(frozen=True)
class Phase9ShadowCriteria:
    warmup_decisions_per_context: int = 2
    exploration_bonus_bps: float = 50.0
    min_decisions: int = 50
    min_pools: int = 3
    min_selected_arms: int = 2
    min_mean_uplift_vs_baseline_bps: float = 0.0
    max_mean_regret_vs_oracle_bps: float = 300.0
    min_post_promotion_seconds: int = 1

    def validate(self) -> None:
        if self.min_post_promotion_seconds < 1:
            raise ValueError(
                "min_post_promotion_seconds must be positive"
            )
        self.bandit_criteria()

    def bandit_criteria(self) -> ContextualBanditCriteria:
        return ContextualBanditCriteria(
            warmup_decisions_per_context=(
                self.warmup_decisions_per_context
            ),
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
        )


@dataclass(frozen=True)
class Phase9ShadowReport:
    research_only: bool
    policy_actionable: bool
    phase9_promotion_exists: bool
    phase9_current: bool
    phase9_promoted_at: str | None
    cycle_id: str
    dataset_version: str | None
    dataset_sha256: str | None
    dataset_cutoff: str | None
    cutoff_after_promotion: bool
    seconds_after_promotion: float | None
    bandit_research_qualified: bool
    shadow_ready: bool
    criteria: Phase9ShadowCriteria
    phase9_audit: dict[str, Any] | None
    cycle_result: dict[str, Any] | None
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("shadow validation timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _persisted_phase9_context(
    storage: Storage,
) -> tuple[str | None, Phase9ResearchBundleCriteria | None, tuple[str, ...]]:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT promoted_at, evidence_type, qualified, evidence_json
            FROM phase_promotion_evidence
            WHERE phase_name = ?
            LIMIT 1
            """,
            (PHASE9,),
        ).fetchone()

    if row is None:
        return (
            None,
            None,
            ("persisted Phase 9 promotion evidence is missing",),
        )

    reasons: list[str] = []
    if str(row[1]) != PHASE9_EVIDENCE_TYPE:
        reasons.append("persisted Phase 9 evidence type is invalid")
    if not bool(row[2]):
        reasons.append("persisted Phase 9 evidence is not qualified")

    try:
        evidence = json.loads(str(row[3]))
    except (TypeError, ValueError, json.JSONDecodeError):
        evidence = None
        reasons.append("persisted Phase 9 evidence JSON is invalid")

    criteria: Phase9ResearchBundleCriteria | None = None
    if isinstance(evidence, dict):
        bundle = evidence.get("research_bundle")
        criteria_raw = (
            bundle.get("criteria")
            if isinstance(bundle, dict)
            else None
        )
        if isinstance(criteria_raw, dict):
            try:
                criteria = Phase9ResearchBundleCriteria(**criteria_raw)
            except (TypeError, ValueError):
                criteria = None
    if criteria is None:
        reasons.append(
            "persisted Phase 9 research-bundle criteria are invalid"
        )

    promoted_at = str(row[0])
    try:
        _parse_time(promoted_at)
    except ValueError:
        reasons.append("persisted Phase 9 promoted_at is invalid")

    return promoted_at, criteria, tuple(reasons)


def evaluate_phase9_shadow(
    storage: Storage,
    *,
    cycle_id: str,
    criteria: Phase9ShadowCriteria = Phase9ShadowCriteria(),
) -> Phase9ShadowReport:
    if not cycle_id.strip():
        raise ValueError("cycle_id is required")
    criteria.validate()

    promoted_at, promotion_criteria, context_reasons = (
        _persisted_phase9_context(storage)
    )
    reasons = list(context_reasons)
    promotion_exists = promoted_at is not None

    phase9_audit = None
    phase9_current = False
    if promotion_criteria is not None:
        audit = audit_persisted_phase9_promotion(
            storage,
            criteria=promotion_criteria,
        )
        phase9_audit = audit.to_record()
        phase9_current = audit.current
        if not audit.current:
            reasons.extend(
                f"Phase 9 currentness: {reason}"
                for reason in audit.reasons
            )

    cycle_result: CycleContextualBanditResult | None = None
    try:
        cycle_result = evaluate_cycle_contextual_bandit(
            storage,
            cycle_id=cycle_id,
            criteria=criteria.bandit_criteria(),
        )
    except (ValueError, FileNotFoundError) as exc:
        reasons.append(f"shadow cycle replay unavailable: {exc}")

    dataset_version = None
    dataset_sha256 = None
    dataset_cutoff = None
    cutoff_after_promotion = False
    seconds_after_promotion = None
    bandit_qualified = False

    if cycle_result is not None:
        dataset_version = cycle_result.lineage.dataset_version
        dataset_sha256 = cycle_result.lineage.dataset_sha256
        dataset_cutoff = cycle_result.lineage.cutoff
        bandit_qualified = cycle_result.report.research_qualified

        if promoted_at is not None:
            try:
                seconds_after_promotion = (
                    _parse_time(dataset_cutoff)
                    - _parse_time(promoted_at)
                ).total_seconds()
                cutoff_after_promotion = (
                    seconds_after_promotion
                    >= criteria.min_post_promotion_seconds
                )
            except ValueError as exc:
                reasons.append(
                    f"shadow cutoff comparison unavailable: {exc}"
                )

        if not cutoff_after_promotion:
            reasons.append(
                "shadow dataset cutoff is not sufficiently after "
                "Phase 9 promotion"
            )
        if not bandit_qualified:
            reasons.extend(
                f"shadow bandit: {reason}"
                for reason in cycle_result.report.reasons
            )

    shadow_ready = (
        promotion_exists
        and phase9_current
        and cycle_result is not None
        and cutoff_after_promotion
        and bandit_qualified
        and not reasons
    )

    return Phase9ShadowReport(
        research_only=True,
        policy_actionable=False,
        phase9_promotion_exists=promotion_exists,
        phase9_current=phase9_current,
        phase9_promoted_at=promoted_at,
        cycle_id=cycle_id,
        dataset_version=dataset_version,
        dataset_sha256=dataset_sha256,
        dataset_cutoff=dataset_cutoff,
        cutoff_after_promotion=cutoff_after_promotion,
        seconds_after_promotion=seconds_after_promotion,
        bandit_research_qualified=bandit_qualified,
        shadow_ready=shadow_ready,
        criteria=criteria,
        phase9_audit=phase9_audit,
        cycle_result=(
            cycle_result.to_record()
            if cycle_result is not None
            else None
        ),
        reasons=tuple(reasons),
    )


def persist_phase9_shadow(
    storage: Storage,
    *,
    report: Phase9ShadowReport,
) -> int:
    if report.policy_actionable:
        raise ValueError(
            "Phase 9 shadow validation must not grant policy authority"
        )
    if not report.research_only:
        raise ValueError(
            "Phase 9 shadow validation must remain research-only"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE9_SHADOW_EVIDENCE_TYPE,
        pool_address=PHASE9_SHADOW_SCOPE,
        as_of=report.dataset_cutoff,
        status=(
            "SHADOW_READY"
            if report.shadow_ready
            else "SHADOW_NOT_READY"
        ),
        qualified=report.shadow_ready,
        evidence=report.to_record(),
    )
