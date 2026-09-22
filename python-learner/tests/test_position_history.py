import sqlite3

from meteora_learner.position_history import collect_position_history
from meteora_learner.storage import Storage


class FakeAPI:
    def position_history(self, position_address, *, event_type=None, order_direction=None):
        assert position_address == "position"
        assert event_type is None
        assert order_direction == "asc"
        return {
            "events": [
                {
                    "signature": "sig",
                    "ixIndex": 1,
                    "eventType": "add",
                    "positionAddress": "position",
                    "blockTime": 1700000000,
                    "slot": 100,
                    "poolAddress": "pool",
                    "userAddress": "user",
                    "tokenX": "x",
                    "tokenY": "y",
                    "amountX": "10",
                    "amountY": "20",
                    "amountXUsd": "1.0",
                    "amountYUsd": "2.0",
                    "totalUsd": "3.0",
                    "createdAt": "2026-09-22T00:00:00Z",
                }
            ]
        }


def test_collect_position_history_persists_raw_and_normalized_rows(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    result = collect_position_history(storage, FakeAPI(), "position")

    assert result.position_address == "position"
    assert result.events == 1

    conn = sqlite3.connect(storage.path)
    try:
        event = conn.execute(
            """
            SELECT signature, ix_index, event_type, amount_x, amount_y
            FROM position_event_history
            """
        ).fetchone()
        raw = conn.execute(
            """
            SELECT endpoint, entity_key
            FROM raw_api_observations
            """
        ).fetchone()
    finally:
        conn.close()

    assert event == ("sig", 1, "add", "10", "20")
    assert raw == ("/positions/position/historical", "position")
    assert storage.data_status()["position_event_history"] == 1


def test_position_event_upsert_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    collect_position_history(storage, FakeAPI(), "position")
    collect_position_history(storage, FakeAPI(), "position")

    conn = sqlite3.connect(storage.path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM position_event_history").fetchone()[0]
    finally:
        conn.close()

    assert count == 1
