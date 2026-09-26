from __future__ import annotations

import pandas as pd
import pytest

from meteora_learner.pool_lp_learning import (
    MARKET_CONTEXT_FEATURE_COLUMNS,
    enrich_lp_examples_with_market_state,
)


def _market() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for pool, base in (("A", 10.0), ("B", 20.0)):
        for index in range(10):
            rows.append(
                {
                    "pool_address": pool,
                    "observed_at": start
                    + pd.Timedelta(hours=index),
                    "price": base + index,
                    "tvl_usd": 100_000.0 + 1_000.0 * index,
                    "volume_24h_usd": 20_000.0 + 500.0 * index,
                    "fees_24h_usd": 100.0 + index,
                }
            )
    return pd.DataFrame(rows)


def test_enrichment_uses_latest_prior_snapshot_only() -> None:
    lp = pd.DataFrame(
        [
            {
                "pool_address": "A",
                "decision_observed_at": "2026-01-01T05:30:00Z",
                "target_net_return_bps": 123,
            }
        ]
    )

    enriched, report = enrich_lp_examples_with_market_state(
        lp,
        _market(),
        volatility_window=3,
        drawdown_window=3,
        activity_window=3,
    )

    row = enriched.iloc[0]
    assert row["market_observed_at"] == pd.Timestamp(
        "2026-01-01T05:00:00Z"
    )
    assert row["market_price"] == 15.0
    assert row["market_snapshot_age_seconds"] == 1800.0
    assert report.rows_matched == 1
    assert report.rows_unmatched == 0


def test_future_market_change_cannot_leak_into_prior_decision() -> None:
    lp = pd.DataFrame(
        [
            {
                "pool_address": "A",
                "decision_observed_at": "2026-01-01T05:30:00Z",
            }
        ]
    )
    market = _market()

    first, _ = enrich_lp_examples_with_market_state(
        lp,
        market,
        volatility_window=3,
        drawdown_window=3,
        activity_window=3,
    )

    future = (
        (market["pool_address"] == "A")
        & (
            pd.to_datetime(market["observed_at"], utc=True)
            > pd.Timestamp("2026-01-01T05:30:00Z")
        )
    )
    altered = market.copy()
    altered.loc[future, "price"] *= 0.01
    altered.loc[future, "tvl_usd"] *= 0.01

    second, _ = enrich_lp_examples_with_market_state(
        lp,
        altered,
        volatility_window=3,
        drawdown_window=3,
        activity_window=3,
    )

    for column in MARKET_CONTEXT_FEATURE_COLUMNS:
        assert first.iloc[0][column] == pytest.approx(
            second.iloc[0][column]
        )


def test_enrichment_never_crosses_pool_boundaries() -> None:
    lp = pd.DataFrame(
        [
            {
                "pool_address": "B",
                "decision_observed_at": "2026-01-01T05:30:00Z",
            }
        ]
    )

    enriched, _ = enrich_lp_examples_with_market_state(
        lp,
        _market(),
        volatility_window=3,
        drawdown_window=3,
        activity_window=3,
    )

    assert enriched.iloc[0]["market_price"] == 25.0


def test_unmatched_lp_row_is_retained_and_reported() -> None:
    lp = pd.DataFrame(
        [
            {
                "pool_address": "C",
                "decision_observed_at": "2026-01-01T05:30:00Z",
            }
        ]
    )

    enriched, report = enrich_lp_examples_with_market_state(
        lp,
        _market(),
        volatility_window=3,
        drawdown_window=3,
        activity_window=3,
    )

    assert len(enriched) == 1
    assert pd.isna(enriched.iloc[0]["market_observed_at"])
    assert report.rows_matched == 0
    assert report.rows_unmatched == 1


def test_decision_before_first_snapshot_cannot_use_future() -> None:
    lp = pd.DataFrame(
        [
            {
                "pool_address": "A",
                "decision_observed_at": "2025-12-31T23:00:00Z",
            }
        ]
    )

    enriched, report = enrich_lp_examples_with_market_state(
        lp,
        _market(),
        volatility_window=3,
        drawdown_window=3,
        activity_window=3,
    )

    assert pd.isna(enriched.iloc[0]["market_observed_at"])
    assert report.rows_unmatched == 1
