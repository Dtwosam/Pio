from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class PositionAction(str, Enum):
    HOLD = "HOLD"
    REBALANCE = "REBALANCE"
    EXIT = "EXIT"


@dataclass(frozen=True)
class PositionManagementConfig:
    stop_loss_bps: int = 500
    take_profit_bps: int | None = None
    max_rebalances: int = 3
    max_holding_observations: int | None = None
    proactive_rebalance_buffer_bins: int = 0

    def __post_init__(self) -> None:
        if self.stop_loss_bps < 0:
            raise ValueError("stop_loss_bps cannot be negative")
        if self.take_profit_bps is not None and self.take_profit_bps < 0:
            raise ValueError("take_profit_bps cannot be negative")
        if self.max_rebalances < 0:
            raise ValueError("max_rebalances cannot be negative")
        if (
            self.max_holding_observations is not None
            and self.max_holding_observations <= 0
        ):
            raise ValueError("max_holding_observations must be positive")
        if self.proactive_rebalance_buffer_bins < 0:
            raise ValueError("proactive_rebalance_buffer_bins cannot be negative")


@dataclass(frozen=True)
class PositionManagementDecision:
    action: str
    reason: str
    active_bin_id: int
    min_bin_id: int
    max_bin_id: int
    in_range: bool
    distance_to_nearest_edge: int | None
    net_pnl_bps: int | None
    holding_observations: int
    rebalances_done: int
    pool_safe: bool
    emergency_exit: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def decide_position_action(
    *,
    active_bin_id: int,
    min_bin_id: int,
    max_bin_id: int,
    holding_observations: int,
    rebalances_done: int,
    pool_safe: bool,
    emergency_exit: bool = False,
    net_pnl_bps: int | None = None,
    config: PositionManagementConfig = PositionManagementConfig(),
) -> PositionManagementDecision:
    """
    Deterministic management policy for an existing position.

    Safety exits override PnL. The policy never keeps an unsafe position open
    merely to wait for it to become profitable.
    """
    if min_bin_id > max_bin_id:
        raise ValueError("min_bin_id cannot exceed max_bin_id")
    if holding_observations < 0:
        raise ValueError("holding_observations cannot be negative")
    if rebalances_done < 0:
        raise ValueError("rebalances_done cannot be negative")

    in_range = min_bin_id <= active_bin_id <= max_bin_id
    edge_distance = (
        min(active_bin_id - min_bin_id, max_bin_id - active_bin_id)
        if in_range
        else None
    )

    action = PositionAction.HOLD
    reason = "position remains inside deterministic management limits"

    if emergency_exit:
        action = PositionAction.EXIT
        reason = "emergency exit is active"
    elif not pool_safe:
        action = PositionAction.EXIT
        reason = "pool failed current safety screen"
    elif net_pnl_bps is not None and net_pnl_bps <= -config.stop_loss_bps:
        action = PositionAction.EXIT
        reason = (
            f"net PnL {net_pnl_bps} bps reached stop-loss "
            f"-{config.stop_loss_bps} bps"
        )
    elif (
        config.take_profit_bps is not None
        and net_pnl_bps is not None
        and net_pnl_bps >= config.take_profit_bps
    ):
        action = PositionAction.EXIT
        reason = (
            f"net PnL {net_pnl_bps} bps reached take-profit "
            f"{config.take_profit_bps} bps"
        )
    elif (
        config.max_holding_observations is not None
        and holding_observations >= config.max_holding_observations
    ):
        action = PositionAction.EXIT
        reason = (
            f"holding observations {holding_observations} reached maximum "
            f"{config.max_holding_observations}"
        )
    elif not in_range:
        if rebalances_done >= config.max_rebalances:
            action = PositionAction.EXIT
            reason = (
                f"position is out of range and rebalance cap "
                f"{config.max_rebalances} is exhausted"
            )
        else:
            action = PositionAction.REBALANCE
            reason = "active bin moved outside position range"
    elif (
        config.proactive_rebalance_buffer_bins > 0
        and edge_distance is not None
        and edge_distance < config.proactive_rebalance_buffer_bins
    ):
        if rebalances_done >= config.max_rebalances:
            action = PositionAction.HOLD
            reason = (
                "active bin is near range edge but rebalance cap is exhausted; "
                "hold until another exit rule fires"
            )
        else:
            action = PositionAction.REBALANCE
            reason = (
                f"active bin is within {edge_distance} bin(s) of range edge"
            )

    return PositionManagementDecision(
        action=action.value,
        reason=reason,
        active_bin_id=active_bin_id,
        min_bin_id=min_bin_id,
        max_bin_id=max_bin_id,
        in_range=in_range,
        distance_to_nearest_edge=edge_distance,
        net_pnl_bps=net_pnl_bps,
        holding_observations=holding_observations,
        rebalances_done=rebalances_done,
        pool_safe=pool_safe,
        emergency_exit=emergency_exit,
    )
