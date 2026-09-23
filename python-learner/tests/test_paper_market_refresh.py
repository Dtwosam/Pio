from meteora_learner.paper_account import create_paper_account, open_paper_position
from meteora_learner.paper_market_refresh import refresh_paper_market_state
from meteora_learner.storage import Storage


def open_position(storage, position, pool):
    open_paper_position(
        storage,
        event_key=f"{position}-enter",
        account_id="paper",
        position_id=position,
        pool_address=pool,
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=0,
        max_bin_id=0,
        capital_quote=100,
    )


def pool_payload(address):
    return {
        "address": address,
        "name": address,
        "tvl": 100000,
        "volume_24h": 20000,
        "fees_24h": 100,
        "current_price": 1.0,
        "bin_step": 25,
        "active_bin_id": 0,
        "token_x": {"symbol": "X", "decimals": 6},
        "token_y": {"symbol": "Y", "decimals": 6},
        "dynamic_fee_pct": 0.5,
        "is_blacklisted": False,
        "created_at": 1790035200,
    }


def test_market_refresh_deduplicates_open_paper_pools(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    open_position(storage, "a", "pool")
    open_position(storage, "b", "pool")
    calls = []

    def fetch(address):
        calls.append(address)
        return pool_payload(address)

    report = refresh_paper_market_state(
        storage,
        account_id="paper",
        observed_at="2026-09-23T10:00:00+00:00",
        fetch_pool=fetch,
    )

    assert calls == ["pool"]
    assert report.pools_requested == 1
    assert report.pools_refreshed == 1
    assert report.pools_failed == 0
    with storage.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM pool_snapshots WHERE address = 'pool'"
        ).fetchone()[0]
    assert count == 1


def test_market_refresh_isolates_pool_failure(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    open_position(storage, "a", "bad")
    open_position(storage, "b", "good")

    def fetch(address):
        if address == "bad":
            raise RuntimeError("api unavailable")
        return pool_payload(address)

    report = refresh_paper_market_state(
        storage,
        account_id="paper",
        fetch_pool=fetch,
    )

    assert report.pools_requested == 2
    assert report.pools_refreshed == 1
    assert report.pools_failed == 1
    by_pool = {item.pool_address: item for item in report.items}
    assert by_pool["bad"].status == "FAILED"
    assert "api unavailable" in by_pool["bad"].error
