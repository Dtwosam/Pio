from __future__ import annotations

import numpy as np
import pandas as pd

from meteora_learner.pool_lp_cross_sectional_ranking import (
    evaluate_cross_sectional_ranking,
)
from meteora_learner.pool_lp_mint_ablation import (
    MINT_ENRICHED_LP_FEATURE_COLUMNS,
)


def _frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    pools = ("A", "B", "C", "D", "E")

    for index in range(70):
        for pool_index, pool in enumerate(pools):
            row = {
                "pool_address": pool,
                "decision_observed_at": (
                    start + pd.Timedelta(hours=index)
                ),
                "forward_end_observed_at": (
                    start + pd.Timedelta(hours=index + 3)
                ),
            }
            for column in MINT_ENRICHED_LP_FEATURE_COLUMNS:
                row[column] = 1.0
            row["mint_x_log10_supply_tokens"] = float(pool_index)
            row["market_volume_to_tvl"] = (
                0.1 + 0.05 * pool_index
            )

            net = float(pool_index * 200 - 400)
            row["target_net_return_bps"] = net
            row["target_excess_vs_hold_bps"] = net * 0.7
            row["target_range_survival_ratio"] = (
                0.5 + 0.1 * pool_index
            )
            rows.append(row)
    return pd.DataFrame(rows)


def test_cross_sectional_ranking_is_purged_and_non_actionable() -> None:
    report = evaluate_cross_sectional_ranking(
        _frame(),
        min_train_decision_times=25,
        validation_decision_times=5,
        step_decision_times=5,
        min_train_rows=60,
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.ranking_points > 0
    assert all(fold.purged_rows > 0 for fold in report.folds)
    assert report.skipped_single_pool_times == 0


def test_cross_sectional_ranking_detects_learnable_order() -> None:
    report = evaluate_cross_sectional_ranking(
        _frame(),
        min_train_decision_times=25,
        validation_decision_times=5,
        step_decision_times=5,
        min_train_rows=60,
    )

    assert report.mean_opportunity_rank_correlation is not None
    assert report.mean_opportunity_rank_correlation > 0.5
    assert report.mean_opportunity_regret_bps < 250.0
    assert report.mean_selected_uplift_vs_median_bps > 0.0

    assert report.mean_downside_rank_correlation is not None
    assert report.mean_downside_rank_correlation > 0.0
    assert report.mean_downside_regret_bps >= 0.0


def test_cross_sectional_ranking_reports_research_winners_only() -> None:
    report = evaluate_cross_sectional_ranking(
        _frame(),
        min_train_decision_times=25,
        validation_decision_times=5,
        step_decision_times=5,
        min_train_rows=60,
    )

    point = report.folds[0].points[0]
    assert point.predicted_top_pool == "E"
    assert point.realized_top_pool == "E"
    assert point.opportunity_regret_bps == 0.0

    record = report.to_record()
    assert record["policy_actionable"] is False
    assert record["execution_wired"] is False
    assert "recommended_action" not in record
    assert "allocation" not in record
