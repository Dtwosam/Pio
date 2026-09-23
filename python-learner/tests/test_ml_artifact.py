import pandas as pd
import pytest

from meteora_learner.ml_artifact import (
    load_ml_v1_artifact,
    save_ml_v1_artifact,
)
from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_training import train_ml_v1_frame


def training_frame(rows=60):
    records = []
    for index in range(rows):
        positive = index % 2 == 0
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
            "strategy": "SPOT",
            "baseline_selected": 1,
            "target_net_return_bps": 20 if positive else -10,
            "target_excess_vs_hold_bps": 15 if positive else -15,
            "target_range_survival_ratio": 0.8 if positive else 0.4,
            "target_positive_excess": int(positive),
        }
        for feature_index, column in enumerate(ML_FEATURE_COLUMNS):
            row[column] = float((index + feature_index) % 9)
        records.append(row)
    return pd.DataFrame(records)


def test_ml_artifact_round_trip_validates_metadata_and_checksum(tmp_path):
    bundle = train_ml_v1_frame(training_frame(), min_rows=50)
    saved = save_ml_v1_artifact(
        bundle,
        directory=tmp_path,
        model_id="model-v1",
    )

    loaded = load_ml_v1_artifact(
        artifact_path=saved.artifact_path,
        metadata_path=saved.metadata_path,
    )

    assert loaded.train_rows == bundle.train_rows
    assert loaded.validation_rows == bundle.validation_rows
    assert tuple(loaded.feature_columns) == tuple(bundle.feature_columns)
    assert saved.metadata.artifact_sha256
    assert saved.metadata.model_id == "model-v1"


def test_ml_artifact_rejects_tampered_bytes(tmp_path):
    bundle = train_ml_v1_frame(training_frame(), min_rows=50)
    saved = save_ml_v1_artifact(
        bundle,
        directory=tmp_path,
        model_id="model-v1",
    )
    saved.artifact_path.write_bytes(
        saved.artifact_path.read_bytes() + b"tamper"
    )

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_ml_v1_artifact(
            artifact_path=saved.artifact_path,
            metadata_path=saved.metadata_path,
        )


def test_ml_artifact_rejects_unsafe_model_id(tmp_path):
    bundle = train_ml_v1_frame(training_frame(), min_rows=50)

    with pytest.raises(ValueError, match="model_id"):
        save_ml_v1_artifact(
            bundle,
            directory=tmp_path,
            model_id="../escape",
        )
