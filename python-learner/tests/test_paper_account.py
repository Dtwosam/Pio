import pytest

from meteora_learner.paper_account import (
    close_paper_position,
    create_paper_account,
    mark_paper_position,
    open_paper_position,
    paper_account_snapshot,
    rebalance_paper_position,
)
from meteora_learner.storage import Storage


def test_paper_ledger_reconciles_cash_equity_costs_and_realized_pnl(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    account = create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    assert account.account_equity_quote == 1000

    open_paper_position(
        storage,
        event_key="enter-1",
        account_id="paper",
        position_id="pos-1",
        pool_address="pool",
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=-2,
        max_bin_id=2,
        capital_quote=100,
        entry_cost_quote=1,
    )
    account = paper_account_snapshot(storage, account_id="paper")
    assert account.cash_quote == 899
    assert account.account_equity_quote == 999
    assert account.drawdown_bps == 10

    mark_paper_position(
        storage,
        event_key="mark-1",
        position_id="pos-1",
        mark_quote=110,
        fee_delta_quote=5,
        reward_delta_quote=2,
    )
    account = paper_account_snapshot(storage, account_id="paper")
    assert account.account_equity_quote == 1016
    assert account.high_water_equity_quote == 1016
    assert account.drawdown_bps == 0

    position = rebalance_paper_position(
        storage,
        event_key="rebalance-1",
        position_id="pos-1",
        new_min_bin_id=0,
        new_max_bin_id=4,
        new_mark_quote=108,
        rebalance_cost_quote=2,
    )
    assert position.rebalances == 1
    assert position.rebalance_cost_quote == 2

    closed = close_paper_position(
        storage,
        event_key="exit-1",
        position_id="pos-1",
        final_mark_quote=108,
        exit_cost_quote=1,
    )
    assert closed.status == "CLOSED"
    assert closed.realized_pnl_quote == 11

    account = paper_account_snapshot(storage, account_id="paper")
    assert account.cash_quote == 1011
    assert account.account_equity_quote == 1011
    assert account.open_positions == 0
    assert account.closed_positions == 1
    assert account.realized_pnl_quote == 11


def test_paper_event_keys_are_idempotency_guards(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    open_paper_position(
        storage,
        event_key="enter-1",
        account_id="paper",
        position_id="pos-1",
        pool_address="pool",
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=-1,
        max_bin_id=1,
        capital_quote=100,
    )

    with pytest.raises(ValueError, match="already applied"):
        mark_paper_position(
            storage,
            event_key="enter-1",
            position_id="pos-1",
            mark_quote=100,
        )


def test_paper_entry_rejects_insufficient_cash(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=100,
    )

    with pytest.raises(ValueError, match="insufficient paper cash"):
        open_paper_position(
            storage,
            event_key="enter-1",
            account_id="paper",
            position_id="pos-1",
            pool_address="pool",
            policy_source="DETERMINISTIC",
            strategy="SPOT",
            min_bin_id=-1,
            max_bin_id=1,
            capital_quote=100,
            entry_cost_quote=1,
        )
