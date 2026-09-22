from __future__ import annotations

import sqlite3

from meteora_learner.storage import Storage


def test_raw_and_normalized_market_data_are_persisted(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    observed = "2026-09-22T00:00:00+00:00"

    storage.save_raw("/pools", {"data": [{"address": "abc"}]}, observed_at=observed)
    assert storage.save_pool_snapshot(
        {"address": "abc", "name": "A-B", "tvl": 123.4, "volume_24h": 50, "fees_24h": 2},
        observed_at=observed,
    )
    assert storage.save_ohlcv_candles(
        [
            {
                "pool_address": "abc",
                "source": "meteora",
                "candle_time": observed,
                "resolution": "5m",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 42.0,
                "observed_at": observed,
                "raw": {"close": 10.5},
            }
        ]
    ) == 1
    assert storage.save_volume_buckets(
        [
            {
                "pool_address": "abc",
                "source": "meteora",
                "bucket_time": observed,
                "volume": 100.0,
                "fees": 1.5,
                "observed_at": observed,
                "raw": {"fees": 1.5},
            }
        ]
    ) == 1

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM raw_api_observations").fetchone()[0] == 1
        row = conn.execute("SELECT address, tvl, volume_24h, fees_24h FROM pool_snapshots").fetchone()
        assert row == ("abc", 123.4, 50.0, 2.0)
        assert conn.execute("SELECT COUNT(*) FROM ohlcv_candles").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM volume_buckets").fetchone()[0] == 1
    finally:
        conn.close()

    status = storage.data_status()
    assert status["ohlcv_candles"] == 1
    assert status["volume_buckets"] == 1
