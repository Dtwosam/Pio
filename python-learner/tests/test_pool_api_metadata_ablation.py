from __future__ import annotations

import numpy as np
import pandas as pd

from meteora_learner.pool_api_metadata_ablation import (
    API_METADATA_ENRICHED_LP_FEATURE_COLUMNS,
    evaluate_api_metadata_ablation,
)
from meteora_learner.pool_api_metadata_context import (
    POOL_API_METADATA_FEATURE_COLUMNS,
)
from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
)


def _frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")

    for pool_index, pool in enumerate(("A", "B", "C")):
        metadata_signal = float(pool_index - 1)
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

            for column in MINT_ENRICHED_LP_FEATURE_COLUMNS:
                row[column] = 1.0
            for column in POOL_API_METADATA_FEATURE_COLUMNS:
                row[column] = 0.0

            row["api_dynamic_fee_pct"] = metadata_signal + 2.0
            row["api_apr"] = metadata_signal + 5.0
            row["api_pool_age_seconds"] = 100_000.0 + index

            row["target_net_return_bps"] = (
                400.0 * metadata_signal
            )
            row["target_excess_vs_hold_bps"] = (
                250.0 * metadata_signal
            )
            row["target_range_survival_ratio"] = (
                0.8 - 0.2 * abs(metadata_signal)
            )
            row["target_downside_bps"] = max(
                0.0,
                -row["target_net_return_bps"],
            )
            rows.append(row)

    return pd.DataFrame(rows)


def test_api_metadata_ablation_is_purged_and_research_only() -> None:
    report = evaluate_api_metadata_ablation(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert all(fold.purged_rows > 0 for fold in report.folds)
    assert report.base_feature_count == len(
        MINT_ENRICHED_LP_FEATURE_COLUMNS
    )
    assert report.metadata_feature_count == len(
        POOL_API_METADATA_FEATURE_COLUMNS
    )
    assert report.combined_feature_count == len(
        API_METADATA_ENRICHED_LP_FEATURE_COLUMNS
    )


def test_api_metadata_ablation_detects_real_metadata_signal() -> None:
    report = evaluate_api_metadata_ablation(
        _frame(),
        min_train_decision_times=30,
        validation_decision_times=10,
        step_decision_times=10,
        min_train_rows=50,
    )

    net = next(
        item for item in report.aggregates
        if item.target == "target_net_return_bps"
    )
    assert net.mean_metadata_mae_improvement > 0
    assert net.metadata_better_folds > 0


def test_api_metadata_ablation_emits_no_policy_verdict() -> None:
    report = evaluate_api_metadata_ablation(
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
    assert "qualified" not in record
    assert "allocation" not in record
    assert "recommended_action" not in record
