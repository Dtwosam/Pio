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
