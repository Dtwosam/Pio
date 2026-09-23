from meteora_learner.paper_account import (
    close_paper_position,
    create_paper_account,
    mark_paper_position,
    open_paper_position,
    rebalance_paper_position,
)
from meteora_learner.paper_audit import audit_paper_ledger
from meteora_learner.storage import Storage


def seed_account(storage):
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
        min_bin_id=0,
        max_bin_id=2,
        capital_quote=100,
        entry_cost_quote=1,
        event_time="2026-09-23T09:00:00+00:00",
    )


def test_paper_ledger_audit_reconciles_open_position(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_account(storage)
    mark_paper_position(
        storage,
        event_key="mark",
        position_id="pos",
        mark_quote=110,
        fee_delta_quote=2,
        reward_delta_quote=3,
        event_time="2026-09-23T09:05:00+00:00",
    )
    rebalance_paper_position(
        storage,
        event_key="rebalance",
        position_id="pos",
        new_min_bin_id=1,
        new_max_bin_id=3,
        new_mark_quote=108,
        rebalance_cost_quote=0.5,
        event_time="2026-09-23T09:10:00+00:00",
    )

    report = audit_paper_ledger(storage, account_id="paper")

    assert report.passing is True
    assert report.positions_checked == 1
    assert report.position_failures == 0
    assert report.cash_drift_quote == 0
    assert report.positions[0].passing is True


def test_paper_ledger_audit_reconciles_closed_position(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_account(storage)
    mark_paper_position(
        storage,
        event_key="mark",
        position_id="pos",
        mark_quote=110,
        fee_delta_quote=2,
        reward_delta_quote=3,
        event_time="2026-09-23T09:05:00+00:00",
    )
    close_paper_position(
        storage,
        event_key="exit",
        position_id="pos",
        final_mark_quote=105,
        exit_cost_quote=1,
        fee_delta_quote=1,
        reward_delta_quote=2,
        event_time="2026-09-23T09:15:00+00:00",
    )

    report = audit_paper_ledger(storage, account_id="paper")

    assert report.passing is True
    assert report.position_failures == 0
    assert report.cash_drift_quote == 0


def test_paper_ledger_audit_detects_cash_and_position_drift(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_account(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE paper_accounts
            SET cash_quote = '999'
            WHERE account_id = 'paper'
            """
        )
        conn.execute(
            """
            UPDATE paper_positions
            SET fee_income_quote = '7'
            WHERE position_id = 'pos'
            """
        )

    report = audit_paper_ledger(storage, account_id="paper")

    assert report.passing is False
    assert report.cash_drift_quote != 0
    assert report.position_failures == 1
    assert any("cash_quote drift" in reason for reason in report.reasons)
    assert any(
        "fee_income_quote" in reason
        for reason in report.positions[0].reasons
    )
