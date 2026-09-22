from __future__ import annotations

import sqlite3

import pytest

from meteora_learner.dlmm_math import bin_price
from meteora_learner.storage import Storage


def test_raw_and_current_meteora_pool_shape_are_persisted(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    observed = "2026-09-22T00:00:00+00:00"
    expected_active = 123
    price = float(bin_price(expected_active, 25, token_x_decimals=6, token_y_decimals=6))

    storage.save_raw("/pools", {"data": [{"address": "abc"}]}, observed_at=observed)
    assert storage.save_pool_snapshot(
        {
            "address": "abc",
            "name": "A-B",
            "tvl": 123.4,
            "volume": {"24h": 50},
            "fees": {"24h": 2},
            "current_price": price,
            "pool_config": {
                "bin_step": 25,
                "base_fee_pct": 0.2,
                "max_fee_pct": 5.0,
                "protocol_fee_pct": 0.1,
                "collect_fee_mode": 0,
            },
            "dynamic_fee_pct": 0.3,
            "apr": 0.1,
            "apy": 0.2,
            "is_blacklisted": False,
            "created_at": 1_700_000_000,
            "token_x": {"symbol": "A", "decimals": 6},
            "token_y": {"symbol": "B", "decimals": 6},
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
                "protocol_fees": 0.15,
                "observed_at": observed,
                "raw": {"fees": 1.5, "protocol_fees": 0.15},
            }
        ]
    ) == 1

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            """
            SELECT address, tvl, volume_24h, fees_24h, current_price, bin_step,
                   active_bin_id, apr, apy, token_x_symbol, token_y_symbol,
                   token_x_decimals, token_y_decimals, dynamic_fee_pct,
                   base_fee_pct, max_fee_pct, protocol_fee_pct, collect_fee_mode,
                   is_blacklisted, pool_created_at
            FROM pool_snapshots
            """
        ).fetchone()
        assert row[:4] == ("abc", 123.4, 50.0, 2.0)
        assert row[4] == pytest.approx(price)
        assert row[5:] == (
            25, expected_active, 0.1, 0.2, "A", "B", 6, 6, 0.3,
            0.2, 5.0, 0.1, 0, 0, 1_700_000_000
        )
        volume = conn.execute(
            "SELECT volume, fees, protocol_fees FROM volume_buckets"
        ).fetchone()
        assert volume == (100.0, 1.5, 0.15)
    finally:
        conn.close()


def test_existing_database_is_migrated_with_new_columns(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
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
        );
        CREATE TABLE volume_buckets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pool_address TEXT NOT NULL,
            source TEXT NOT NULL,
            bucket_time TEXT NOT NULL,
            volume REAL,
            fees REAL,
            observed_at TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            UNIQUE(pool_address, source, bucket_time)
        );
        """
    )
    conn.commit()
    conn.close()

    Storage(db)

    conn = sqlite3.connect(db)
    try:
        pool_columns = {row[1] for row in conn.execute("PRAGMA table_info(pool_snapshots)").fetchall()}
        volume_columns = {row[1] for row in conn.execute("PRAGMA table_info(volume_buckets)").fetchall()}
    finally:
        conn.close()

    assert {
        "current_price", "bin_step", "active_bin_id", "apr", "apy",
        "token_x_decimals", "token_y_decimals", "dynamic_fee_pct",
        "base_fee_pct", "max_fee_pct", "protocol_fee_pct",
        "collect_fee_mode", "is_blacklisted", "pool_created_at"
    } <= pool_columns
    assert "protocol_fees" in volume_columns
