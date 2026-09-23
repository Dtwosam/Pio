from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .continuous_learning import (
    ContinuousLearningCriteria,
    build_continuous_learning_plan,
)
from .phase8_validation import (
    Phase8PromotionCriteria,
    audit_persisted_phase8_promotion,
    evaluate_phase8_promotion,
)
from .retraining_cycle import active_retraining_cycle
from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class Phase8EvidenceStatus:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    as_of: str
    phase7_promoted: bool
    champion_model_id: str | None
    champion_dataset_version: str | None
    champion_age_days: float | None
    active_challenger_model_ids: tuple[str, ...]
    retrain_status: str
    retrain_due: bool
    new_chain_observations: int
    required_new_chain_observations: int
    new_chain_pools: int
    required_new_chain_pools: int
    new_live_labels_since_champion: int
    retrain_live_label_trigger: int
    champion_age_trigger_days: float
    active_cycle_id: str | None
    active_cycle_status: str | None
    active_cycle_challenger_model_id: str | None
    active_cycle_challenger_status: str | None
    completed_cycles: int
    champion_cycle_id: str | None
    continuous_promotion_evidence_id: int | None
    live_champion_status: str | None
    live_label_count: int
    required_live_labels: int
    live_distinct_pools: int
    required_live_pools: int
    live_rollback_recommended: bool
    promotion_ready: bool
    persisted_phase8_current: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_phase8_evidence_status(
    storage: Storage,
    *,
    learning_criteria: ContinuousLearningCriteria = (
        ContinuousLearningCriteria()
    ),
    promotion_criteria: Phase8PromotionCriteria = (
        Phase8PromotionCriteria()
    ),
    as_of: str | None = None,
) -> Phase8EvidenceStatus:
    evaluation_time = as_of or utc_now_iso()
    learning = build_continuous_learning_plan(
        storage,
        criteria=learning_criteria,
        as_of=evaluation_time,
    )
    promotion = evaluate_phase8_promotion(
        storage,
        criteria=promotion_criteria,
    )
    persisted = audit_persisted_phase8_promotion(storage)
    cycle = active_retraining_cycle(storage)
    cycle_challenger_status = None
    if (
        cycle is not None
        and cycle.challenger_model_id is not None
    ):
        challenger = storage.model_registry_entry(
            cycle.challenger_model_id
        )
        cycle_challenger_status = (
            str(challenger["status"])
            if challenger is not None
            else "MISSING"
        )

    live = promotion.live_champion
    live_status = live.status if live is not None else None
    live_labels = live.label_count if live is not None else 0
    live_pools = live.distinct_pools if live is not None else 0
    rollback = (
        live.rollback_recommended if live is not None else False
    )

    reasons = tuple(dict.fromkeys(
        tuple(learning.reasons)
        + tuple(promotion.reasons)
        + (() if persisted.current else tuple(persisted.reasons))
    ))

    return Phase8EvidenceStatus(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of=evaluation_time,
        phase7_promoted=learning.phase7_promoted,
        champion_model_id=learning.champion_model_id,
        champion_dataset_version=learning.champion_dataset_version,
        champion_age_days=learning.champion_age_days,
        active_challenger_model_ids=(
            learning.active_challenger_model_ids
        ),
        retrain_status=learning.status,
        retrain_due=learning.retrain_due,
        new_chain_observations=learning.new_chain_observations,
        required_new_chain_observations=(
            learning_criteria.min_new_chain_observations
        ),
        new_chain_pools=learning.new_chain_pools,
        required_new_chain_pools=(
            learning_criteria.min_new_chain_pools
        ),
        new_live_labels_since_champion=learning.new_live_labels,
        retrain_live_label_trigger=(
            learning_criteria.min_new_live_labels
        ),
        champion_age_trigger_days=(
            learning_criteria.max_champion_age_days
        ),
        active_cycle_id=(
            cycle.cycle_id if cycle is not None else None
        ),
        active_cycle_status=(
            cycle.status if cycle is not None else None
        ),
        active_cycle_challenger_model_id=(
            cycle.challenger_model_id
            if cycle is not None
            else None
        ),
        active_cycle_challenger_status=cycle_challenger_status,
        completed_cycles=promotion.completed_cycles,
        champion_cycle_id=promotion.champion_cycle_id,
        continuous_promotion_evidence_id=(
            promotion.continuous_promotion_evidence_id
        ),
        live_champion_status=live_status,
        live_label_count=live_labels,
        required_live_labels=promotion_criteria.min_live_labels,
        live_distinct_pools=live_pools,
        required_live_pools=promotion_criteria.min_live_pools,
        live_rollback_recommended=rollback,
        promotion_ready=promotion.promotion_ready,
        persisted_phase8_current=persisted.current,
        reasons=reasons,
    )
