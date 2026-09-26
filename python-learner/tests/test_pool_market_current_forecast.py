from __future__ import annotations

import numpy as np
import pandas as pd

from meteora_learner.pool_market_current_forecast import (
    build_current_pool_market_forecast,
)


def _history(
    *,
    rows: int = 80,
    truncate_c: int = 0,
) -> pd.DataFrame:
    out = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")

    for pool_index, pool in enumerate(("A", "B", "C")):
        count = rows - truncate_c if pool == "C" else rows
        for index in range(count):
            wave = np.sin(index / 6.0 + pool_index)
            out.append(
                {
                    "pool_address": pool,
                    "observed_at": (
                        start + pd.Timedelta(hours=index)
                    ),
                    "price": (
                        10.0
                        + pool_index
                        + 0.03 * index
                        + 0.2 * wave
                    ),
                    "tvl_usd": (
                        100_000.0
                        + 800.0 * index
                        + 2_000.0 * wave
                    ),
                    "volume_24h_usd": (
                        20_000.0
                        + 250.0 * index
                        + 2_000.0 * abs(wave)
                    ),
                    "fees_24h_usd": (
                        80.0
                        + 0.6 * index
                        + 5.0 * abs(wave)
                    ),
                }
            )
    return pd.DataFrame(out)


def test_current_forecast_is_research_only_and_has_no_rank() -> None:
    report = build_current_pool_market_forecast(
        _history(),
        horizon_rows=4,
        volatility_window=4,
        drawdown_window=6,
        activity_window=4,
        min_train_decision_times=25,
        validation_decision_times=8,
        step_decision_times=8,
        min_train_rows=50,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.pools_forecast == 3
    assert report.training_rows > 0
    assert len(report.walk_forward.folds) > 0

    record = report.to_record()
    text = str(record).lower()
    assert "recommended_action" not in text
    assert "allocation" not in text
    assert "selected_pool" not in text
    assert "rank" not in record


def test_current_forecast_reports_stale_pool_snapshot_lag() -> None:
    report = build_current_pool_market_forecast(
        _history(truncate_c=5),
        horizon_rows=4,
        volatility_window=4,
        drawdown_window=6,
        activity_window=4,
        min_train_decision_times=25,
        validation_decision_times=8,
        step_decision_times=8,
        min_train_rows=50,
    )

    by_pool = {
        item.pool_address: item
        for item in report.forecasts
    }
    assert by_pool["A"].snapshot_lag_seconds == 0.0
    assert by_pool["B"].snapshot_lag_seconds == 0.0
    assert by_pool["C"].snapshot_lag_seconds == 5 * 3600.0


def test_current_forecast_training_labels_do_not_extend_beyond_now() -> None:
    report = build_current_pool_market_forecast(
        _history(),
        horizon_rows=4,
        volatility_window=4,
        drawdown_window=6,
        activity_window=4,
        min_train_decision_times=25,
        validation_decision_times=8,
        step_decision_times=8,
        min_train_rows=50,
    )

    assert pd.Timestamp(
        report.training_forward_end_max
    ) <= pd.Timestamp(report.universe_reference_time)

    for item in report.forecasts:
        assert len(item.predictions) == 6
        assert all(
            np.isfinite(value)
            for _, value in item.predictions
        )
