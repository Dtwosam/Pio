from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from meteora_learner.pool_market_learning import (
    POOL_MARKET_FEATURE_COLUMNS,
    POOL_MARKET_TARGET_COLUMNS,
    build_pool_market_learning_dataset,
)
from meteora_learner.pool_market_training import (
    predict_pool_market_outcomes,
    purged_timestamp_split,
    train_pool_market_models,
)


def _history(rows: int = 90) -> pd.DataFrame:
    out = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for pool_index, pool in enumerate(("A", "B", "C")):
        for index in range(rows):
            wave = np.sin(index / 7.0 + pool_index)
            trend = index / rows
            price = (
                10.0
                + pool_index * 2.0
                + 0.03 * index
                + 0.4 * wave
            )
            tvl = (
                100_000.0
                * (1.0 + 0.15 * trend)
                * (1.0 + 0.03 * wave)
            )
            volume = (
                20_000.0
                * (1.0 + 0.4 * abs(wave))
                * (1.0 + 0.2 * trend)
            )
            fees = volume * (
                0.002 + 0.0004 * (pool_index + 1)
            )
            out.append(
                {
                    "pool_address": pool,
                    "observed_at": start
                    + pd.Timedelta(hours=index),
                    "price": price,
                    "tvl_usd": tvl,
                    "volume_24h_usd": volume,
                    "fees_24h_usd": fees,
                }
            )
    return pd.DataFrame(out)


def _dataset() -> pd.DataFrame:
    return build_pool_market_learning_dataset(
        _history(),
        horizon_rows=6,
        volatility_window=5,
        drawdown_window=8,
        activity_window=5,
    ).to_frame()


def test_purged_split_removes_overlapping_forward_labels() -> None:
    frame = _dataset()

    train, valid, split = purged_timestamp_split(
        frame,
        split_fraction=0.75,
    )

    validation_start = pd.Timestamp(
        split.validation_decision_start
    )

    assert split.purged_rows > 0
    assert (
        pd.to_datetime(
            train["forward_end_observed_at"],
            utc=True,
        )
        < validation_start
    ).all()
    assert (
        pd.to_datetime(
            valid["decision_observed_at"],
            utc=True,
        )
        >= validation_start
    ).all()


def test_training_is_continuous_research_only() -> None:
    bundle = train_pool_market_models(
        _dataset(),
        split_fraction=0.75,
        min_train_rows=50,
        min_validation_rows=20,
    )

    assert bundle.research_only is True
    assert bundle.policy_actionable is False
    assert bundle.execution_wired is False
    assert bundle.target_columns == POOL_MARKET_TARGET_COLUMNS
    assert len(bundle.metrics) == len(POOL_MARKET_TARGET_COLUMNS)

    for item in bundle.metrics:
        assert np.isfinite(item.model_mae)
        assert np.isfinite(item.baseline_mae)
        assert item.model_mae >= 0
        assert item.baseline_mae >= 0


def test_prediction_contains_only_continuous_target_estimates() -> None:
    frame = _dataset()
    bundle = train_pool_market_models(
        frame,
        split_fraction=0.75,
        min_train_rows=50,
        min_validation_rows=20,
    )

    features = frame.iloc[-5:][
        list(POOL_MARKET_FEATURE_COLUMNS)
    ]
    prediction = predict_pool_market_outcomes(
        bundle,
        features,
    )

    assert list(prediction.columns) == [
        f"predicted_{target}"
        for target in POOL_MARKET_TARGET_COLUMNS
    ]
    lowered = " ".join(prediction.columns).lower()
    assert "safe" not in lowered
    assert "unsafe" not in lowered
    assert "action" not in lowered
    assert "allocation" not in lowered
    assert "rank" not in lowered


def test_training_rejects_insufficient_purged_history() -> None:
    frame = _dataset().head(30)

    with pytest.raises(ValueError, match="purged training rows"):
        train_pool_market_models(
            frame,
            split_fraction=0.75,
            min_train_rows=100,
            min_validation_rows=1,
        )


def test_invalid_forward_window_is_rejected() -> None:
    frame = _dataset()
    frame.loc[
        frame.index[0],
        "forward_end_observed_at",
    ] = frame.loc[
        frame.index[0],
        "decision_observed_at",
    ]

    with pytest.raises(
        ValueError,
        match="forward_end_observed_at must be after",
    ):
        purged_timestamp_split(frame)
