from meteora_learner.research_store import ResearchStore
from meteora_learner.storage import Storage


def test_research_store_reads_latest_snapshot_and_ordered_candles(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)

    storage.save_pool_snapshot(
        {"address": "pool", "current_price": 10, "bin_step": 25, "active_id": 100},
        observed_at="2026-09-22T00:00:00+00:00",
    )
    storage.save_pool_snapshot(
        {"address": "pool", "current_price": 11, "bin_step": 25, "active_id": 110},
        observed_at="2026-09-22T01:00:00+00:00",
    )

    storage.save_ohlcv_candles(
        [
            {
                "pool_address": "pool",
                "source": "meteora",
                "candle_time": "2026-09-22T00:10:00+00:00",
                "resolution": "5m",
                "open": 10.1,
                "high": 10.2,
                "low": 10.0,
                "close": 10.1,
                "volume": 1,
                "observed_at": "2026-09-22T01:00:00+00:00",
                "raw": {},
            },
            {
                "pool_address": "pool",
                "source": "meteora",
                "candle_time": "2026-09-22T00:05:00+00:00",
                "resolution": "5m",
                "open": 10,
                "high": 10.1,
                "low": 9.9,
                "close": 10,
                "volume": 1,
                "observed_at": "2026-09-22T01:00:00+00:00",
                "raw": {},
            },
        ]
    )

    store = ResearchStore(db)
    latest = store.latest_pool_snapshot("pool")
    candles = store.load_ohlcv("pool", limit=10)

    assert latest is not None
    assert latest["current_price"] == 11.0
    assert latest["active_bin_id"] == 110
    assert [row["close"] for row in candles] == [10.0, 10.1]
