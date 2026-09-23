from meteora_learner.quote_registry import (
    load_fresh_quote_map,
    required_paper_quote_mints,
    save_token_quote,
    token_quote_status,
)
from meteora_learner.storage import Storage


def test_quote_registry_persists_and_checks_freshness(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    saved = save_token_quote(
        storage,
        token_mint="mint",
        quote_per_atomic=0.000001,
        source="TEST",
        observed_at="2026-09-23T10:00:00+00:00",
    )
    assert saved.quote_per_atomic == 0.000001

    fresh = token_quote_status(
        storage,
        token_mint="mint",
        max_age_seconds=300,
        as_of="2026-09-23T10:04:00+00:00",
    )
    assert fresh.available is True
    assert fresh.fresh is True
    assert fresh.age_seconds == 240

    stale = token_quote_status(
        storage,
        token_mint="mint",
        max_age_seconds=300,
        as_of="2026-09-23T10:06:00+00:00",
    )
    assert stale.fresh is False
    assert stale.age_seconds == 360


def test_quote_map_excludes_missing_and_stale_quotes(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_token_quote(
        storage,
        token_mint="fresh",
        quote_per_atomic=1.0,
        source="TEST",
        observed_at="2026-09-23T10:00:00+00:00",
    )
    save_token_quote(
        storage,
        token_mint="stale",
        quote_per_atomic=2.0,
        source="TEST",
        observed_at="2026-09-23T09:00:00+00:00",
    )

    quotes, statuses = load_fresh_quote_map(
        storage,
        token_mints=("fresh", "stale", "missing"),
        max_age_seconds=300,
        as_of="2026-09-23T10:02:00+00:00",
    )

    assert quotes == {"fresh": 1.0}
    by_mint = {item.token_mint: item for item in statuses}
    assert by_mint["stale"].fresh is False
    assert by_mint["missing"].available is False


def test_quote_registry_rejects_non_positive_value(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    import pytest
    with pytest.raises(ValueError, match="positive"):
        save_token_quote(
            storage,
            token_mint="mint",
            quote_per_atomic=0.0,
            source="TEST",
        )


def test_quote_registry_does_not_look_ahead(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_token_quote(
        storage,
        token_mint="mint",
        quote_per_atomic=1.0,
        source="PAST",
        observed_at="2026-09-23T10:00:00+00:00",
    )
    save_token_quote(
        storage,
        token_mint="mint",
        quote_per_atomic=2.0,
        source="FUTURE",
        observed_at="2026-09-23T10:10:00+00:00",
    )

    status = token_quote_status(
        storage,
        token_mint="mint",
        max_age_seconds=300,
        as_of="2026-09-23T10:04:00+00:00",
    )

    assert status.fresh is True
    assert status.quote_per_atomic == 1.0
    assert status.source == "PAST"
    assert status.observed_at == "2026-09-23T10:00:00+00:00"


def test_required_paper_quotes_include_external_rewards_only(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_accounts(
                account_id, created_at, updated_at,
                starting_equity_quote, cash_quote, high_water_equity_quote
            ) VALUES ('paper', 't', 't', '1000', '900', '1000')
            """
        )
        conn.execute(
            """
            INSERT INTO paper_positions(
                position_id, account_id, pool_address, status, policy_source,
                strategy, min_bin_id, max_bin_id, opened_at,
                entry_capital_quote, entry_cost_quote, current_mark_quote,
                fee_income_quote, reward_income_quote, rebalance_cost_quote,
                exit_cost_quote, rebalances, raw_json
            ) VALUES (
                'pos', 'paper', 'pool', 'OPEN', 'DETERMINISTIC',
                'SPOT', 0, 0, 't',
                '100', '0', '100', '0', '0', '0', '0', 0, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_counterfactual_positions(
                position_id, pool_address, entry_observed_at,
                amount_x_atomic, amount_y_atomic, idle_x_atomic, idle_y_atomic,
                entry_price_q64, entry_value_y_atomic, capital_quote,
                max_share_bps, favor_x_active, token_x_mint, token_y_mint,
                reward_mint_0, reward_mint_1, initial_state_json
            ) VALUES (
                'pos', 'pool', 't', '0', '100', '0', '0',
                '1', '100', '100', 500, 0,
                'x', 'y', 'reward', 'x', '{"bins":[]}'
            )
            """
        )

    assert required_paper_quote_mints(
        storage,
        account_id="paper",
    ) == ("reward", "y")
