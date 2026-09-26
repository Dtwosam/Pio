from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from meteora_learner.pool_market_learning import (
    POOL_MARKET_TARGET_COLUMNS,
    build_pool_market_learning_dataset,
)
from meteora_learner.pool_market_walk_forward import (
    evaluate_pool_market_walk_forward,
)


def _dataset() -> pd.DataFrame:
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    rows = []
    for pool_index, pool in enumerate(("A", "B", "C")):
        for index in range(100):
            wave = np.sin(index / 6.0 + pool_index)
            rows.append(
                {
                    "pool_address": pool,
                    "observed_at": (
                        start + pd.Timedelta(hours=index)
                    ),
                    "price": (
                        20.0
                        + pool_index
                        + 0.04 * index
                        + 0.6 * wave
                    ),
                    "tvl_usd": (
                        150_000.0
                        + 800.0 * index
                        + 3_000.0 * wave
                    ),
                    "volume_24h_usd": (
                        30_000.0
                        + 250.0 * index
                        + 4_000.0 * abs(wave)
                    ),
                    "fees_24h_usd": (
                        90.0
                        + 0.8 * index
                        + 10.0 * abs(wave)
                    ),
                }
            )

    return build_pool_market_learning_dataset(
        pd.DataFrame(rows),
        horizon_rows=5,
        volatility_window=5,
        drawdown_window=8,
        activity_window=5,
    ).to_frame()


def test_walk_forward_is_purged_and_descriptive_only() -> None:
    report = evaluate_pool_market_walk_forward(
        _dataset(),
        min_train_decision_times=35,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=60,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert len(report.folds) >= 3
    assert all(fold.purged_rows > 0 for fold in report.folds)
    assert all(fold.validation_rows > 0 for fold in report.folds)
    assert all(fold.pools_in_train == 3 for fold in report.folds)
    assert all(
        fold.pools_in_validation == 3
        for fold in report.folds
    )


def test_walk_forward_reports_every_continuous_target() -> None:
    report = evaluate_pool_market_walk_forward(
        _dataset(),
        min_train_decision_times=35,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=60,
    )

    assert tuple(
        item.target for item in report.aggregates
    ) == POOL_MARKET_TARGET_COLUMNS

    for item in report.aggregates:
        assert item.folds == len(report.folds)
        assert np.isfinite(item.mean_model_mae)
        assert np.isfinite(item.mean_baseline_mae)
        assert np.isfinite(item.mean_mae_improvement)
        assert 0 <= item.model_better_folds <= item.folds


def test_walk_forward_does_not_emit_policy_verdict_fields() -> None:
    report = evaluate_pool_market_walk_forward(
        _dataset(),
        min_train_decision_times=35,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=60,
    )

    record = report.to_record()
    text = " ".join(str(key).lower() for key in record)
    assert "qualified" not in text
    assert "approved" not in text
    assert "allocation" not in text
    assert "action" not in text
    assert record["policy_actionable"] is False


def test_walk_forward_rejects_insufficient_history() -> None:
    frame = _dataset()
    first_times = sorted(
        frame["decision_observed_at"].unique()
    )[:20]
    small = frame[
        frame["decision_observed_at"].isin(first_times)
    ]

    with pytest.raises(
        ValueError,
        match="not enough unique decision timestamps",
    ):
        evaluate_pool_market_walk_forward(
            small,
            min_train_decision_times=15,
            validation_decision_times=10,
        )
