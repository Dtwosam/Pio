import pytest

from meteora_learner.position_policy import (
    PositionManagementConfig,
    decide_position_action,
)


def test_unsafe_pool_exits_even_when_position_is_losing():
    decision = decide_position_action(
        active_bin_id=0,
        min_bin_id=-2,
        max_bin_id=2,
        holding_observations=5,
        rebalances_done=0,
        pool_safe=False,
        net_pnl_bps=-200,
    )
    assert decision.action == "EXIT"
    assert "safety screen" in decision.reason


def test_stop_loss_exits_inside_range():
    decision = decide_position_action(
        active_bin_id=0,
        min_bin_id=-2,
        max_bin_id=2,
        holding_observations=5,
        rebalances_done=0,
        pool_safe=True,
        net_pnl_bps=-500,
        config=PositionManagementConfig(stop_loss_bps=500),
    )
    assert decision.action == "EXIT"
    assert "stop-loss" in decision.reason


def test_out_of_range_rebalances_when_cap_remains():
    decision = decide_position_action(
        active_bin_id=3,
        min_bin_id=-2,
        max_bin_id=2,
        holding_observations=5,
        rebalances_done=1,
        pool_safe=True,
        net_pnl_bps=20,
        config=PositionManagementConfig(max_rebalances=3),
    )
    assert decision.action == "REBALANCE"


def test_out_of_range_exits_after_rebalance_cap():
    decision = decide_position_action(
        active_bin_id=3,
        min_bin_id=-2,
        max_bin_id=2,
        holding_observations=5,
        rebalances_done=3,
        pool_safe=True,
        net_pnl_bps=20,
        config=PositionManagementConfig(max_rebalances=3),
    )
    assert decision.action == "EXIT"
    assert "cap" in decision.reason


def test_take_profit_is_optional():
    hold = decide_position_action(
        active_bin_id=0,
        min_bin_id=-2,
        max_bin_id=2,
        holding_observations=5,
        rebalances_done=0,
        pool_safe=True,
        net_pnl_bps=1000,
    )
    assert hold.action == "HOLD"

    exit_decision = decide_position_action(
        active_bin_id=0,
        min_bin_id=-2,
        max_bin_id=2,
        holding_observations=5,
        rebalances_done=0,
        pool_safe=True,
        net_pnl_bps=1000,
        config=PositionManagementConfig(take_profit_bps=1000),
    )
    assert exit_decision.action == "EXIT"


def test_invalid_range_is_rejected():
    with pytest.raises(ValueError):
        decide_position_action(
            active_bin_id=0,
            min_bin_id=2,
            max_bin_id=-2,
            holding_observations=0,
            rebalances_done=0,
            pool_safe=True,
        )
