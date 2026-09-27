from meteora_learner.jupiter_quotes import JupiterTokenUSDQuote
from meteora_learner.phase2_research_quotes import (
    WRAPPED_SOL_MINT,
    collect_phase2_research_quotes,
    required_pool_research_quote_mints,
)
from meteora_learner.quote_registry import token_quote_status
from meteora_learner.storage import Storage


POOL = "pool"
X = "x-mint"
Y = "y-mint"
R = "reward-mint"
DEFAULT = "11111111111111111111111111111111"


def save_pool(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint,
                reward_mint_0, reward_mint_1, raw_json
            ) VALUES (
                '2026-09-26T16:00:00+00:00',
                ?, 10, 25, ?, ?, ?, ?, '{}'
            )
            """,
            (POOL, X, Y, R, DEFAULT),
        )


def quote(mint, value=0.000001):
    return JupiterTokenUSDQuote(
        token_mint=mint,
        symbol=None,
        decimals=6,
        usd_price=value * 1_000_000,
        usd_per_atomic=value,
    )


def test_required_research_mints_include_pool_rewards_and_wsol(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage)

    mints = required_pool_research_quote_mints(
        storage,
        pool_address=POOL,
    )

    assert set(mints) == {X, Y, R, WRAPPED_SOL_MINT}
    assert DEFAULT not in mints


def test_quote_observer_persists_every_successful_research_quote(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage)

    report = collect_phase2_research_quotes(
        storage,
        pool_address=POOL,
        observed_at="2026-09-26T16:05:00+00:00",
        fetch_quote=lambda mint: quote(mint),
    )

    assert report.mints_requested == 4
    assert report.quotes_refreshed == 4
    assert report.quotes_failed == 0
    assert {
        item.observed_at for item in report.items
    } == {"2026-09-26T16:05:00+00:00"}
    for mint in (X, Y, R, WRAPPED_SOL_MINT):
        status = token_quote_status(
            storage,
            token_mint=mint,
            max_age_seconds=300,
            as_of="2026-09-26T16:05:00+00:00",
        )
        assert status.available is True
        assert status.fresh is True
        assert status.source == "JUPITER_TOKENS_V2_USD"


def test_quote_observer_keeps_successes_when_one_mint_fails(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage)

    def fetch(mint):
        if mint == R:
            raise RuntimeError("missing price")
        return quote(mint)

    report = collect_phase2_research_quotes(
        storage,
        pool_address=POOL,
        observed_at="2026-09-26T16:05:00+00:00",
        fetch_quote=fetch,
    )

    assert report.quotes_refreshed == 3
    assert report.quotes_failed == 1
    failed = [item for item in report.items if item.status == "FAILED"]
    assert len(failed) == 1
    assert failed[0].token_mint == R


def test_quote_observer_rejects_wrong_mint_response(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage)

    report = collect_phase2_research_quotes(
        storage,
        pool_address=POOL,
        observed_at="2026-09-26T16:05:00+00:00",
        fetch_quote=lambda mint: quote("wrong-mint"),
    )

    assert report.quotes_refreshed == 0
    assert report.quotes_failed == 4
    assert all(
        item.error == "QUOTE_MINT_MISMATCH"
        for item in report.items
    )



def test_quote_observer_does_not_backdate_later_network_responses(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage)
    mints = required_pool_research_quote_mints(
        storage,
        pool_address=POOL,
    )

    timestamps = iter(
        (
            "2026-09-26T16:05:00+00:00",
            "2026-09-26T16:06:00+00:00",
            "2026-09-26T16:07:00+00:00",
            "2026-09-26T16:08:00+00:00",
            "2026-09-26T16:09:00+00:00",
        )
    )
    report = collect_phase2_research_quotes(
        storage,
        pool_address=POOL,
        fetch_quote=lambda mint: quote(mint),
        now=lambda: next(timestamps),
    )

    assert report.observed_at == "2026-09-26T16:05:00+00:00"
    assert [item.token_mint for item in report.items] == list(mints)
    assert [item.observed_at for item in report.items] == [
        "2026-09-26T16:06:00+00:00",
        "2026-09-26T16:07:00+00:00",
        "2026-09-26T16:08:00+00:00",
        "2026-09-26T16:09:00+00:00",
    ]

    first = token_quote_status(
        storage,
        token_mint=mints[0],
        max_age_seconds=300,
        as_of="2026-09-26T16:06:30+00:00",
    )
    second = token_quote_status(
        storage,
        token_mint=mints[1],
        max_age_seconds=300,
        as_of="2026-09-26T16:06:30+00:00",
    )
    assert first.available is True
    assert first.observed_at == "2026-09-26T16:06:00+00:00"
    assert second.available is False



def test_quote_observer_failure_does_not_echo_fetch_exception(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool(storage)
    secret = "https://user:secret@example.invalid/price"

    def fetch(_mint):
        raise RuntimeError(f"request failed at {secret}")

    report = collect_phase2_research_quotes(
        storage,
        pool_address=POOL,
        observed_at="2026-09-26T16:05:00+00:00",
        fetch_quote=fetch,
    )

    assert report.quotes_refreshed == 0
    assert report.quotes_failed == 4
    encoded = str(report.to_record())
    assert secret not in encoded
    assert "request failed" not in encoded
    assert {
        item.error for item in report.items
    } == {"QUOTE_FETCH_FAILED"}
