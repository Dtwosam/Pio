from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_training import (
    ENRICHED_LP_MODEL_TARGET_COLUMNS,
)
from meteora_learner.pool_lp_unseen_pool import (
    evaluate_unseen_pool_walk_forward,
)


def _frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")

    for pool_index, pool in enumerate(("A", "B", "C", "D")):
        mint_signal = float(pool_index % 2)

        for index in range(50):
            market_signal = float(index % 2)
            row = {
                "pool_address": pool,
                "decision_observed_at": (
                    start + pd.Timedelta(hours=index)
                ),
                "forward_end_observed_at": (
                    start + pd.Timedelta(hours=index + 4)
                ),
            }
            for column in MINT_ENRICHED_LP_FEATURE_COLUMNS:
                row[column] = 0.0

            row["market_volume_to_tvl"] = market_signal
            row["market_fees_to_tvl"] = market_signal
            row["mint_x_mint_authority_active"] = mint_signal
            row["mint_y_mint_authority_active"] = mint_signal
            row["mint_x_initialized"] = 1.0
            row["mint_y_initialized"] = 1.0
            row["mint_x_program_standard_spl"] = 1.0
            row["mint_y_program_standard_spl"] = 1.0
            row["mint_x_program_matches_pool"] = 1.0
            row["mint_y_program_matches_pool"] = 1.0

            market_signed = 1.0 if market_signal else -1.0
            mint_signed = 1.0 if mint_signal else -1.0
            row["target_net_return_bps"] = (
                500.0 * market_signed
                + 300.0 * mint_signed
            )
            row["target_excess_vs_hold_bps"] = (
                300.0 * market_signed
                + 180.0 * mint_signed
            )
            row["target_range_survival_ratio"] = float(
                np.clip(
                    0.5
                    + 0.25 * market_signed
                    + 0.15 * mint_signed,
                    0.0,
                    1.0,
                )
            )
            rows.append(row)

    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def unseen_pool_report():
    return evaluate_unseen_pool_walk_forward(
        _frame(),
        min_train_decision_times=20,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=40,
    )


def test_unseen_pool_validation_holds_each_pool_out(
    unseen_pool_report,
) -> None:
    report = unseen_pool_report

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.pools_available == 4
    assert report.pools_evaluated == 4
    assert {
        fold.held_out_pool for fold in report.folds
    } == {"A", "B", "C", "D"}
    assert all(fold.training_pool_count == 3 for fold in report.folds)
    assert all(fold.purged_rows > 0 for fold in report.folds)
    assert all(fold.validation_rows == 10 for fold in report.folds)


def test_unseen_pool_model_can_generalize_shared_signal(
    unseen_pool_report,
) -> None:
    net = next(
        item for item in unseen_pool_report.aggregates
        if item.target == "target_net_return_bps"
    )

    assert net.held_out_pools == 4
    assert net.mean_mae_improvement > 0
    assert net.model_better_folds > 0


def test_unseen_pool_reports_every_continuous_target(
    unseen_pool_report,
) -> None:
    assert tuple(
        item.target for item in unseen_pool_report.aggregates
    ) == ENRICHED_LP_MODEL_TARGET_COLUMNS

    for item in unseen_pool_report.aggregates:
        assert np.isfinite(item.mean_model_mae)
        assert np.isfinite(item.mean_baseline_mae)
        assert np.isfinite(item.mean_mae_improvement)
        assert 0 <= item.model_better_folds <= item.folds


def test_unseen_pool_validation_has_no_policy_verdict(
    unseen_pool_report,
) -> None:
    record = unseen_pool_report.to_record()
    assert record["policy_actionable"] is False
    assert record["execution_wired"] is False
    assert "qualified" not in record
    assert "approved" not in record
    assert "allocation" not in record
    assert "recommended_action" not in record


def test_unseen_pool_validation_rejects_unknown_holdout() -> None:
    with pytest.raises(ValueError, match="not present"):
        evaluate_unseen_pool_walk_forward(
            _frame(),
            held_out_pools=("UNKNOWN",),
            min_train_decision_times=20,
            validation_decision_times=10,
            step_decision_times=10,
            min_train_rows=40,
        )
