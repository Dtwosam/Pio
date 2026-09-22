import sqlite3

import pytest

from meteora_learner.chain_ingest import ingest_chain_snapshot
from meteora_learner.storage import Storage


def snapshot():
    return {
        "pool_address": "pool",
        "active_bin_id": 100,
        "bin_step": 25,
        "token_x_mint": "x",
        "token_y_mint": "y",
        "bin_arrays": [
            {
                "address": "array",
                "index": 1,
                "lower_bin_id": 70,
                "upper_bin_id": 139,
                "bins": [
                    {
                        "bin_id": 100,
                        "price": "18446744073709551616",
                        "amount_x": "10",
                        "amount_y": "20",
                        "liquidity_supply": "30",
                        "fee_amount_x_per_token_stored": "40",
                        "fee_amount_y_per_token_stored": "50",
                    }
                ],
            }
        ],
    }


def test_ingest_chain_snapshot_persists_pool_and_bins(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    result = ingest_chain_snapshot(
        storage,
        snapshot(),
        observed_at="2026-09-22T10:00:00+00:00",
    )

    assert result.pool_address == "pool"
    assert result.bin_arrays == 1
    assert result.bins == 1

    conn = sqlite3.connect(db)
    try:
        pool = conn.execute(
            "SELECT pool_address, active_bin_id, bin_step FROM chain_pool_snapshots"
        ).fetchone()
        bin_row = conn.execute(
            "SELECT bin_id, price, amount_x, amount_y, liquidity_supply FROM bin_liquidity_snapshots"
        ).fetchone()
    finally:
        conn.close()

    assert pool == ("pool", 100, 25)
    assert bin_row == (100, "18446744073709551616", "10", "20", "30")
    assert storage.data_status()["bin_liquidity_snapshots"] == 1


def test_ingest_chain_snapshot_rejects_bad_shape(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with pytest.raises(ValueError):
        ingest_chain_snapshot(storage, {"pool_address": "pool"})
