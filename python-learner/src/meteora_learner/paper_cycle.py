from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .paper_account import (
    PaperAccountSnapshot,
    PaperPositionSnapshot,
    close_paper_position,
    mark_paper_position,
    open_paper_position,
    paper_account_snapshot,
    paper_position_snapshot,
    rebalance_paper_position,
)
from .paper_policy import (
    PaperPositionPolicyEvaluation,
    evaluate_paper_position_policy,
)
from .phase3_plan import Phase3ResearchPlan
from .position_policy import PositionManagementConfig
from .storage import Storage


@dataclass(frozen=True)
class PaperEntryResult:
    opened: bool
    reason: str | None
    position: PaperPositionSnapshot | None
    account: PaperAccountSnapshot

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperObservationResult:
    position_id: str
    policy: PaperPositionPolicyEvaluation
    executed_action: str
    execution_reason: str | None
    position_after: PaperPositionSnapshot
    account_after: PaperAccountSnapshot

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def open_deterministic_paper_plan(
    storage: Storage,
    *,
    account_id: str,
    position_id: str,
    event_key: str,
    plan: Phase3ResearchPlan,
    phase3_ready: bool,
    entry_cost_quote: float = 0.0,
) -> PaperEntryResult:
    """
    Turn one fully-authorized deterministic research plan into a paper position.

    Phase 3 promotion is required in addition to the plan's Phase 2/pool/baseline/
    sizing authorization. No transaction is built, signed or sent.
    """
    account = paper_account_snapshot(storage, account_id=account_id)

    if not phase3_ready:
        return PaperEntryResult(
            opened=False,
            reason="Phase 3 deterministic policy is not promoted",
            position=None,
            account=account,
        )
    if not plan.policy_authorized or plan.entry_gate is None:
        return PaperEntryResult(
            opened=False,
            reason="Phase 3 plan is not policy-authorized",
            position=None,
            account=account,
        )
    if plan.entry_gate.proposal is None:
        return PaperEntryResult(
            opened=False,
            reason="Phase 3 plan has no actionable proposal",
            position=None,
            account=account,
        )
    if plan.entry_gate.sized_quote <= 0:
        return PaperEntryResult(
            opened=False,
            reason="Phase 3 plan has no deployable capital",
            position=None,
            account=account,
        )

    proposal = plan.entry_gate.proposal
    position = open_paper_position(
        storage,
        event_key=event_key,
        account_id=account_id,
        position_id=position_id,
        pool_address=plan.pool_address,
        policy_source="DETERMINISTIC",
        strategy=proposal.strategy,
        min_bin_id=proposal.min_bin_id,
        max_bin_id=proposal.max_bin_id,
        capital_quote=plan.entry_gate.sized_quote,
        entry_cost_quote=entry_cost_quote,
    )
    return PaperEntryResult(
        opened=True,
        reason=None,
        position=position,
        account=paper_account_snapshot(storage, account_id=account_id),
    )


def _recenter_same_width(
    *,
    active_bin_id: int,
    min_bin_id: int,
    max_bin_id: int,
) -> tuple[int, int]:
    width = max_bin_id - min_bin_id
    left = width // 2
    new_min = active_bin_id - left
    return new_min, new_min + width


def apply_paper_observation(
    storage: Storage,
    *,
    event_key_prefix: str,
    position_id: str,
    active_bin_id: int,
    holding_observations: int,
    mark_quote: float,
    fee_delta_quote: float = 0.0,
    reward_delta_quote: float = 0.0,
    pool_safe: bool = True,
    emergency_exit: bool = False,
    estimated_exit_cost_quote: float = 0.0,
    rebalance_cost_quote: float | None = None,
    config: PositionManagementConfig = PositionManagementConfig(),
) -> PaperObservationResult:
    """
    Apply one paper observation and deterministic management action.

    HOLD needs no further mutation. EXIT realizes current marked economics.
    REBALANCE recenters the existing width on the active bin when a calibrated
    rebalance cost is supplied; otherwise it remains pending rather than assuming
    a zero cost.
    """
    before = paper_position_snapshot(storage, position_id=position_id)
    if before.status != "OPEN":
        raise ValueError("paper position must be open")

    mark_paper_position(
        storage,
        event_key=f"{event_key_prefix}:mark",
        position_id=position_id,
        mark_quote=mark_quote,
        fee_delta_quote=fee_delta_quote,
        reward_delta_quote=reward_delta_quote,
    )
    policy = evaluate_paper_position_policy(
        storage,
        position_id=position_id,
        active_bin_id=active_bin_id,
        holding_observations=holding_observations,
        pool_safe=pool_safe,
        estimated_exit_cost_quote=estimated_exit_cost_quote,
        emergency_exit=emergency_exit,
        config=config,
    )

    executed_action = "HOLD"
    reason: str | None = policy.decision.reason

    if policy.decision.action == "EXIT":
        close_paper_position(
            storage,
            event_key=f"{event_key_prefix}:exit",
            position_id=position_id,
            final_mark_quote=mark_quote,
            exit_cost_quote=estimated_exit_cost_quote,
        )
        executed_action = "EXIT"
    elif policy.decision.action == "REBALANCE":
        if rebalance_cost_quote is None:
            executed_action = "REBALANCE_PENDING_COST"
            reason = (
                "rebalance requested but no calibrated rebalance cost was supplied"
            )
        else:
            new_min, new_max = _recenter_same_width(
                active_bin_id=active_bin_id,
                min_bin_id=before.min_bin_id,
                max_bin_id=before.max_bin_id,
            )
            rebalance_paper_position(
                storage,
                event_key=f"{event_key_prefix}:rebalance",
                position_id=position_id,
                new_min_bin_id=new_min,
                new_max_bin_id=new_max,
                new_mark_quote=mark_quote,
                rebalance_cost_quote=rebalance_cost_quote,
            )
            executed_action = "REBALANCE"

    after = paper_position_snapshot(storage, position_id=position_id)
    return PaperObservationResult(
        position_id=position_id,
        policy=policy,
        executed_action=executed_action,
        execution_reason=reason,
        position_after=after,
        account_after=paper_account_snapshot(
            storage,
            account_id=after.account_id,
        ),
    )
