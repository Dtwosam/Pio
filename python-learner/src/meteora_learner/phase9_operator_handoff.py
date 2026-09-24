from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .phase9_evidence_plan import (
    Phase9EvidenceDebtItem,
    build_phase9_evidence_plan,
)
from .phase9_evidence_status import evaluate_phase9_evidence_status
from .phase9_explicit_inputs import build_phase9_explicit_input_template
from .phase9_pool_cohort import Phase9PoolCohortCriteria
from .phase9_validation import Phase9ResearchBundleCriteria
from .storage import Storage, utc_now_iso
from .wallet_flow import WalletFlowCriteria


@dataclass(frozen=True)
class Phase9OperatorHandoff:
    research_only: bool
    read_only: bool
    policy_actionable: bool
    execution_wired: bool
    as_of: str
    status: str
    research_bundle_ready: bool
    debt_type: str | None
    scope: str | None
    reason: str | None
    automatic_action_available: bool
    operator_action_required: bool
    manual_input_required: bool
    suggested_command: str | None
    explicit_input_template: dict[str, Any] | None
    required_manual_fields: tuple[str, ...]
    followup_commands: tuple[str, ...]
    blockers: tuple[dict[str, Any], ...]
    reasons: tuple[str, ...]
    inspection_only: bool = False

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _required_explicit_fields(
    template: dict[str, Any],
) -> tuple[str, ...]:
    fields: list[str] = []
    for index, item in enumerate(template.get("static_hedges") or []):
        for key in ("amount_x", "amount_y"):
            if item.get(key) is None:
                fields.append(f"static_hedges[{index}].{key}")
        instrument = item.get("instrument") or {}
        for key in (
            "instrument_id",
            "venue",
            "available_liquidity_y_atomic",
            "funding_bps_per_holding_window",
        ):
            if instrument.get(key) is None:
                fields.append(
                    f"static_hedges[{index}].instrument.{key}"
                )
        criteria = item.get("criteria") or {}
        if criteria.get("hedge_round_trip_cost_bps") is None:
            fields.append(
                "static_hedges["
                f"{index}].criteria.hedge_round_trip_cost_bps"
            )

    for index, item in enumerate(template.get("pool_inputs") or []):
        for key in (
            "amount_x",
            "amount_y",
            "requested_quote",
            "network_cost_y_atomic",
        ):
            if item.get(key) is None:
                fields.append(f"pool_inputs[{index}].{key}")

    portfolio = template.get("portfolio") or {}
    for key in (
        "account_equity_quote",
        "cash_quote",
        "current_deployed_quote",
        "portfolio_drawdown_bps",
        "budget_quote",
    ):
        if portfolio.get(key) is None:
            fields.append(f"portfolio.{key}")

    return tuple(fields)


def _manual_blockers(
    items: tuple[Phase9EvidenceDebtItem, ...],
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
            item.blocking
            and (
                not item.actionable
                or item.debt_type == "EXPLICIT_RESEARCH_INPUTS"
            )
        )
    )


def build_phase9_operator_handoff(
    storage: Storage,
    *,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
    cohort_criteria: Phase9PoolCohortCriteria = (
        Phase9PoolCohortCriteria()
    ),
    wallet_criteria: WalletFlowCriteria = WalletFlowCriteria(),
    mint_max_snapshot_age_seconds: int = 3600,
    history_interval_seconds: int = 3600,
    as_of: str | None = None,
) -> Phase9OperatorHandoff:
    plan = build_phase9_evidence_plan(
        storage,
        criteria=criteria,
        cohort_criteria=cohort_criteria,
        wallet_criteria=wallet_criteria,
        mint_max_snapshot_age_seconds=mint_max_snapshot_age_seconds,
        history_interval_seconds=history_interval_seconds,
        as_of=as_of,
    )
    evaluation_time = plan.as_of
    blockers = _manual_blockers(plan.items)

    if plan.research_bundle_ready:
        return Phase9OperatorHandoff(
            research_only=True,
            read_only=True,
            policy_actionable=False,
            execution_wired=False,
            as_of=evaluation_time,
            status="READY",
            research_bundle_ready=True,
            debt_type=None,
            scope=None,
            reason=None,
            automatic_action_available=False,
            operator_action_required=False,
            manual_input_required=False,
            suggested_command=None,
            explicit_input_template=None,
            required_manual_fields=(),
            followup_commands=(),
            blockers=blockers,
            reasons=plan.reasons,
            inspection_only=plan.inspection_only,
        )

    action = plan.next_action
    if action is None:
        return Phase9OperatorHandoff(
            research_only=True,
            read_only=True,
            policy_actionable=False,
            execution_wired=False,
            as_of=evaluation_time,
            status="NO_ACTION",
            research_bundle_ready=False,
            debt_type=None,
            scope=None,
            reason=None,
            automatic_action_available=False,
            operator_action_required=bool(blockers),
            manual_input_required=False,
            suggested_command=None,
            explicit_input_template=None,
            required_manual_fields=(),
            followup_commands=(),
            blockers=blockers,
            reasons=plan.reasons,
            inspection_only=plan.inspection_only,
        )

    if plan.inspection_only:
        return Phase9OperatorHandoff(
            research_only=True,
            read_only=True,
            policy_actionable=False,
            execution_wired=False,
            as_of=evaluation_time,
            status="INSPECTION_ONLY",
            research_bundle_ready=False,
            debt_type=action.debt_type,
            scope=action.scope,
            reason=action.reason,
            automatic_action_available=False,
            operator_action_required=False,
            manual_input_required=False,
            suggested_command=None,
            explicit_input_template=None,
            required_manual_fields=(),
            followup_commands=(),
            blockers=blockers,
            reasons=plan.reasons,
            inspection_only=True,
        )

    explicit_template = None
    required_fields: tuple[str, ...] = ()
    followups: tuple[str, ...] = ()
    operator_action_required = not action.actionable
    manual_input_required = False
    status = "AUTOMATIC_ACTION"
    suggested_command = action.shell_command

    if action.debt_type == "EXPLICIT_RESEARCH_INPUTS":
        status = "MANUAL_REQUIRED"
        operator_action_required = True
        manual_input_required = True
        evidence_status = evaluate_phase9_evidence_status(
            storage,
            criteria=criteria,
            cohort_criteria=cohort_criteria,
            mint_max_snapshot_age_seconds=(
                mint_max_snapshot_age_seconds
            ),
            wallet_criteria=wallet_criteria,
            as_of=as_of,
        )
        template_pools = tuple(evidence_status.sampling_pools[:3])
        explicit_template = build_phase9_explicit_input_template(
            storage,
            pool_addresses=(template_pools or None),
        )
        required_fields = _required_explicit_fields(explicit_template)
        template_command = "pio phase9-research-input-template"
        if template_pools:
            template_command += (
                " --pools " + ",".join(template_pools)
            )
        template_command += " > phase9-research-inputs.json"
        suggested_command = template_command
        followups = (
            "pio phase9-research-inputs-check "
            "--file phase9-research-inputs.json --require-valid",
            "pio phase9-research-inputs-ingest "
            "--file phase9-research-inputs.json",
            "pio phase9-research-inputs-audit --require-valid",
            "pio phase9-explicit-research-run --persist --require-ready",
            "pio phase9-bandit-research-run --persist --require-qualified",
            "pio phase9-evidence-run --max-steps 8",
        )
    elif action.debt_type == "PHASE8_DEPENDENCY":
        status = "MANUAL_REQUIRED"
        operator_action_required = True
        manual_input_required = False
        suggested_command = "pio phase8-operator-handoff"
        followups = (
            "pio phase8-evidence-run --max-steps 4",
            "pio phase8-evidence-plan",
            "pio phase9-evidence-run --max-steps 8",
        )
    elif not action.actionable:
        status = "MANUAL_REQUIRED"
        operator_action_required = True

    automatic_action_available = bool(
        action.actionable
        and action.debt_type not in {
            "PHASE8_DEPENDENCY",
            "EXPLICIT_RESEARCH_INPUTS",
        }
    )

    return Phase9OperatorHandoff(
        research_only=True,
        read_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of=evaluation_time,
        status=status,
        research_bundle_ready=False,
        debt_type=action.debt_type,
        scope=action.scope,
        reason=action.reason,
        automatic_action_available=automatic_action_available,
        operator_action_required=operator_action_required,
        manual_input_required=manual_input_required,
        suggested_command=suggested_command,
        explicit_input_template=explicit_template,
        required_manual_fields=required_fields,
        followup_commands=followups,
        blockers=blockers,
        reasons=plan.reasons,
        inspection_only=plan.inspection_only,
    )
