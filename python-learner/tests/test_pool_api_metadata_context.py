from __future__ import annotations

import pandas as pd

from meteora_learner.pool_api_metadata_context import (
    attach_pool_api_metadata_from_store,
)
from meteora_learner.storage import Storage


def _save_snapshot(
    storage: Storage,
    observed_at: str,
    *,
    apr: float | None = 12.0,
    dynamic_fee: float | None = 0.2,
) -> None:
    storage.save_pool_snapshot(
        {
            "address": "POOL",
            "name": "POOL",
            "tvl": 100_000.0,
            "volume_24h": 20_000.0,
            "fees_24h": 100.0,
            "current_price": 1.0,
            "bin_step": 25,
            "active_bin_id": 0,
            "apr": apr,
            "apy": 13.0,
            "dynamic_fee_pct": dynamic_fee,
            "base_fee_pct": 0.1,
            "max_fee_pct": 1.0,
            "protocol_fee_pct": 0.05,
            "collect_fee_mode": 0,
            "is_blacklisted": False,
            "pool_created_at": "2025-12-01T00:00:00+00:00",
            "token_x": {
                "symbol": "X",
                "decimals": 6,
            },
            "token_y": {
                "symbol": "Y",
                "decimals": 9,
            },
        },
        observed_at=observed_at,
    )


def test_api_metadata_uses_latest_prior_snapshot(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_snapshot(
        storage,
        "2026-01-01T04:00:00+00:00",
        apr=12.0,
    )
    _save_snapshot(
        storage,
        "2026-01-01T06:00:00+00:00",
        apr=99.0,
    )

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T05:00:00Z",
            }
        ]
    )

    enriched, report = attach_pool_api_metadata_from_store(
        str(storage.path),
        decisions,
    )
    row = enriched.iloc[0]

    assert row["api_apr"] == 12.0
    assert row["api_snapshot_age_seconds"] == 3600.0
    assert row["api_pool_age_seconds"] > 0.0
    assert row["api_bin_step"] == 25.0
    assert row["api_dynamic_fee_pct"] == 0.2
    assert row["api_is_blacklisted"] == 0.0
    assert row["api_token_x_decimals"] == 6.0
    assert row["api_token_y_decimals"] == 9.0
    assert report.rows_with_snapshot == 1
    assert report.rows_with_complete_metadata == 1


def test_future_api_snapshot_cannot_leak_backward(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_snapshot(
        storage,
        "2026-01-01T06:00:00+00:00",
        apr=99.0,
    )
    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T05:00:00Z",
            }
        ]
    )

    enriched, report = attach_pool_api_metadata_from_store(
        str(storage.path),
        decisions,
    )

    assert pd.isna(enriched.iloc[0]["api_apr"])
    assert report.rows_without_snapshot == 1


def test_incomplete_api_metadata_is_reported_not_imputed(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_snapshot(
        storage,
        "2026-01-01T04:00:00+00:00",
        apr=None,
        dynamic_fee=None,
    )
    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T05:00:00Z",
            }
        ]
    )

    enriched, report = attach_pool_api_metadata_from_store(
        str(storage.path),
        decisions,
    )

    assert pd.isna(enriched.iloc[0]["api_apr"])
    assert pd.isna(enriched.iloc[0]["api_dynamic_fee_pct"])
    assert report.rows_with_incomplete_metadata == 1


def test_missing_pool_snapshot_is_retained_as_missing_context(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    decisions = pd.DataFrame(
        [
            {
                "pool_address": "UNKNOWN",
                "decision_observed_at": "2026-01-01T05:00:00Z",
            }
        ]
    )

    enriched, report = attach_pool_api_metadata_from_store(
        str(storage.path),
        decisions,
    )

    assert pd.isna(enriched.iloc[0]["api_snapshot_age_seconds"])
    assert report.rows_without_snapshot == 1
