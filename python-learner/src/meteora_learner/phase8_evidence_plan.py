from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .continuous_learning import ContinuousLearningCriteria
from .phase8_evidence_status import (
    Phase8EvidenceStatus,
    evaluate_phase8_evidence_status,
)
from .phase8_validation import Phase8PromotionCriteria
from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class Phase8EvidenceDebtItem:
    priority: int
    debt_type: str
    scope: str
    blocking: bool
    operator_required: bool
    shell_command: str | None
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase8EvidencePlan:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    as_of: str
    promotion_ready: bool
    persisted_phase8_current: bool
    next_action: Phase8EvidenceDebtItem | None
    items: tuple[Phase8EvidenceDebtItem, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _item(
    priority: int,
    debt_type: str,
    scope: str,
    reason: str,
    *,
    operator_required: bool = True,
    shell_command: str | None = None,
) -> Phase8EvidenceDebtItem:
    return Phase8EvidenceDebtItem(
        priority=priority,
        debt_type=debt_type,
        scope=scope,
        blocking=True,
        operator_required=operator_required,
        shell_command=shell_command,
        reason=reason,
    )


def build_phase8_evidence_plan(
    storage: Storage,
    *,
    learning_criteria: ContinuousLearningCriteria = (
        ContinuousLearningCriteria()
    ),
    promotion_criteria: Phase8PromotionCriteria = (
        Phase8PromotionCriteria()
    ),
    as_of: str | None = None,
) -> Phase8EvidencePlan:
    evaluation_time = as_of or utc_now_iso()
    status = evaluate_phase8_evidence_status(
        storage,
        learning_criteria=learning_criteria,
        promotion_criteria=promotion_criteria,
        as_of=evaluation_time,
    )

    if status.persisted_phase8_current:
        return Phase8EvidencePlan(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            as_of=evaluation_time,
            promotion_ready=status.promotion_ready,
            persisted_phase8_current=True,
            next_action=None,
            items=(),
            reasons=(),
        )

    items: list[Phase8EvidenceDebtItem] = []

    if not status.phase7_promoted:
        items.append(
            _item(
                5,
                "PHASE7_DEPENDENCY",
                "PHASE7_PROMOTION",
                (
                    "Phase 7 must be persistently promoted before "
                    "continuous retraining or Phase 8 promotion can proceed"
                ),
                shell_command="pio phase7-validate --require-ready",
            )
        )

    if status.champion_model_id is None:
        items.append(
            _item(
                10,
                "INITIAL_CHAMPION_REQUIRED",
                "MODEL_REGISTRY",
                (
                    "Phase 8 continuous learning requires an existing "
                    "CHAMPION produced through the staged offline/PAPER "
                    "challenger workflow; model identity and artifacts must "
                    "be chosen explicitly"
                ),
            )
        )

    if status.live_rollback_recommended:
        items.append(
            _item(
                12,
                "LIVE_CHAMPION_SAFETY_BREACH",
                status.champion_model_id or "CHAMPION",
                (
                    "current live champion evidence recommends rollback. "
                    "Rollback is intentionally manual and requires persisted "
                    "BREACH evidence review"
                ),
                shell_command=(
                    "pio ml-live-monitor --model-id "
                    + str(status.champion_model_id)
                    + " --persist"
                    if status.champion_model_id
                    else None
                ),
            )
        )

    if status.active_cycle_id is not None:
        cycle_scope = status.active_cycle_id
        cycle_status = status.active_cycle_status or "UNKNOWN"
        challenger = status.active_cycle_challenger_model_id
        challenger_status = status.active_cycle_challenger_status

        if cycle_status == "PLANNED" and challenger is None:
            items.append(
                _item(
                    20,
                    "RETRAIN_TRAINING_INPUTS_REQUIRED",
                    cycle_scope,
                    (
                        "active retraining cycle is PLANNED without a "
                        "challenger. Training requires the exact cycle-bound "
                        "dataset file plus an explicit model_id and artifact "
                        "directory"
                    ),
                )
            )
        elif (
            cycle_status == "CHALLENGER_REGISTERED"
            or challenger_status == "OFFLINE_CANDIDATE"
        ):
            items.append(
                _item(
                    25,
                    "RETRAIN_WALK_FORWARD_REQUIRED",
                    challenger or cycle_scope,
                    (
                        "cycle challenger is registered but still offline. "
                        "Run cycle-bound walk-forward validation against the "
                        "exact checksum-matching dataset before any PAPER "
                        "transition"
                    ),
                )
            )
        elif (
            cycle_status == "OFFLINE_QUALIFIED"
            or challenger_status == "OFFLINE_QUALIFIED"
        ):
            items.append(
                _item(
                    30,
                    "PAPER_CHALLENGER_START_REQUIRED",
                    challenger or cycle_scope,
                    (
                        "offline-qualified cycle challenger must be moved to "
                        "PAPER_CHALLENGER before continuous promotion "
                        "validation"
                    ),
                    shell_command=(
                        "pio ml-start-paper --model-id " + challenger
                        if challenger
                        else None
                    ),
                )
            )
        elif (
            cycle_status == "PAPER_CHALLENGER"
            or challenger_status == "PAPER_CHALLENGER"
        ):
            items.append(
                _item(
                    35,
                    "PAPER_CHALLENGER_EVIDENCE_REQUIRED",
                    challenger or cycle_scope,
                    (
                        "PAPER challenger is active. Real challenger and "
                        "incumbent closed-trade evidence plus an explicit "
                        "PAPER account are required before "
                        "ml-continuous-validate can qualify or promote it"
                    ),
                )
            )
        else:
            items.append(
                _item(
                    38,
                    "ACTIVE_CYCLE_REVIEW_REQUIRED",
                    cycle_scope,
                    (
                        "active retraining cycle is in an unhandled or "
                        "inconsistent state: cycle_status="
                        f"{cycle_status}, challenger_status="
                        f"{challenger_status}"
                    ),
                    shell_command=(
                        "pio ml-retrain-status --cycle-id "
                        + cycle_scope
                    ),
                )
            )
    elif (
        status.champion_model_id is not None
        and not status.promotion_ready
    ):
        if status.retrain_due:
            items.append(
                _item(
                    40,
                    "RETRAIN_DATASET_INPUTS_REQUIRED",
                    status.champion_model_id,
                    (
                        "continuous retraining is due. Starting the cycle "
                        "requires explicit per-pool amount_x, amount_y and "
                        "network_cost_y_atomic inputs plus an output dataset "
                        "path; these values must not be invented"
                    ),
                    shell_command=(
                        "pio ml-retrain-plan --persist --require-due"
                    ),
                )
            )
        elif status.retrain_status == "WAITING_CHAIN_EVIDENCE":
            chain_remaining = max(
                0,
                status.required_new_chain_observations
                - status.new_chain_observations,
            )
            pool_remaining = max(
                0,
                status.required_new_chain_pools
                - status.new_chain_pools,
            )
            items.append(
                _item(
                    45,
                    "WAITING_CHAIN_EVIDENCE",
                    status.champion_model_id,
                    (
                        "continuous retraining is waiting for real post-"
                        "champion chain evidence: "
                        f"{chain_remaining} observations and "
                        f"{pool_remaining} distinct pools remain toward the "
                        "configured trigger"
                    ),
                    operator_required=False,
                )
            )
        elif status.retrain_status == "WAITING_TRIGGER":
            label_remaining = max(
                0,
                status.retrain_live_label_trigger
                - status.new_live_labels_since_champion,
            )
            items.append(
                _item(
                    50,
                    "WAITING_RETRAIN_TRIGGER",
                    status.champion_model_id,
                    (
                        "chain evidence is sufficient but the retraining "
                        "trigger has not fired. "
                        f"{label_remaining} new champion live label(s) remain "
                        "to the label trigger unless champion age reaches "
                        f"{status.champion_age_trigger_days:.2f} days first"
                    ),
                    operator_required=False,
                )
            )
        elif status.retrain_status == "CHALLENGER_IN_PROGRESS":
            items.append(
                _item(
                    55,
                    "ORPHAN_CHALLENGER_REVIEW_REQUIRED",
                    ",".join(status.active_challenger_model_ids),
                    (
                        "model registry reports an active challenger but no "
                        "active retraining cycle is visible; inspect and "
                        "repair the challenger/cycle lineage before starting "
                        "another cycle"
                    ),
                )
            )

    if status.champion_model_id is not None and not status.promotion_ready:
        if status.completed_cycles < promotion_criteria.min_completed_cycles:
            items.append(
                _item(
                    60,
                    "COMPLETED_CYCLE_EVIDENCE_REQUIRED",
                    status.champion_model_id,
                    (
                        f"completed continuous cycles "
                        f"{status.completed_cycles}/"
                        f"{promotion_criteria.min_completed_cycles} are below "
                        "the Phase 8 promotion floor"
                    ),
                    operator_required=False,
                )
            )
        if status.champion_cycle_id is None:
            items.append(
                _item(
                    62,
                    "CHAMPION_CYCLE_LINEAGE_REQUIRED",
                    status.champion_model_id,
                    (
                        "current champion is not linked to a completed "
                        "continuous retraining cycle"
                    ),
                )
            )
        if status.continuous_promotion_evidence_id is None:
            items.append(
                _item(
                    64,
                    "CONTINUOUS_PROMOTION_EVIDENCE_REQUIRED",
                    status.champion_model_id,
                    (
                        "current champion lacks matching qualified "
                        "continuous-promotion evidence"
                    ),
                )
            )

        if status.live_label_count < status.required_live_labels:
            items.append(
                _item(
                    70,
                    "LIVE_LABEL_DEPTH_REQUIRED",
                    status.champion_model_id,
                    (
                        f"live champion labels {status.live_label_count}/"
                        f"{status.required_live_labels} are below the Phase 8 "
                        "promotion floor"
                    ),
                    operator_required=False,
                    shell_command=(
                        "pio ml-live-monitor --model-id "
                        + status.champion_model_id
                        + " --persist"
                    ),
                )
            )
        if status.live_distinct_pools < status.required_live_pools:
            items.append(
                _item(
                    72,
                    "LIVE_POOL_DIVERSITY_REQUIRED",
                    status.champion_model_id,
                    (
                        f"live champion distinct pools "
                        f"{status.live_distinct_pools}/"
                        f"{status.required_live_pools} are below the Phase 8 "
                        "promotion floor"
                    ),
                    operator_required=False,
                )
            )
        if (
            status.live_champion_status not in {None, "HEALTHY"}
            and not status.live_rollback_recommended
        ):
            items.append(
                _item(
                    74,
                    "LIVE_CHAMPION_HEALTH_REQUIRED",
                    status.champion_model_id,
                    (
                        "live champion status "
                        f"{status.live_champion_status} is not HEALTHY"
                    ),
                    operator_required=False,
                    shell_command=(
                        "pio ml-live-monitor --model-id "
                        + status.champion_model_id
                        + " --persist"
                    ),
                )
            )

    if status.promotion_ready and not status.persisted_phase8_current:
        items.append(
            _item(
                90,
                "PHASE8_PROMOTION_PERSISTENCE_REQUIRED",
                "PHASE8_PROMOTION",
                (
                    "the current Phase 8 gate passes, but persisted Phase 8 "
                    "promotion evidence is missing or stale. Persisting "
                    "promotion remains an explicit operator action"
                ),
                shell_command=(
                    "pio phase8-validate --persist-ready --require-ready"
                ),
            )
        )

    items.sort(key=lambda item: (item.priority, item.scope))
    next_action = items[0] if items else None
    reasons = tuple(dict.fromkeys(
        tuple(status.reasons)
        + tuple(item.reason for item in items)
    ))

    return Phase8EvidencePlan(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of=evaluation_time,
        promotion_ready=status.promotion_ready,
        persisted_phase8_current=status.persisted_phase8_current,
        next_action=next_action,
        items=tuple(items),
        reasons=reasons,
    )
