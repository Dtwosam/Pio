from meteora_learner.paper_account import (
    create_paper_account,
    mark_paper_position,
    open_paper_position,
    rebalance_paper_position,
)
from meteora_learner.paper_policy import evaluate_paper_position_policy
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


def open_position(storage):
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    open_paper_position(
        storage,
        event_key="enter",
        account_id="paper",
        position_id="pos",
        pool_address="pool",
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=-2,
        max_bin_id=2,
        capital_quote=100,
        entry_cost_quote=1,
    )


def test_paper_policy_uses_net_liquidation_pnl_for_stop_loss(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    open_position(storage)
    mark_paper_position(
        storage,
        event_key="mark",
        position_id="pos",
        mark_quote=96,
        fee_delta_quote=1,
    )

    result = evaluate_paper_position_policy(
        storage,
        position_id="pos",
        active_bin_id=0,
        holding_observations=2,
        pool_safe=True,
        estimated_exit_cost_quote=1,
        config=PositionManagementConfig(stop_loss_bps=500),
    )

    assert result.net_liquidation_value_quote == 96
    assert result.net_pnl_quote == -5
    assert result.net_pnl_bps == -500
    assert result.decision.action == "EXIT"


def test_paper_policy_counts_rebalances_when_deciding_out_of_range(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    open_position(storage)
    rebalance_paper_position(
        storage,
        event_key="rebalance",
        position_id="pos",
        new_min_bin_id=0,
        new_max_bin_id=4,
        new_mark_quote=99,
        rebalance_cost_quote=1,
    )

    result = evaluate_paper_position_policy(
        storage,
        position_id="pos",
        active_bin_id=5,
        holding_observations=3,
        pool_safe=True,
        config=PositionManagementConfig(max_rebalances=1, stop_loss_bps=5000),
    )

    assert result.position.rebalances == 1
    assert result.decision.action == "EXIT"
    assert "cap" in result.decision.reason
