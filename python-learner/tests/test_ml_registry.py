from types import SimpleNamespace

import pandas as pd
import pytest

from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_registry import (
    CHAMPION,
    OFFLINE_QUALIFIED,
    PAPER_CHALLENGER,
    current_champion,
    qualify_offline_challenger,
    register_ml_v1_bundle,
    start_paper_challenger,
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


def offline_validation(bundle):
    return SimpleNamespace(
        offline_qualified=True,
        phase3_ready=True,
        validation_start=bundle.validation_start,
        validation_end=bundle.validation_end,
        to_record=lambda: {
            "offline_qualified": True,
            "phase3_ready": True,
            "validation_start": bundle.validation_start,
            "validation_end": bundle.validation_end,
        },
    )


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

    with pytest.raises(ValueError, match="qualified paper validation"):
        transition_model(
            storage,
            model_id="model-a",
            new_status=CHAMPION,
        )

    assert qualify_offline_challenger(
        storage,
        model_id="model-a",
        validation=offline_validation(bundle),
    ).status == OFFLINE_QUALIFIED
    assert start_paper_challenger(
        storage,
        model_id="model-a",
    ).status == PAPER_CHALLENGER
    with pytest.raises(ValueError, match="qualified paper validation"):
        transition_model(
            storage,
            model_id="model-a",
            new_status=CHAMPION,
        )
    assert current_champion(storage) is None


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
        qualify_offline_challenger(
            storage,
            model_id=model_id,
            validation=offline_validation(bundle),
        )
        start_paper_challenger(
            storage,
            model_id=model_id,
        )

    with pytest.raises(ValueError, match="qualified paper validation"):
        transition_model(storage, model_id="model-a", new_status=CHAMPION)

    with pytest.raises(ValueError, match="qualified paper validation"):
        transition_model(storage, model_id="model-b", new_status=CHAMPION)
