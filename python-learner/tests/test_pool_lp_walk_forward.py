from __future__ import annotations

import numpy as np
import pandas as pd

from meteora_learner.pool_lp_learning import (
    ENRICHED_LP_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_training import (
    ENRICHED_LP_MODEL_TARGET_COLUMNS,
)
from meteora_learner.pool_lp_walk_forward import (
    evaluate_enriched_lp_walk_forward,
)


def _frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for pool_index, pool in enumerate(("A", "B", "C")):
        for index in range(100):
            wave = np.sin(index / 6.0 + pool_index)
            row = {
                "pool_address": pool,
                "decision_observed_at": (
                    start + pd.Timedelta(hours=index)
                ),
                "forward_end_observed_at": (
                    start + pd.Timedelta(hours=index + 5)
                ),
            }
            for feature_index, column in enumerate(
                ENRICHED_LP_FEATURE_COLUMNS
            ):
                row[column] = (
                    1.0
                    + 0.01 * feature_index
                    + 0.002 * index
                    + 0.04 * wave
                    + 0.01 * pool_index
                )
            row["target_net_return_bps"] = (
                90.0 * wave + 1.5 * index - 30.0
            )
            row["target_excess_vs_hold_bps"] = (
                50.0 * wave + 0.8 * index - 15.0
            )
            row["target_range_survival_ratio"] = float(
                np.clip(0.75 + 0.12 * wave, 0.0, 1.0)
            )
            rows.append(row)
    return pd.DataFrame(rows)


def test_enriched_lp_walk_forward_is_purged() -> None:
    report = evaluate_enriched_lp_walk_forward(
        _frame(),
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


def test_enriched_lp_walk_forward_reports_all_targets() -> None:
    report = evaluate_enriched_lp_walk_forward(
        _frame(),
        min_train_decision_times=35,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=60,
    )

    assert tuple(
        item.target for item in report.aggregates
    ) == ENRICHED_LP_MODEL_TARGET_COLUMNS

    for aggregate in report.aggregates:
        assert np.isfinite(aggregate.mean_model_mae)
        assert np.isfinite(aggregate.mean_baseline_mae)
        assert np.isfinite(aggregate.mean_mae_improvement)
        assert (
            0
            <= aggregate.model_better_folds
            <= aggregate.folds
        )


def test_enriched_lp_walk_forward_has_no_policy_verdict() -> None:
    report = evaluate_enriched_lp_walk_forward(
        _frame(),
        min_train_decision_times=35,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=60,
    )

    record = report.to_record()
    assert record["policy_actionable"] is False
    assert record["execution_wired"] is False
    assert "qualified" not in record
    assert "recommended_action" not in record
    assert "allocation" not in record
