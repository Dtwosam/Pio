from __future__ import annotations

import numpy as np
import pandas as pd

from meteora_learner.pool_chain_context import (
    POOL_CHAIN_CONTEXT_FEATURE_COLUMNS,
)
from meteora_learner.pool_chain_context_ablation import (
    CHAIN_CONTEXT_ENRICHED_LP_FEATURE_COLUMNS,
    evaluate_chain_context_ablation,
)
from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_training import (
    ENRICHED_LP_MODEL_TARGET_COLUMNS,
)


def _frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")

    for pool_index, pool in enumerate(("A", "B", "C")):
        for index in range(90):
            signal = float((index + pool_index) % 2)
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
                row[column] = 1.0
            for feature_index, column in enumerate(
                POOL_CHAIN_CONTEXT_FEATURE_COLUMNS
            ):
                row[column] = 1.0 + 0.01 * feature_index

            row["chain_total_liquidity_log10_change"] = (
                1.0 if signal else -1.0
            )
            row["chain_near_active_liquidity_ratio_change"] = (
                0.5 if signal else -0.5
            )

            signed = 1.0 if signal else -1.0
            row["target_net_return_bps"] = 500.0 * signed
            row["target_excess_vs_hold_bps"] = 300.0 * signed
            row["target_range_survival_ratio"] = (
                0.9 if signal else 0.1
            )
            rows.append(row)

    return pd.DataFrame(rows)


def test_chain_context_ablation_detects_incremental_signal() -> None:
    report = evaluate_chain_context_ablation(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.combined_feature_count == len(
        CHAIN_CONTEXT_ENRICHED_LP_FEATURE_COLUMNS
    )
    assert report.chain_feature_count == len(
        POOL_CHAIN_CONTEXT_FEATURE_COLUMNS
    )
    assert all(fold.purged_rows > 0 for fold in report.folds)

    net = next(
        item for item in report.aggregates
        if item.target == "target_net_return_bps"
    )
    assert net.mean_chain_mae_improvement > 0
    assert net.chain_better_folds > 0


def test_chain_context_ablation_reports_all_targets() -> None:
    report = evaluate_chain_context_ablation(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert tuple(
        item.target for item in report.aggregates
    ) == ENRICHED_LP_MODEL_TARGET_COLUMNS

    for item in report.aggregates:
        assert np.isfinite(item.mean_base_mae)
        assert np.isfinite(item.mean_chain_mae)
        assert np.isfinite(item.mean_chain_mae_improvement)


def test_chain_context_ablation_emits_no_policy_verdict() -> None:
    report = evaluate_chain_context_ablation(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    record = report.to_record()
    assert record["policy_actionable"] is False
    assert record["execution_wired"] is False
    assert "approved" not in record
    assert "allocation" not in record
    assert "recommended_action" not in record
