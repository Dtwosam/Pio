from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from meteora_learner.pool_lp_feature_drift import (
    evaluate_feature_drift,
)
from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
)


SHIFT_FEATURE = MINT_ENRICHED_LP_FEATURE_COLUMNS[0]


def _frame(*, shift_late: bool) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")

    for pool_index, pool in enumerate(("A", "B", "C")):
        for index in range(90):
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
                MINT_ENRICHED_LP_FEATURE_COLUMNS
            ):
                value = (
                    1.0
                    + 0.01 * feature_index
                    + 0.005 * pool_index
                    + 0.01 * np.sin(index / 2.0)
                )
                if (
                    shift_late
                    and column == SHIFT_FEATURE
                    and index >= 55
                ):
                    value += 100.0
                row[column] = value

            row["target_net_return_bps"] = 10.0
            row["target_excess_vs_hold_bps"] = 5.0
            row["target_range_survival_ratio"] = 0.8
            rows.append(row)

    return pd.DataFrame(rows)


def test_feature_drift_is_purged_and_research_only() -> None:
    report = evaluate_feature_drift(
        _frame(shift_late=False),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert len(report.folds) >= 3
    assert all(fold.purged_rows > 0 for fold in report.folds)


def test_feature_drift_exposes_shifted_feature_escape_rate() -> None:
    stable = evaluate_feature_drift(
        _frame(shift_late=False),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )
    shifted = evaluate_feature_drift(
        _frame(shift_late=True),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    stable_feature = next(
        item for item in stable.aggregates
        if item.feature == SHIFT_FEATURE
    )
    shifted_feature = next(
        item for item in shifted.aggregates
        if item.feature == SHIFT_FEATURE
    )

    assert (
        shifted_feature.max_validation_reference_escape_rate
        > stable_feature.max_validation_reference_escape_rate
    )
    assert shifted_feature.max_validation_reference_escape_rate > 0.5


def test_feature_drift_reports_all_features_without_verdict() -> None:
    report = evaluate_feature_drift(
        _frame(shift_late=True),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert len(report.aggregates) == len(
        MINT_ENRICHED_LP_FEATURE_COLUMNS
    )
    record = report.to_record()
    assert record["policy_actionable"] is False
    assert record["execution_wired"] is False
    assert "approved" not in record
    assert "qualified" not in record
    assert "recommended_action" not in record


def test_feature_drift_rejects_invalid_reference_quantiles() -> None:
    with pytest.raises(ValueError, match="reference_lower_quantile"):
        evaluate_feature_drift(
            _frame(shift_late=False),
            reference_lower_quantile=0.6,
        )

    with pytest.raises(ValueError, match="reference_upper_quantile"):
        evaluate_feature_drift(
            _frame(shift_late=False),
            reference_upper_quantile=0.4,
        )
