import pandas as pd
import pytest

from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_training import train_ml_v1_frame


def frame(rows=60):
    data = []
    for index in range(rows):
        positive = index % 3 != 0
        target_excess = 20 + index % 7 if positive else -(10 + index % 5)
        record = {
            "pool_address": f"pool-{index % 3}",
            "decision_observed_at": (
                pd.Timestamp("2026-01-01", tz="UTC")
                + pd.Timedelta(hours=index)
            ).isoformat(),
            "forward_end_observed_at": (
                pd.Timestamp("2026-01-01", tz="UTC")
                + pd.Timedelta(hours=index + 1)
            ).isoformat(),
            "strategy": "SPOT",
            "target_net_return_bps": target_excess + 5,
            "target_excess_vs_hold_bps": target_excess,
            "target_range_survival_ratio": 0.8 if positive else 0.4,
            "target_positive_excess": int(positive),
        }
        for feature_index, column in enumerate(ML_FEATURE_COLUMNS):
            record[column] = float((index + feature_index) % 11)
        data.append(record)
    return pd.DataFrame(data)


def test_ml_v1_uses_strict_timestamp_holdout():
    result = train_ml_v1_frame(frame(), split_fraction=0.8, min_rows=50)

    assert result.train_rows + result.validation_rows == 60
    assert result.train_rows > result.validation_rows
    assert pd.Timestamp(result.train_end) < pd.Timestamp(result.validation_start)
    assert result.metrics.net_return_mae_bps >= 0
    assert result.metrics.excess_vs_hold_mae_bps >= 0
    assert result.metrics.downside_mae_bps >= 0
    assert 0 <= result.metrics.positive_excess_brier <= 1
    assert 0 <= result.metrics.range_survival_mae <= 1


def test_ml_v1_rejects_too_few_examples():
    with pytest.raises(ValueError, match="at least 50"):
        train_ml_v1_frame(frame(rows=20), min_rows=50)


def test_ml_v1_requires_both_positive_classes_in_training():
    data = frame()
    data["target_positive_excess"] = 1
    with pytest.raises(ValueError, match="both positive"):
        train_ml_v1_frame(data)
