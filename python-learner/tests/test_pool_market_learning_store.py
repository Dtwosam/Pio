from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meteora_learner.pool_market_learning import (
    build_pool_market_learning_dataset_from_store,
    load_pool_market_history,
)
from meteora_learner.storage import Storage


def _save_history(storage: Storage, address: str, base: float) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(14):
        observed_at = (start + timedelta(hours=index)).isoformat()
        storage.save_pool_snapshot(
            {
                "address": address,
                "tvl": 100_000.0 * (1.0 + 0.01 * index),
                "volume_24h": 20_000.0 * (1.0 + 0.02 * index),
                "fees_24h": 100.0 * (1.0 + 0.01 * index),
                "current_price": base * (1.0 + 0.005 * index),
                "bin_step": 25,
                "token_x": {"symbol": "X", "decimals": 6},
                "token_y": {"symbol": "Y", "decimals": 6},
            },
            observed_at=observed_at,
        )


def test_load_pool_market_history_reads_chronological_snapshots(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_history(storage, "POOL_B", 10.0)
    _save_history(storage, "POOL_A", 100.0)

    frame = load_pool_market_history(str(storage.path))

    assert len(frame) == 28
    assert list(frame.columns) == [
        "pool_address",
        "observed_at",
        "price",
        "tvl_usd",
        "volume_24h_usd",
        "fees_24h_usd",
    ]
    assert frame.iloc[0]["pool_address"] == "POOL_A"
    assert frame.iloc[14]["pool_address"] == "POOL_B"


def test_store_adapter_can_build_learning_examples(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_history(storage, "POOL_A", 100.0)
    _save_history(storage, "POOL_B", 10.0)

    report = build_pool_market_learning_dataset_from_store(
        str(storage.path),
        horizon_rows=3,
        volatility_window=3,
        drawdown_window=4,
        activity_window=3,
    )

    assert report.pools_seen == 2
    assert report.examples_built > 0
    assert {
        item.pool_address for item in report.examples
    } == {"POOL_A", "POOL_B"}


def test_store_adapter_respects_pool_and_time_scope(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_history(storage, "POOL_A", 100.0)
    _save_history(storage, "POOL_B", 10.0)

    frame = load_pool_market_history(
        str(storage.path),
        pool_addresses=("POOL_B",),
        start_observed_at="2026-01-01T04:00:00+00:00",
        end_observed_at="2026-01-01T08:00:00+00:00",
    )

    assert set(frame["pool_address"]) == {"POOL_B"}
    assert len(frame) == 5


def test_same_timestamp_uses_latest_normalized_snapshot(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    timestamp = "2026-01-01T00:00:00+00:00"

    for tvl in (100.0, 200.0):
        storage.save_pool_snapshot(
            {
                "address": "POOL_A",
                "tvl": tvl,
                "volume_24h": 10.0,
                "fees_24h": 1.0,
                "current_price": 1.0,
            },
            observed_at=timestamp,
        )

    frame = load_pool_market_history(str(storage.path))

    assert len(frame) == 1
    assert frame.iloc[0]["tvl_usd"] == 200.0
