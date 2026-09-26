from __future__ import annotations

import numpy as np
import pandas as pd

from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.pool_lp_context_ablation import (
    evaluate_context_feature_ablation,
)
from meteora_learner.pool_lp_learning import (
    MARKET_CONTEXT_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_training import (
    ENRICHED_LP_MODEL_TARGET_COLUMNS,
)
from meteora_learner.pool_token_mint_context import (
    TOKEN_MINT_CONTEXT_FEATURE_COLUMNS,
)


def _frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")

    for pool_index, pool in enumerate(("A", "B", "C", "D")):
        mint_signal = float(pool_index % 2)

        for index in range(90):
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

            for column in ML_FEATURE_COLUMNS:
                row[column] = 1.0

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
                300.0 * market_signed
                + 500.0 * mint_signed
            )
            row["target_excess_vs_hold_bps"] = (
                180.0 * market_signed
                + 300.0 * mint_signed
            )
            row["target_range_survival_ratio"] = float(
                np.clip(
                    0.5
                    + 0.15 * market_signed
                    + 0.25 * mint_signed,
                    0.0,
                    1.0,
                )
            )
            rows.append(row)

    return pd.DataFrame(rows)


def test_context_ablation_separates_market_and_mint_value() -> None:
    report = evaluate_context_feature_ablation(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=80,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.lp_feature_count == len(ML_FEATURE_COLUMNS)
    assert report.market_feature_count == len(
        MARKET_CONTEXT_FEATURE_COLUMNS
    )
    assert report.mint_feature_count == len(
        TOKEN_MINT_CONTEXT_FEATURE_COLUMNS
    )
    assert report.full_feature_count == len(
        MINT_ENRICHED_LP_FEATURE_COLUMNS
    )

    net = next(
        item for item in report.aggregates
        if item.target == "target_net_return_bps"
    )
    assert net.mean_market_mae_improvement > 0
    assert net.mean_mint_mae_improvement > 0
    assert net.market_better_folds > 0
    assert net.mint_better_folds > 0


def test_context_ablation_uses_purged_identical_folds() -> None:
    report = evaluate_context_feature_ablation(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=80,
    )

    assert len(report.folds) >= 3
    assert all(fold.purged_rows > 0 for fold in report.folds)
    assert all(fold.validation_rows > 0 for fold in report.folds)
    assert all(fold.pools_in_train == 4 for fold in report.folds)
    assert all(
        fold.pools_in_validation == 4
        for fold in report.folds
    )


def test_context_ablation_reports_every_lp_target() -> None:
    report = evaluate_context_feature_ablation(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=80,
    )

    assert tuple(
        item.target for item in report.aggregates
    ) == ENRICHED_LP_MODEL_TARGET_COLUMNS

    for item in report.aggregates:
        assert np.isfinite(item.mean_lp_only_mae)
        assert np.isfinite(item.mean_lp_market_mae)
        assert np.isfinite(item.mean_lp_market_mint_mae)
        assert np.isfinite(item.mean_market_mae_improvement)
        assert np.isfinite(item.mean_mint_mae_improvement)
        assert 0 <= item.market_better_folds <= item.folds
        assert 0 <= item.mint_better_folds <= item.folds


def test_context_ablation_has_no_policy_verdict() -> None:
    report = evaluate_context_feature_ablation(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=80,
    )

    record = report.to_record()
    assert record["policy_actionable"] is False
    assert record["execution_wired"] is False
    assert "qualified" not in record
    assert "approved" not in record
    assert "allocation" not in record
    assert "recommended_action" not in record
