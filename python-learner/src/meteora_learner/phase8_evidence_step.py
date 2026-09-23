from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .continuous_learning import ContinuousLearningCriteria
from .phase8_evidence_plan import (
    Phase8EvidencePlan,
    build_phase8_evidence_plan,
)
from .phase8_offline_retraining import (
    train_phase8_cycle_challenger,
    validate_phase8_cycle_challenger_offline,
)
from .phase8_retrain_inputs import (
    load_phase8_retrain_inputs,
    run_phase8_retrain_build_from_inputs,
)
from .phase8_validation import Phase8PromotionCriteria
from .storage import Storage


@dataclass(frozen=True)
class Phase8EvidenceStepReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    status: str
    debt_type: str | None
    scope: str | None
    progressed: bool
    operation: dict[str, Any] | None
    error: str | None
    plan_before: Phase8EvidencePlan
    plan_after: Phase8EvidencePlan

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _progressed(
    before: Phase8EvidencePlan,
    after: Phase8EvidencePlan,
) -> bool:
    if (
        not before.persisted_phase8_current
        and after.persisted_phase8_current
    ):
        return True
    old = before.next_action
    new = after.next_action
    if old is None:
        return False
    if new is None:
        return True
    return (old.debt_type, old.scope) != (new.debt_type, new.scope)


def run_phase8_evidence_step(
    storage: Storage,
    *,
    learning_criteria: ContinuousLearningCriteria = (
        ContinuousLearningCriteria()
    ),
    promotion_criteria: Phase8PromotionCriteria = (
        Phase8PromotionCriteria()
    ),
) -> Phase8EvidenceStepReport:
    before = build_phase8_evidence_plan(
        storage,
        learning_criteria=learning_criteria,
        promotion_criteria=promotion_criteria,
    )
    action = before.next_action

    if action is None:
        return Phase8EvidenceStepReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="READY",
            debt_type=None,
            scope=None,
            progressed=False,
            operation=None,
            error=None,
            plan_before=before,
            plan_after=before,
        )

    if action.operator_required:
        return Phase8EvidenceStepReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="MANUAL_REQUIRED",
            debt_type=action.debt_type,
            scope=action.scope,
            progressed=False,
            operation=None,
            error=None,
            plan_before=before,
            plan_after=before,
        )

    if action.shell_command is None:
        return Phase8EvidenceStepReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="WAITING",
            debt_type=action.debt_type,
            scope=action.scope,
            progressed=False,
            operation=None,
            error=None,
            plan_before=before,
            plan_after=before,
        )

    operation = None
    error = None
    status = "COMPLETE"

    try:
        if action.debt_type == "RETRAIN_DATASET_BUILD_READY":
            artifact = load_phase8_retrain_inputs(
                storage,
                evidence_id=int(action.scope),
            )
            if artifact is None:
                raise ValueError(
                    "Phase 8 retraining input artifact disappeared"
                )
            result = run_phase8_retrain_build_from_inputs(
                storage,
                artifact=artifact,
                criteria=learning_criteria,
            )
            operation = result.to_record()
        elif action.debt_type == "RETRAIN_OFFLINE_TRAIN_READY":
            result = train_phase8_cycle_challenger(
                storage,
                cycle_id=action.scope,
            )
            operation = result.to_record()
        elif action.debt_type == "RETRAIN_OFFLINE_VALIDATION_READY":
            result = validate_phase8_cycle_challenger_offline(
                storage,
                cycle_id=action.scope,
            )
            operation = result.to_record()
            if not result.offline_qualified:
                status = "NOT_QUALIFIED"
        else:
            status = "MANUAL_REQUIRED"
    except Exception as exc:
        status = "FAILED"
        error = f"{type(exc).__name__}: {str(exc)[:2000]}"

    after = build_phase8_evidence_plan(
        storage,
        learning_criteria=learning_criteria,
        promotion_criteria=promotion_criteria,
    )
    return Phase8EvidenceStepReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        status=status,
        debt_type=action.debt_type,
        scope=action.scope,
        progressed=(
            status == "COMPLETE"
            and _progressed(before, after)
        ),
        operation=operation,
        error=error,
        plan_before=before,
        plan_after=after,
    )
