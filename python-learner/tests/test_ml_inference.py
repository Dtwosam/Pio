import pandas as pd

from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_inference import (
    MLInferenceConfig,
    score_ml_candidates,
)
from meteora_learner.ml_training import train_ml_v1_frame


def dataset(rows=60):
    records = []
    for index in range(rows):
        positive = index % 3 != 0
        excess = 25 if positive else -15
        row = {
            "pool_address": f"pool-{index % 3}",
            "decision_observed_at": (
                pd.Timestamp("2026-01-01", tz="UTC")
                + pd.Timedelta(hours=index)
            ).isoformat(),
            "forward_end_observed_at": (
                pd.Timestamp("2026-01-01", tz="UTC")
                + pd.Timedelta(hours=index + 1)
            ).isoformat(),
            "strategy": ("SPOT", "CURVE", "BID_ASK")[index % 3],
            "baseline_selected": int(index % 4 == 0),
            "target_net_return_bps": excess + 5,
            "target_excess_vs_hold_bps": excess,
            "target_range_survival_ratio": 0.9 if positive else 0.4,
            "target_positive_excess": int(positive),
        }
        for feature_index, column in enumerate(ML_FEATURE_COLUMNS):
            row[column] = float((index * 2 + feature_index) % 13)
        records.append(row)
    return pd.DataFrame(records)


def test_ml_inference_ranks_candidates_but_never_marks_policy_actionable():
    frame = dataset()
    bundle = train_ml_v1_frame(frame, min_rows=50)
    candidates = frame.tail(3).copy()

    report = score_ml_candidates(
        bundle,
        candidates,
        config=MLInferenceConfig(
            risk_lambda=1.0,
            min_positive_excess_probability=0.0,
            min_range_survival_probability=0.0,
            min_score_bps=-1_000_000,
        ),
    )

    assert report.candidates_seen == 3
    assert report.candidates_eligible == 3
    assert report.research_choice is not None
    assert report.policy_actionable is False
    assert report.research_choice in report.predictions
