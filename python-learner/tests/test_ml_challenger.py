import pandas as pd

from meteora_learner.ml_challenger import (
    MLChallengerCriteria,
    evaluate_ml_challenger,
)
from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_inference import MLInferenceConfig
from meteora_learner.ml_training import train_ml_v1_frame


def action_frame(decisions=30):
    rows = []
    for decision in range(decisions):
        decision_time = (
            pd.Timestamp("2026-01-01", tz="UTC")
            + pd.Timedelta(hours=decision)
        )
        for action in range(3):
            is_baseline = action == 0
            actual_excess = (
                30 + decision % 5
                if action == 2
                else (10 + decision % 3 if action == 0 else -5)
            )
            row = {
                "pool_address": f"pool-{decision % 3}",
                "decision_observed_at": decision_time.isoformat(),
                "forward_end_observed_at": (
                    decision_time + pd.Timedelta(hours=1)
                ).isoformat(),
                "strategy": ("SPOT", "CURVE", "BID_ASK")[action],
                "baseline_selected": int(is_baseline),
                "target_net_return_bps": actual_excess + 5,
                "target_excess_vs_hold_bps": actual_excess,
                "target_range_survival_ratio": 0.8 + 0.05 * (action == 2),
                "target_positive_excess": int(actual_excess > 0),
            }
            for feature_index, column in enumerate(ML_FEATURE_COLUMNS):
                if column == "strategy_spot":
                    value = int(action == 0)
                elif column == "strategy_curve":
                    value = int(action == 1)
                elif column == "strategy_bid_ask":
                    value = int(action == 2)
                elif column == "half_width":
                    value = action + 1
                elif column == "center_offset":
                    value = 0
                elif column == "range_width_bins":
                    value = 2 * (action + 1) + 1
                else:
                    value = float((decision + feature_index) % 11)
                row[column] = value
            rows.append(row)
    return pd.DataFrame(rows)


def test_challenger_evaluates_only_held_out_decision_groups():
    frame = action_frame()
    bundle = train_ml_v1_frame(
        frame,
        split_fraction=0.7,
        min_rows=50,
    )

    report = evaluate_ml_challenger(
        bundle,
        frame,
        phase3_ready=True,
        inference_config=MLInferenceConfig(
            risk_lambda=0.0,
            min_positive_excess_probability=0.0,
            min_range_survival_probability=0.0,
            min_score_bps=-1_000_000,
        ),
        criteria=MLChallengerCriteria(
            min_comparable_decisions=1,
            min_choice_coverage_rate=0.5,
            min_mean_uplift_bps=-1_000,
            min_win_rate=0.0,
            min_positive_excess_rate=0.0,
            max_single_decision_loss_bps=10_000,
        ),
    )

    assert report.validation_decisions_seen > 0
    assert report.comparable_decisions > 0
    assert report.policy_actionable is False
    for decision in report.decisions:
        assert pd.Timestamp(decision.decision_observed_at) >= pd.Timestamp(
            bundle.validation_start
        )


def test_challenger_cannot_qualify_before_phase3_promotion():
    frame = action_frame()
    bundle = train_ml_v1_frame(frame, split_fraction=0.7, min_rows=50)

    report = evaluate_ml_challenger(
        bundle,
        frame,
        phase3_ready=False,
        inference_config=MLInferenceConfig(
            min_positive_excess_probability=0.0,
            min_range_survival_probability=0.0,
            min_score_bps=-1_000_000,
        ),
        criteria=MLChallengerCriteria(
            min_comparable_decisions=1,
            min_choice_coverage_rate=0.0,
            min_mean_uplift_bps=-1_000,
            min_win_rate=0.0,
            min_positive_excess_rate=0.0,
            max_single_decision_loss_bps=10_000,
        ),
    )

    assert report.offline_qualified is False
    assert report.policy_actionable is False
    assert "Phase 3" in report.reasons[0]
