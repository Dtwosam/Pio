import sqlite3

import pytest

from meteora_learner.position_ingest import ingest_position_snapshot
from meteora_learner.storage import Storage


def position_snapshot():
    return {
        "position_address": "position",
        "pool_address": "pool",
        "owner": "owner",
        "fee_owner": "owner",
        "lower_bin_id": 99,
        "upper_bin_id": 101,
        "total_x_amount": "10",
        "total_y_amount": "20",
        "fee_x": "3",
        "fee_y": "4",
        "reward_one": "5",
        "reward_two": "6",
        "last_updated_at": 1700000000,
        "total_claimed_fee_x_amount": "7",
        "total_claimed_fee_y_amount": "8",
        "bins": [
            {
                "bin_id": 100,
                "price": "123",
                "bin_x_amount": "1000",
                "bin_y_amount": "2000",
                "bin_liquidity": "3000",
                "position_liquidity": "300",
                "position_x_amount": "100",
                "position_y_amount": "200",
                "position_fee_x_amount": "3",
                "position_fee_y_amount": "4",
                "position_reward_amounts": ["5", "6"],
            }
        ],
    }


def test_position_snapshot_is_persisted(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    result = ingest_position_snapshot(
        storage,
        position_snapshot(),
        observed_at="2026-09-22T12:00:00+00:00",
    )
    assert result.position_address == "position"
    assert result.pool_address == "pool"
    assert result.bins == 1

    conn = sqlite3.connect(storage.path)
    try:
        position = conn.execute(
            "SELECT position_address, pool_address, fee_x, fee_y FROM chain_position_snapshots"
        ).fetchone()
        bin_row = conn.execute(
            """
            SELECT bin_id, bin_liquidity, position_liquidity,
                   position_fee_x_amount, position_fee_y_amount
            FROM position_bin_snapshots
            """
        ).fetchone()
    finally:
        conn.close()

    assert position == ("position", "pool", "3", "4")
    assert bin_row == (100, "3000", "300", "3", "4")
    assert storage.data_status()["position_bin_snapshots"] == 1


def test_position_snapshot_rejects_inverted_range(tmp_path):
    payload = position_snapshot()
    payload["lower_bin_id"] = 102
    with pytest.raises(ValueError):
        ingest_position_snapshot(Storage(tmp_path / "pio.db"), payload)
