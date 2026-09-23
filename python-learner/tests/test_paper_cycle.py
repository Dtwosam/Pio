from types import SimpleNamespace

from meteora_learner.baseline_policy import BaselineProposal
from meteora_learner.paper_account import (
    create_paper_account,
    open_paper_position,
)
from meteora_learner.paper_cycle import (
    apply_paper_observation,
    open_deterministic_paper_plan,
)
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


def authorized_plan():
    proposal = BaselineProposal(
        strategy="SPOT",
        half_width=1,
        center_offset=0,
        decision_active_bin_id=0,
        min_bin_id=-1,
        max_bin_id=1,
    )
    gate = SimpleNamespace(
        proposal=proposal,
        sized_quote=100.0,
    )
    return SimpleNamespace(
        pool_address="pool",
        policy_authorized=True,
        entry_gate=gate,
    )


def test_paper_plan_open_requires_phase3_promotion(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )

    blocked = open_deterministic_paper_plan(
        storage,
        account_id="paper",
        position_id="pos",
        event_key="enter",
        plan=authorized_plan(),
        phase3_ready=False,
    )
    assert blocked.opened is False
    assert blocked.account.cash_quote == 1000

    opened = open_deterministic_paper_plan(
        storage,
        account_id="paper",
        position_id="pos",
        event_key="enter",
        plan=authorized_plan(),
        phase3_ready=True,
    )
    assert opened.opened is True
    assert opened.position.min_bin_id == -1
    assert opened.position.max_bin_id == 1
    assert opened.account.cash_quote == 900


def test_paper_observation_automatically_exits_on_stop_loss(tmp_path):
    storage = Storage(tmp_path / "pio.db")
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
        min_bin_id=-1,
        max_bin_id=1,
        capital_quote=100,
    )

    result = apply_paper_observation(
        storage,
        event_key_prefix="obs-1",
        position_id="pos",
        active_bin_id=0,
        holding_observations=1,
        mark_quote=94,
        estimated_exit_cost_quote=1,
        config=PositionManagementConfig(stop_loss_bps=500),
    )

    assert result.executed_action == "EXIT"
    assert result.position_after.status == "CLOSED"
    assert result.account_after.open_positions == 0


def test_paper_observation_recenters_same_width_on_rebalance(tmp_path):
    storage = Storage(tmp_path / "pio.db")
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
        min_bin_id=-1,
        max_bin_id=1,
        capital_quote=100,
    )

    result = apply_paper_observation(
        storage,
        event_key_prefix="obs-1",
        position_id="pos",
        active_bin_id=3,
        holding_observations=1,
        mark_quote=100,
        rebalance_cost_quote=1,
        config=PositionManagementConfig(
            stop_loss_bps=5000,
            max_rebalances=3,
        ),
    )

    assert result.executed_action == "REBALANCE"
    assert result.position_after.min_bin_id == 2
    assert result.position_after.max_bin_id == 4
    assert result.position_after.rebalances == 1


def test_paper_rebalance_waits_when_cost_is_unknown(tmp_path):
    storage = Storage(tmp_path / "pio.db")
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
        min_bin_id=-1,
        max_bin_id=1,
        capital_quote=100,
    )

    result = apply_paper_observation(
        storage,
        event_key_prefix="obs-1",
        position_id="pos",
        active_bin_id=3,
        holding_observations=1,
        mark_quote=100,
        rebalance_cost_quote=None,
        config=PositionManagementConfig(
            stop_loss_bps=5000,
            max_rebalances=3,
        ),
    )

    assert result.executed_action == "REBALANCE_PENDING_COST"
    assert result.position_after.min_bin_id == -1
    assert result.position_after.max_bin_id == 1
