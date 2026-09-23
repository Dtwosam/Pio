import pandas as pd
import pytest

from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_registry import (
    CHAMPION,
    OFFLINE_QUALIFIED,
    PAPER_CHALLENGER,
    current_champion,
    register_ml_v1_bundle,
    transition_model,
)
from meteora_learner.ml_training import train_ml_v1_frame
from meteora_learner.storage import Storage


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


def test_model_registry_enforces_staged_promotion(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    bundle = train_ml_v1_frame(training_frame(), min_rows=50)

    record = register_ml_v1_bundle(
        storage,
        model_id="model-a",
        bundle=bundle,
        dataset_version="dataset-v1",
    )
    assert record.status == "OFFLINE_CANDIDATE"

    with pytest.raises(ValueError, match="invalid model transition"):
        transition_model(
            storage,
            model_id="model-a",
            new_status=CHAMPION,
        )

    assert transition_model(
        storage,
        model_id="model-a",
        new_status=OFFLINE_QUALIFIED,
    ).status == OFFLINE_QUALIFIED
    assert transition_model(
        storage,
        model_id="model-a",
        new_status=PAPER_CHALLENGER,
    ).status == PAPER_CHALLENGER
    assert transition_model(
        storage,
        model_id="model-a",
        new_status=CHAMPION,
    ).status == CHAMPION
    assert current_champion(storage).model_id == "model-a"


def test_registry_blocks_second_champion_until_first_is_rolled_back(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    bundle = train_ml_v1_frame(training_frame(), min_rows=50)

    for model_id in ("model-a", "model-b"):
        register_ml_v1_bundle(
            storage,
            model_id=model_id,
            bundle=bundle,
            dataset_version="dataset-v1",
        )
        transition_model(
            storage,
            model_id=model_id,
            new_status=OFFLINE_QUALIFIED,
        )
        transition_model(
            storage,
            model_id=model_id,
            new_status=PAPER_CHALLENGER,
        )

    transition_model(storage, model_id="model-a", new_status=CHAMPION)

    with pytest.raises(ValueError, match="champion already exists"):
        transition_model(storage, model_id="model-b", new_status=CHAMPION)
