from __future__ import annotations

from meteora_learner.pool_market_data_quality import (
    build_pool_market_coverage_report,
)
from meteora_learner.storage import Storage


def _save(
    storage: Storage,
    address: str,
    observed_at: str,
    *,
    price=1.0,
    tvl=100.0,
    volume=20.0,
    fees=1.0,
) -> None:
    storage.save_pool_snapshot(
        {
            "address": address,
            "current_price": price,
            "tvl": tvl,
            "volume_24h": volume,
            "fees_24h": fees,
        },
        observed_at=observed_at,
    )


def test_coverage_report_exposes_history_and_gaps(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save(
        storage,
        "A",
        "2026-01-01T00:00:00+00:00",
    )
    _save(
        storage,
        "A",
        "2026-01-01T01:00:00+00:00",
    )
    _save(
        storage,
        "A",
        "2026-01-01T03:00:00+00:00",
    )
    _save(
        storage,
        "B",
        "2026-01-01T00:30:00+00:00",
    )

    report = build_pool_market_coverage_report(
        str(storage.path)
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.pools_seen == 2
    assert report.observations_seen == 4

    a = next(
        item for item in report.coverage
        if item.pool_address == "A"
    )
    assert a.observations == 3
    assert a.median_interval_seconds == 5400.0
    assert a.max_gap_seconds == 7200.0

    b = next(
        item for item in report.coverage
        if item.pool_address == "B"
    )
    assert b.observations == 1
    assert b.median_interval_seconds is None
    assert b.max_gap_seconds is None


def test_coverage_counts_missing_economic_observations(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save(
        storage,
        "A",
        "2026-01-01T00:00:00+00:00",
    )
    _save(
        storage,
        "A",
        "2026-01-01T01:00:00+00:00",
        price=None,
        tvl=None,
        volume=None,
        fees=None,
    )

    report = build_pool_market_coverage_report(
        str(storage.path)
    )
    item = report.coverage[0]

    assert item.observations == 2
    assert item.valid_price_observations == 1
    assert item.valid_tvl_observations == 1
    assert item.valid_volume_observations == 1
    assert item.valid_fee_observations == 1


def test_empty_coverage_is_descriptive_not_failure(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = build_pool_market_coverage_report(
        str(storage.path)
    )

    assert report.pools_seen == 0
    assert report.observations_seen == 0
    assert report.coverage == ()
