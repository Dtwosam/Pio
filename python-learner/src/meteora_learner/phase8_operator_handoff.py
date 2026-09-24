from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .continuous_learning import ContinuousLearningCriteria
from .phase8_evidence_plan import (
    Phase8EvidenceDebtItem,
    build_phase8_evidence_plan,
)
from .phase8_retrain_inputs import build_phase8_retrain_input_template
from .phase8_validation import Phase8PromotionCriteria
from .storage import Storage, utc_now_iso


AUTOMATIC_OFFLINE_DEBT_TYPES = {
    "RETRAIN_DATASET_BUILD_READY",
    "RETRAIN_OFFLINE_TRAIN_READY",
    "RETRAIN_OFFLINE_VALIDATION_READY",
}


@dataclass(frozen=True)
class Phase8OperatorHandoff:
    research_only: bool
    read_only: bool
    policy_actionable: bool
    execution_wired: bool
    as_of: str
    status: str
    promotion_ready: bool
    persisted_phase8_current: bool
    debt_type: str | None
    scope: str | None
    reason: str | None
    automatic_action_available: bool
    operator_action_required: bool
    manual_input_required: bool
    suggested_command: str | None
    retrain_input_template: dict[str, Any] | None
    required_manual_fields: tuple[str, ...]
    followup_commands: tuple[str, ...]
    operator_blockers: tuple[dict[str, Any], ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _required_retrain_fields(
    template: dict[str, Any],
) -> tuple[str, ...]:
    fields: list[str] = []
    if template.get("champion_model_id") is None:
        fields.append("champion_model_id")
    if template.get("champion_dataset_version") is None:
        fields.append("champion_dataset_version")
    for index, item in enumerate(template.get("pools") or []):
        for key in (
            "pool_address",
            "amount_x",
            "amount_y",
            "network_cost_y_atomic",
        ):
            if item.get(key) is None:
                fields.append(f"pools[{index}].{key}")
    return tuple(fields)


def _operator_blockers(
    items: tuple[Phase8EvidenceDebtItem, ...],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "priority": item.priority,
            "debt_type": item.debt_type,
            "scope": item.scope,
            "reason": item.reason,
            "inspection_command": item.shell_command,
        }
        for item in items
        if (
            item.operator_required
            or item.debt_type == "RETRAIN_DATASET_INPUTS_REQUIRED"
            or (
                item.shell_command is not None
                and item.debt_type not in AUTOMATIC_OFFLINE_DEBT_TYPES
            )
        )
    )


def build_phase8_operator_handoff(
    storage: Storage,
    *,
    learning_criteria: ContinuousLearningCriteria = (
        ContinuousLearningCriteria()
    ),
    promotion_criteria: Phase8PromotionCriteria = (
        Phase8PromotionCriteria()
    ),
    as_of: str | None = None,
) -> Phase8OperatorHandoff:
    plan = build_phase8_evidence_plan(
        storage,
        learning_criteria=learning_criteria,
        promotion_criteria=promotion_criteria,
        as_of=as_of,
    )
    evaluation_time = plan.as_of
    blockers = _operator_blockers(plan.items)

    if plan.persisted_phase8_current:
        return Phase8OperatorHandoff(
            research_only=True,
            read_only=True,
            policy_actionable=False,
            execution_wired=False,
            as_of=evaluation_time,
            status="READY",
            promotion_ready=plan.promotion_ready,
            persisted_phase8_current=True,
            debt_type=None,
            scope=None,
            reason=None,
            automatic_action_available=False,
            operator_action_required=False,
            manual_input_required=False,
            suggested_command=None,
            retrain_input_template=None,
            required_manual_fields=(),
            followup_commands=(),
            operator_blockers=blockers,
            reasons=plan.reasons,
        )

    action = plan.next_action
    if action is None:
        return Phase8OperatorHandoff(
            research_only=True,
            read_only=True,
            policy_actionable=False,
            execution_wired=False,
            as_of=evaluation_time,
            status="NO_ACTION",
            promotion_ready=plan.promotion_ready,
            persisted_phase8_current=False,
            debt_type=None,
            scope=None,
            reason=None,
            automatic_action_available=False,
            operator_action_required=bool(blockers),
            manual_input_required=False,
            suggested_command=None,
            retrain_input_template=None,
            required_manual_fields=(),
            followup_commands=(),
            operator_blockers=blockers,
            reasons=plan.reasons,
        )

    automatic = action.debt_type in AUTOMATIC_OFFLINE_DEBT_TYPES
    operator_required = False
    manual_input_required = False
    status = "AUTOMATIC_ACTION" if automatic else "WAITING_EVIDENCE"
    suggested_command = (
        "pio phase8-evidence-run --max-steps 4"
        if automatic
        else None
    )
    retrain_template = None
    required_fields: tuple[str, ...] = ()
    followups: tuple[str, ...] = ()

    if action.debt_type == "RETRAIN_DATASET_INPUTS_REQUIRED":
        status = "MANUAL_REQUIRED"
        operator_required = True
        manual_input_required = True
        retrain_template = build_phase8_retrain_input_template(storage)
        required_fields = _required_retrain_fields(retrain_template)
        suggested_command = (
            "pio phase8-retrain-input-template "
            "> phase8-retrain-inputs.json"
        )
        followups = (
            "pio phase8-retrain-inputs-check "
            "--file phase8-retrain-inputs.json --require-valid",
            "pio phase8-retrain-inputs-ingest "
            "--file phase8-retrain-inputs.json",
            "pio phase8-retrain-inputs-audit --require-valid",
            "pio phase8-evidence-run --max-steps 4",
        )
    elif (
        action.operator_required
        or (
            action.shell_command is not None
            and not automatic
        )
    ):
        status = "MANUAL_REQUIRED"
        operator_required = True
        suggested_command = action.shell_command
        followups = ("pio phase8-evidence-plan",)

    return Phase8OperatorHandoff(
        research_only=True,
        read_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of=evaluation_time,
        status=status,
        promotion_ready=plan.promotion_ready,
        persisted_phase8_current=plan.persisted_phase8_current,
        debt_type=action.debt_type,
        scope=action.scope,
        reason=action.reason,
        automatic_action_available=automatic,
        operator_action_required=operator_required,
        manual_input_required=manual_input_required,
        suggested_command=suggested_command,
        retrain_input_template=retrain_template,
        required_manual_fields=required_fields,
        followup_commands=followups,
        operator_blockers=blockers,
        reasons=plan.reasons,
    )
