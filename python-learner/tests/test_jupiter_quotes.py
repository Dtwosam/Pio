from meteora_learner.jupiter_quotes import (
    JupiterTokenUSDQuote,
    refresh_open_paper_jupiter_quotes,
)
from meteora_learner.paper_account import create_paper_account, open_paper_position
from meteora_learner.quote_registry import token_quote_status
from meteora_learner.storage import Storage


def seed_binding(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_counterfactual_positions(
                position_id, pool_address, entry_observed_at,
                amount_x_atomic, amount_y_atomic, idle_x_atomic, idle_y_atomic,
                entry_price_q64, entry_value_y_atomic, capital_quote,
                max_share_bps, favor_x_active, token_x_mint, token_y_mint,
                reward_mint_0, reward_mint_1, initial_state_json
            ) VALUES (
                'pos', 'pool', '2026-09-23T10:00:00+00:00',
                '0', '100', '0', '0', '1', '100', '100',
                500, 0, 'x', 'y', 'x', 'y', '{"bins":[]}'
            )
            """
        )


def seed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    open_paper_position(
        storage,
        event_key="enter",
        account_id="paper",
        position_id="pos",
        pool_address="pool",
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=0,
        max_bin_id=0,
        capital_quote=100,
    )
    seed_binding(storage)
    return storage


def test_jupiter_refresh_persists_usd_per_atomic_quote(tmp_path):
    storage = seed(tmp_path)

    def fetch(mint):
        return JupiterTokenUSDQuote(
            token_mint=mint,
            symbol="Y",
            decimals=6,
            usd_price=1.0,
            usd_per_atomic=0.000001,
        )

    report = refresh_open_paper_jupiter_quotes(
        storage,
        account_id="paper",
        observed_at="2026-09-23T10:05:00+00:00",
        fetch_quote=fetch,
    )

    assert report.quotes_refreshed == 1
    status = token_quote_status(
        storage,
        token_mint="y",
        max_age_seconds=60,
        as_of="2026-09-23T10:05:30+00:00",
    )
    assert status.fresh is True
    assert status.quote_per_atomic == 0.000001
    assert status.source == "JUPITER_TOKENS_V2_USD"


def test_jupiter_refresh_fails_mint_without_inserting_fallback(tmp_path):
    storage = seed(tmp_path)

    def fetch(mint):
        raise ValueError("Jupiter has no USD price for token")

    report = refresh_open_paper_jupiter_quotes(
        storage,
        account_id="paper",
        fetch_quote=fetch,
    )
    assert report.quotes_failed == 1

    status = token_quote_status(storage, token_mint="y")
    assert status.available is False


def test_jupiter_refresh_includes_external_reward_mint(tmp_path):
    storage = seed(tmp_path)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE paper_counterfactual_positions
            SET reward_mint_0 = 'reward'
            WHERE position_id = 'pos'
            """
        )

    requested = []

    def fetch(mint):
        requested.append(mint)
        return JupiterTokenUSDQuote(
            token_mint=mint,
            symbol=mint.upper(),
            decimals=6,
            usd_price=1.0,
            usd_per_atomic=0.000001,
        )

    report = refresh_open_paper_jupiter_quotes(
        storage,
        account_id="paper",
        observed_at="2026-09-23T10:05:00+00:00",
        fetch_quote=fetch,
    )

    assert report.mints_requested == 2
    assert report.quotes_refreshed == 2
    assert requested == ["reward", "y"]
    assert token_quote_status(
        storage,
        token_mint="reward",
        max_age_seconds=60,
        as_of="2026-09-23T10:05:30+00:00",
    ).fresh is True
