from __future__ import annotations

import numpy as np
import pandas as pd

from meteora_learner.pool_lp_learning import (
    ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    ENRICHED_LP_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_training import (
    ENRICHED_LP_MODEL_TARGET_COLUMNS,
    predict_enriched_lp_outcomes,
    purged_enriched_lp_split,
    train_enriched_lp_models,
)


def _frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for pool_index, pool in enumerate(("A", "B", "C")):
        for index in range(90):
            wave = np.sin(index / 7.0 + pool_index)
            row = {
                "pool_address": pool,
                "decision_observed_at": (
                    start + pd.Timedelta(hours=index)
                ),
                "forward_end_observed_at": (
                    start + pd.Timedelta(hours=index + 4)
                ),
            }
            for feature_index, column in enumerate(
                ENRICHED_LP_FEATURE_COLUMNS
            ):
                row[column] = (
                    1.0
                    + 0.01 * feature_index
                    + 0.002 * index
                    + 0.03 * wave
                    + 0.01 * pool_index
                )
            row["target_net_return_bps"] = (
                80.0 * wave + 2.0 * index - 40.0
            )
            row["target_excess_vs_hold_bps"] = (
                45.0 * wave + 0.8 * index - 20.0
            )
            row["target_range_survival_ratio"] = float(
                np.clip(0.7 + 0.15 * wave, 0.0, 1.0)
            )
            rows.append(row)
    return pd.DataFrame(rows)


def test_enriched_lp_split_purges_overlapping_labels() -> None:
    train, valid, split = purged_enriched_lp_split(
        _frame(),
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


def test_enriched_lp_training_is_continuous_and_non_actionable() -> None:
    bundle = train_enriched_lp_models(
        _frame(),
        split_fraction=0.75,
        min_train_rows=50,
        min_validation_rows=20,
    )

    assert bundle.research_only is True
    assert bundle.policy_actionable is False
    assert bundle.execution_wired is False
    assert bundle.target_columns == ENRICHED_LP_MODEL_TARGET_COLUMNS

    for metric in bundle.metrics:
        assert np.isfinite(metric.model_mae)
        assert np.isfinite(metric.baseline_mae)
        assert metric.model_mae >= 0
        assert metric.baseline_mae >= 0


def test_downside_target_is_derived_continuously() -> None:
    bundle = train_enriched_lp_models(
        _frame(),
        split_fraction=0.75,
        min_train_rows=50,
        min_validation_rows=20,
    )

    assert "target_downside_bps" in bundle.models
    assert set(ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS) <= set(
        bundle.models
    )


def test_enriched_lp_prediction_has_no_action_or_rank() -> None:
    frame = _frame()
    bundle = train_enriched_lp_models(
        frame,
        split_fraction=0.75,
        min_train_rows=50,
        min_validation_rows=20,
    )

    prediction = predict_enriched_lp_outcomes(
        bundle,
        frame.iloc[-5:],
    )

    assert list(prediction.columns) == [
        f"predicted_{target}"
        for target in ENRICHED_LP_MODEL_TARGET_COLUMNS
    ]
    names = " ".join(prediction.columns).lower()
    assert "rank" not in names
    assert "allocation" not in names
    assert "action" not in names
