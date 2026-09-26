from __future__ import annotations

import pandas as pd

from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.pool_lp_learning import (
    ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    ENRICHED_LP_FEATURE_COLUMNS,
    build_market_enriched_lp_training_frame,
)


def _lp_row(
    pool: str,
    decision: str,
    forward_end: str,
    *,
    value: float = 1.0,
) -> dict:
    row = {
        "pool_address": pool,
        "decision_observed_at": decision,
        "forward_end_observed_at": forward_end,
        "target_net_return_bps": 100.0 * value,
        "target_excess_vs_hold_bps": 50.0 * value,
        "target_range_survival_ratio": 0.8,
    }
    for index, column in enumerate(ML_FEATURE_COLUMNS):
        row[column] = value + index * 0.01
    return row


def _market() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for index in range(12):
        rows.append(
            {
                "pool_address": "A",
                "observed_at": start
                + pd.Timedelta(hours=index),
                "price": 10.0 + 0.1 * index,
                "tvl_usd": 100_000.0 + 1_000.0 * index,
                "volume_24h_usd": 20_000.0 + 300.0 * index,
                "fees_24h_usd": 100.0 + index,
            }
        )
    return pd.DataFrame(rows)


def test_build_enriched_lp_training_frame_keeps_complete_rows() -> None:
    lp = pd.DataFrame(
        [
            _lp_row(
                "A",
                "2026-01-01T06:30:00Z",
                "2026-01-01T08:30:00Z",
            ),
            _lp_row(
                "A",
                "2026-01-01T07:30:00Z",
                "2026-01-01T09:30:00Z",
                value=2.0,
            ),
        ]
    )

    frame, report = build_market_enriched_lp_training_frame(
        lp,
        _market(),
        volatility_window=3,
        drawdown_window=3,
        activity_window=3,
    )

    assert report.rows_seen == 2
    assert report.rows_ready == 2
    assert report.rows_dropped_unmatched == 0
    assert set(ENRICHED_LP_FEATURE_COLUMNS) <= set(frame.columns)
    assert set(ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS) <= set(
        frame.columns
    )


def test_build_enriched_lp_training_frame_drops_unmatched_market() -> None:
    lp = pd.DataFrame(
        [
            _lp_row(
                "B",
                "2026-01-01T06:30:00Z",
                "2026-01-01T08:30:00Z",
            )
        ]
    )

    frame, report = build_market_enriched_lp_training_frame(
        lp,
        _market(),
        volatility_window=3,
        drawdown_window=3,
        activity_window=3,
    )

    assert frame.empty
    assert report.rows_ready == 0
    assert report.rows_dropped_unmatched == 1


def test_build_enriched_lp_training_frame_drops_incomplete_lp() -> None:
    row = _lp_row(
        "A",
        "2026-01-01T06:30:00Z",
        "2026-01-01T08:30:00Z",
    )
    row[ML_FEATURE_COLUMNS[0]] = None
    lp = pd.DataFrame([row])

    frame, report = build_market_enriched_lp_training_frame(
        lp,
        _market(),
        volatility_window=3,
        drawdown_window=3,
        activity_window=3,
    )

    assert frame.empty
    assert report.rows_dropped_incomplete_lp == 1
