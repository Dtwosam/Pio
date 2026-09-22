from __future__ import annotations

import sqlite3

from meteora_learner.storage import Storage


def test_raw_and_normalized_market_data_are_persisted(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    observed = "2026-09-22T00:00:00+00:00"

    storage.save_raw("/pools", {"data": [{"address": "abc"}]}, observed_at=observed)
    assert storage.save_pool_snapshot(
        {
            "address": "abc",
            "name": "A-B",
            "tvl": 123.4,
            "volume_24h": 50,
            "fees_24h": 2,
            "current_price": 10.5,
            "bin_step": 25,
            "active_id": 123,
            "apr": 0.1,
            "apy": 0.2,
            "token_x": {"symbol": "A"},
            "token_y": {"symbol": "B"},
        },
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
        row = conn.execute(
            """
            SELECT address, tvl, volume_24h, fees_24h, current_price, bin_step,
                   active_bin_id, apr, apy, token_x_symbol, token_y_symbol
            FROM pool_snapshots
            """
        ).fetchone()
        assert row == ("abc", 123.4, 50.0, 2.0, 10.5, 25, 123, 0.1, 0.2, "A", "B")
        assert conn.execute("SELECT COUNT(*) FROM ohlcv_candles").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM volume_buckets").fetchone()[0] == 1
    finally:
        conn.close()

    status = storage.data_status()
    assert status["ohlcv_candles"] == 1
    assert status["volume_buckets"] == 1


def test_existing_database_is_migrated_with_new_pool_columns(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE pool_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            address TEXT NOT NULL,
            name TEXT,
            tvl REAL,
            volume_24h REAL,
            fees_24h REAL,
            raw_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    Storage(db)

    conn = sqlite3.connect(db)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pool_snapshots)").fetchall()}
    finally:
        conn.close()

    assert {"current_price", "bin_step", "active_bin_id", "apr", "apy"} <= columns
