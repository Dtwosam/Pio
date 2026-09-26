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



def test_phase9_source_tables_are_append_only(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    observed = "2026-09-23T12:00:00+00:00"
    token_program = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "token_x_program": token_program,
            "token_y_program": token_program,
            "bin_arrays": [],
        },
        observed_at=observed,
    )
    storage.save_token_mint_snapshot(
        {
            "mint_address": "x",
            "token_program": token_program,
            "capture_slot_start": 1,
            "capture_slot_end": 2,
            "supply": "1000",
            "decimals": 6,
            "is_initialized": True,
            "mint_authority": None,
            "freeze_authority": None,
            "data_len": 82,
            "token_2022_extension_data_len": 0,
            "has_token_2022_extension_data": False,
        },
        observed_at=observed,
    )
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO bin_liquidity_snapshots(
                observed_at, pool_address, bin_array_index,
                bin_id, price, amount_x, amount_y,
                liquidity_supply, fee_amount_x_per_token_stored,
                fee_amount_y_per_token_stored
            ) VALUES (?, 'pool', 0, 0, '1', '1', '1', '1', '0', '0')
            """,
            (observed,),
        )
        conn.execute(
            """
            INSERT INTO position_event_history(
                observed_at, position_address, signature, ix_index,
                event_type, block_time, slot, pool_address,
                user_address, token_x, token_y,
                amount_x, amount_y, amount_x_usd, amount_y_usd,
                total_usd, created_at, raw_json
            ) VALUES (
                ?, 'position', 'signature', 0, 'ADD_LIQUIDITY',
                1, 1, 'pool', 'user', 'x', 'y',
                '1', '1', '1', '1', '2', ?, '{}'
            )
            """,
            (observed, observed),
        )

        for table in (
            "chain_pool_snapshots",
            "token_mint_snapshots",
            "bin_liquidity_snapshots",
            "position_event_history",
        ):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="immutable",
            ):
                conn.execute(
                    f"UPDATE {table} SET id = id WHERE id = "
                    f"(SELECT MIN(id) FROM {table})"
                )
            with pytest.raises(
                sqlite3.IntegrityError,
                match="immutable",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE id = "
                    f"(SELECT MIN(id) FROM {table})"
                )



def test_phase2_position_observation_attempt_ledger_is_append_only(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    row_id = storage.save_phase2_position_observation_attempt(
        pool_address="pool",
        position_address="position",
        attempted_at="2026-09-26T15:00:00+00:00",
        succeeded=False,
        failure_category="EXECUTOR_FAILED",
    )
    assert row_id > 0

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT pool_address, position_address, succeeded,
                   failure_category, capture_slot
            FROM phase2_position_observation_attempts
            WHERE id = ?
            """,
            (row_id,),
        ).fetchone()
    assert row == (
        "pool",
        "position",
        0,
        "EXECUTOR_FAILED",
        None,
    )

    status = storage.data_status()
    assert status["phase2_position_observation_attempts"] == 1
    assert status["phase2_position_observation_successes"] == 0
    assert status["phase2_position_observation_failures"] == 1
    assert status["latest_phase2_position_observation_attempt"] == (
        "2026-09-26T15:00:00+00:00"
    )

    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        with storage.connect() as conn:
            conn.execute(
                """
                UPDATE phase2_position_observation_attempts
                SET failure_category = 'OTHER'
                WHERE id = ?
                """,
                (row_id,),
            )

    with pytest.raises(sqlite3.IntegrityError):
        with storage.connect() as conn:
            conn.execute(
                """
                DELETE FROM phase2_position_observation_attempts
                WHERE id = ?
                """,
                (row_id,),
            )


def test_phase2_position_attempt_validation_is_fail_closed(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="requires non-negative capture_slot"):
        storage.save_phase2_position_observation_attempt(
            pool_address="pool",
            position_address="position",
            attempted_at="2026-09-26T15:00:00+00:00",
            succeeded=True,
        )

    with pytest.raises(ValueError, match="requires failure_category"):
        storage.save_phase2_position_observation_attempt(
            pool_address="pool",
            position_address="position",
            attempted_at="2026-09-26T15:00:00+00:00",
            succeeded=False,
        )
