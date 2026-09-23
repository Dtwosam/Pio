import pandas as pd

from meteora_learner.ml_challenger import MLChallengerCriteria
from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_inference import MLInferenceConfig
from meteora_learner.ml_walk_forward import (
    MLWalkForwardCriteria,
    evaluate_ml_walk_forward,
)


def synthetic_frame():
    rows = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for index in range(24):
        observed = start + pd.Timedelta(hours=index)
        for action in range(2):
            spot = int(action == 0)
            positive = int(action == (index % 2))
            excess = 100 if positive else -100
            row = {
                "pool_address": "pool",
                "decision_observed_at": observed.isoformat(),
                "forward_end_observed_at": (
                    observed + pd.Timedelta(hours=1)
                ).isoformat(),
                "strategy": "SPOT" if spot else "CURVE",
                "baseline_selected": int(action == 0),
                "target_net_return_bps": excess,
                "target_excess_vs_hold_bps": excess,
                "target_range_survival_ratio": 1.0,
                "target_positive_excess": positive,
            }
            feature_values = {
                "strategy_spot": spot,
                "strategy_curve": int(action == 1),
                "strategy_bid_ask": 0,
                "half_width": action + 1,
                "center_offset": 0,
                "range_width_bins": 2 * (action + 1) + 1,
                "active_bin_id": index,
                "active_bin_move_1": index % 3 - 1,
                "deposit_fee_rate_bps": 5.0,
                "occupied_bins": 10,
                "active_liquidity_ratio": 0.2 + action * 0.1,
                "near_active_liquidity_ratio": 0.6,
                "below_active_liquidity_ratio": 0.2,
                "above_active_liquidity_ratio": 0.2,
                "liquidity_weighted_distance_bins": 1.0 + action,
                "fee_growth_bins_x": index % 2,
                "fee_growth_bins_y": (index + 1) % 2,
                "trailing_range_survival_ratio": 0.8,
                "trailing_excess_vs_hold_bps": excess // 2,
                "trailing_net_return_bps": excess // 2,
                "trailing_max_observed_share_bps": 100,
            }
            assert set(feature_values) == set(ML_FEATURE_COLUMNS)
            row.update(feature_values)
            rows.append(row)
    return pd.DataFrame(rows)


def walk_criteria():
    return MLWalkForwardCriteria(
        min_train_decision_times=8,
        validation_decision_times=4,
        step_decision_times=4,
        min_folds=3,
        min_total_comparable_decisions=3,
        min_qualified_fold_rate=0.0,
        min_mean_fold_uplift_bps=-10_000,
        min_positive_fold_rate=0.0,
        max_worst_fold_mean_uplift_loss_bps=10_000,
        training_split_fraction=0.75,
        training_min_rows=10,
    )


def fold_criteria():
    return MLChallengerCriteria(
        min_comparable_decisions=1,
        min_choice_coverage_rate=0.0,
        min_mean_uplift_bps=-10_000,
        min_win_rate=0.0,
        min_positive_excess_rate=0.0,
        max_single_decision_loss_bps=10_000,
    )


def inference_config():
    return MLInferenceConfig(
        risk_lambda=0.0,
        min_positive_excess_probability=0.0,
        min_range_survival_probability=0.0,
        min_score_bps=-1_000_000.0,
    )


def test_walk_forward_uses_strictly_later_validation_windows():
    report = evaluate_ml_walk_forward(
        synthetic_frame(),
        phase3_ready=True,
        criteria=walk_criteria(),
        fold_criteria=fold_criteria(),
        inference_config=inference_config(),
    )

    assert report.folds_attempted == 4
    assert report.folds_evaluated == 4
    assert report.total_comparable_decisions >= 4
    for fold in report.folds:
        assert pd.Timestamp(fold.train_window_end) < pd.Timestamp(
            fold.validation_start
        )
        assert pd.Timestamp(fold.validation_start) <= pd.Timestamp(
            fold.validation_end
        )


def test_walk_forward_is_not_qualified_without_phase3():
    report = evaluate_ml_walk_forward(
        synthetic_frame(),
        phase3_ready=False,
        criteria=walk_criteria(),
        fold_criteria=fold_criteria(),
        inference_config=inference_config(),
    )

    assert report.walk_forward_qualified is False
    assert any("Phase 3" in reason for reason in report.reasons)
