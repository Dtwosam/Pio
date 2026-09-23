import pandas as pd

from meteora_learner.ml_challenger import MLChallengerCriteria
from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_inference import MLInferenceConfig
from meteora_learner.phase_promotion import (
    persist_phase2_promotion,
    persist_phase3_promotion,
)
from meteora_learner.ml_workflow import (
    load_registered_ml_v1,
    qualify_registered_offline_challenger,
    train_save_register_ml_v1,
)
from meteora_learner.storage import Storage


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


def seed_phase3(storage):
    ready = SimpleNamespace(
        promotion_ready=True,
        to_record=lambda: {"promotion_ready": True},
    )
    persist_phase2_promotion(storage, report=ready)
    persist_phase3_promotion(storage, report=ready)


def test_train_save_register_and_reload_round_trip(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    frame = action_frame()

    result = train_save_register_ml_v1(
        storage,
        frame,
        model_id="model-a",
        dataset_version="actions-v1",
        artifact_directory=tmp_path / "models",
        split_fraction=0.7,
        min_rows=50,
    )

    assert result.registry.status == "OFFLINE_CANDIDATE"
    assert result.registry.artifact_uri == str(result.artifact.artifact_path)

    loaded = load_registered_ml_v1(storage, model_id="model-a")
    assert loaded.train_rows == result.registry.train_rows
    assert loaded.validation_rows == result.registry.validation_rows


def test_registered_challenger_can_only_qualify_from_held_out_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    frame = action_frame()
    seed_phase3(storage)
    train_save_register_ml_v1(
        storage,
        frame,
        model_id="model-a",
        dataset_version="actions-v1",
        artifact_directory=tmp_path / "models",
        split_fraction=0.7,
        min_rows=50,
    )

    validation, record = qualify_registered_offline_challenger(
        storage,
        frame,
        model_id="model-a",
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

    assert validation.offline_qualified is True
    assert record.status == "OFFLINE_QUALIFIED"
