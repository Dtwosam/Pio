from __future__ import annotations

import sqlite3

from meteora_learner.storage import Storage


def test_raw_and_pool_snapshot_are_persisted(tmp_path):
    db = tmp_path / "pio.db"
    storage = Storage(db)
    storage.save_raw("/pools", {"data": [{"address": "abc"}]}, observed_at="2026-09-22T00:00:00+00:00")
    assert storage.save_pool_snapshot(
        {"address": "abc", "name": "A-B", "tvl": 123.4, "volume_24h": 50, "fees_24h": 2},
        observed_at="2026-09-22T00:00:00+00:00",
    )

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM raw_api_observations").fetchone()[0] == 1
        row = conn.execute("SELECT address, tvl, volume_24h, fees_24h FROM pool_snapshots").fetchone()
        assert row == ("abc", 123.4, 50.0, 2.0)
    finally:
        conn.close()
