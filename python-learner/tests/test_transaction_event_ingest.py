import sqlite3

from meteora_learner.storage import Storage
from meteora_learner.transaction_event_ingest import ingest_transaction_events


def snapshot():
    return {
        "signature": "sig",
        "slot": 123,
        "block_time": 1700000000,
        "events": [
            {
                "event_index": 0,
                "parent_ix_index": 4,
                "event": {
                    "event_type": "AddLiquidity",
                    "event": {
                        "lb_pair": "pool",
                        "from": "user",
                        "position": "position",
                        "amount_x": "10",
                        "amount_y": "20",
                        "active_bin_id": 7,
                    },
                },
            },
            {
                "event_index": 1,
                "parent_ix_index": 4,
                "event": {
                    "event_type": "CompositionFee",
                    "event": {
                        "from": "user",
                        "bin_id": 7,
                        "token_x_fee_amount": "1",
                        "token_y_fee_amount": "2",
                        "protocol_token_x_fee_amount": "0",
                        "protocol_token_y_fee_amount": "1",
                    },
                },
            },
        ],
    }


def test_transaction_event_snapshot_is_persisted(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    result = ingest_transaction_events(
        storage,
        snapshot(),
        observed_at="2026-09-23T00:00:00+00:00",
    )
    assert result.signature == "sig"
    assert result.events == 2

    conn = sqlite3.connect(storage.path)
    try:
        rows = conn.execute(
            """
            SELECT event_type, parent_ix_index, position_address, active_bin_id,
                   amount_x, token_y_fee_amount
            FROM chain_transaction_events
            ORDER BY event_index
            """
        ).fetchall()
    finally:
        conn.close()

    assert rows == [
        ("AddLiquidity", 4, "position", 7, "10", None),
        ("CompositionFee", 4, None, None, None, "2"),
    ]
    assert storage.data_status()["chain_transaction_events"] == 2


def test_transaction_event_ingest_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    ingest_transaction_events(storage, snapshot())
    ingest_transaction_events(storage, snapshot())

    conn = sqlite3.connect(storage.path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM chain_transaction_events"
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 2



def test_rebalance_lifecycle_event_fields_are_persisted(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    payload = {
        "signature": "rebalance-sig",
        "slot": 999,
        "block_time": 1700000100,
        "network_fee_lamports": 9000,
        "compute_units_consumed": 250000,
        "succeeded": True,
        "events": [
            {
                "event_index": 0,
                "parent_ix_index": 3,
                "event": {
                    "event_type": "Rebalancing",
                    "event": {
                        "lb_pair": "pool",
                        "position": "position",
                        "owner": "owner",
                        "active_bin_id": 12,
                        "x_withdrawn_amount": "10",
                        "x_added_amount": "8",
                        "y_withdrawn_amount": "20",
                        "y_added_amount": "18",
                        "x_fee_amount": "1",
                        "y_fee_amount": "2",
                        "old_min_id": 1,
                        "old_max_id": 5,
                        "new_min_id": 10,
                        "new_max_id": 14,
                        "reward_one": "3",
                        "reward_two": "4",
                    },
                },
            }
        ],
    }

    result = ingest_transaction_events(storage, payload)
    assert result.events == 1

    conn = sqlite3.connect(storage.path)
    try:
        row = conn.execute(
            """
            SELECT event_type, position_address, owner_address,
                   x_withdrawn_amount, x_added_amount,
                   old_min_id, new_max_id, reward_one
            FROM chain_transaction_events
            WHERE signature = 'rebalance-sig'
            """
        ).fetchone()
        receipt = conn.execute(
            """
            SELECT network_fee_lamports, compute_units_consumed, succeeded
            FROM chain_transaction_snapshots
            WHERE signature = 'rebalance-sig'
            """
        ).fetchone()
    finally:
        conn.close()

    assert row == (
        "Rebalancing", "position", "owner",
        "10", "8", 1, 14, "3",
    )
    assert receipt == (9000, 250000, 1)
