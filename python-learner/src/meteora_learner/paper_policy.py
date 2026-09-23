from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from .paper_account import PaperPositionSnapshot, paper_position_snapshot
from .position_policy import (
    PositionManagementConfig,
    PositionManagementDecision,
    decide_position_action,
)
from .storage import Storage


BPS = Decimal("10000")


@dataclass(frozen=True)
class PaperPositionPolicyEvaluation:
    position: PaperPositionSnapshot
    estimated_exit_cost_quote: float
    net_liquidation_value_quote: float
    net_pnl_quote: float
    net_pnl_bps: int
    decision: PositionManagementDecision

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_paper_position_policy(
    storage: Storage,
    *,
    position_id: str,
    active_bin_id: int,
    holding_observations: int,
    pool_safe: bool,
    estimated_exit_cost_quote: float = 0.0,
    emergency_exit: bool = False,
    config: PositionManagementConfig = PositionManagementConfig(),
) -> PaperPositionPolicyEvaluation:
    position = paper_position_snapshot(storage, position_id=position_id)
    if position.status != "OPEN":
        raise ValueError("paper position must be open")

    exit_cost = Decimal(str(estimated_exit_cost_quote))
    if exit_cost < 0:
        raise ValueError("estimated_exit_cost_quote cannot be negative")

    mark = Decimal(str(position.current_mark_quote))
    fees = Decimal(str(position.fee_income_quote))
    rewards = Decimal(str(position.reward_income_quote))
    entry_capital = Decimal(str(position.entry_capital_quote))
    entry_cost = Decimal(str(position.entry_cost_quote))
    rebalance_cost = Decimal(str(position.rebalance_cost_quote))

    net_liquidation = mark + fees + rewards - exit_cost
    pnl = (
        net_liquidation
        - entry_capital
        - entry_cost
        - rebalance_cost
    )
    pnl_bps = (
        int(pnl * BPS / entry_capital)
        if entry_capital > 0
        else 0
    )

    decision = decide_position_action(
        active_bin_id=active_bin_id,
        min_bin_id=position.min_bin_id,
        max_bin_id=position.max_bin_id,
        holding_observations=holding_observations,
        rebalances_done=position.rebalances,
        pool_safe=pool_safe,
        emergency_exit=emergency_exit,
        net_pnl_bps=pnl_bps,
        config=config,
    )

    return PaperPositionPolicyEvaluation(
        position=position,
        estimated_exit_cost_quote=float(exit_cost),
        net_liquidation_value_quote=float(net_liquidation),
        net_pnl_quote=float(pnl),
        net_pnl_bps=pnl_bps,
        decision=decision,
    )
