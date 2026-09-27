from __future__ import annotations

from types import SimpleNamespace

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.market_paper_exploration import run_market_paper_exploration
from meteora_learner.paper_account import (
    create_paper_account,
    open_paper_position,
)
from meteora_learner.storage import Storage


def _chain(storage, pool, token_y, observed_at):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": pool,
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": f"{pool}-x",
            "token_y_mint": token_y,
            "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
            "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
            "base_fee_rate": "0",
            "variable_fee_rate": "0",
            "total_fee_rate": "0",
            "deposit_total_fee_rate": "0",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
            "supports_limit_order": False,
            "reward_mint_0": None,
            "reward_mint_1": None,
            "bin_arrays": [],
        },
        observed_at=observed_at,
    )


def _intake(*pools):
    return SimpleNamespace(
        research_only=True,
        paper_only=True,
        policy_actionable=False,
        execution_wired=False,
        pools=tuple(
            SimpleNamespace(
                pool_address=pool,
                ready_for_candidate_cycle=True,
            )
            for pool in pools
        ),
    )


def test_exploration_uses_neutral_order_and_quote_normalized_amounts(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    _chain(storage, "A", "mint-a", "2026-09-27T08:00:00+00:00")
    _chain(storage, "B", "mint-b", "2026-09-27T08:00:00+00:00")
    refreshed = []
    entries = []

    def refresh(storage_arg, *, token_mints, observed_at):
        refreshed.extend(sorted(token_mints))
        return ()

    def status(storage_arg, *, token_mint, **kwargs):
        return SimpleNamespace(
            fresh=True,
            quote_per_atomic=(0.5 if token_mint == "mint-a" else 0.25),
        )

    def entry(storage_arg, **kwargs):
        entries.append(kwargs)
        return SimpleNamespace(
            status="OPENED",
            paper_only=True,
            live_authorized=False,
        )

    report = run_market_paper_exploration(
        storage,
        account_id="paper",
        run_id="r1",
        per_position_capital_quote=100,
        network_cost_quote=1,
        max_new_positions=2,
        intake_runner=lambda database_path, **kwargs: _intake("A", "B"),
        quote_refresher=refresh,
        quote_status_loader=status,
        entry_runner=entry,
    )

    assert refreshed == ["mint-a", "mint-b"]
    assert [row["pool_address"] for row in entries] == ["A", "B"]
    assert entries[0]["amount_y"] == 200
    assert entries[0]["network_cost_y_atomic"] == 2
    assert entries[0]["as_of"] == "2026-09-27T08:00:00+00:00"
    assert entries[1]["amount_y"] == 400
    assert entries[1]["network_cost_y_atomic"] == 4
    assert report.positions_opened == 2
    assert report.paper_only is True
    assert report.live_authorized is False


def test_exploration_skips_pool_already_open_in_account(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    open_paper_position(
        storage,
        event_key="existing",
        account_id="paper",
        position_id="existing-pos",
        pool_address="A",
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=0,
        max_bin_id=0,
        capital_quote=100,
    )
    _chain(storage, "A", "mint-a", "2026-09-27T08:00:00+00:00")
    _chain(storage, "B", "mint-b", "2026-09-27T08:00:00+00:00")
    entries = []

    report = run_market_paper_exploration(
        storage,
        account_id="paper",
        run_id="r2",
        per_position_capital_quote=100,
        network_cost_quote=0,
        max_new_positions=1,
        intake_runner=lambda database_path, **kwargs: _intake("A", "B"),
        quote_refresher=lambda *args, **kwargs: (),
        quote_status_loader=lambda *args, **kwargs: SimpleNamespace(
            fresh=True,
            quote_per_atomic=1,
        ),
        entry_runner=lambda storage_arg, **kwargs: (
            entries.append(kwargs)
            or SimpleNamespace(
                status="OPENED",
                paper_only=True,
                live_authorized=False,
            )
        ),
    )

    assert [row["pool_address"] for row in entries] == ["B"]
    assert any(
        item.pool_address == "A" and item.status == "ALREADY_OPEN"
        for item in report.items
    )
    assert report.positions_opened == 1


def test_quote_failure_does_not_expose_raw_error_or_stop_next_pool(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    _chain(storage, "A", "mint-a", "2026-09-27T08:00:00+00:00")
    _chain(storage, "B", "mint-b", "2026-09-27T08:00:00+00:00")

    def status(storage_arg, *, token_mint, **kwargs):
        if token_mint == "mint-a":
            return SimpleNamespace(fresh=False, quote_per_atomic=None)
        return SimpleNamespace(fresh=True, quote_per_atomic=1)

    entries = []
    report = run_market_paper_exploration(
        storage,
        account_id="paper",
        run_id="r3",
        per_position_capital_quote=50,
        network_cost_quote=0,
        max_new_positions=1,
        intake_runner=lambda database_path, **kwargs: _intake("A", "B"),
        quote_refresher=lambda *args, **kwargs: (),
        quote_status_loader=status,
        entry_runner=lambda storage_arg, **kwargs: (
            entries.append(kwargs)
            or SimpleNamespace(
                status="OPENED",
                paper_only=True,
                live_authorized=False,
            )
        ),
    )

    assert [row["pool_address"] for row in entries] == ["B"]
    assert any(
        item.pool_address == "A"
        and item.status == "TOKEN_Y_QUOTE_UNAVAILABLE"
        for item in report.items
    )
    assert report.positions_opened == 1
