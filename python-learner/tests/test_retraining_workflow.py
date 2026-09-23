from pathlib import Path

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.continuous_learning import ContinuousLearningCriteria
from meteora_learner.liquidity_math import Q64
from meteora_learner.ml_retraining_dataset import MLRetrainPoolSpec
from meteora_learner.phase_promotion import PHASE7, PHASE7_EVIDENCE_TYPE
from meteora_learner.retraining_workflow import (
    RETRAIN_DATASET_EVIDENCE_TYPE,
    start_retraining_cycle_with_dataset,
    train_retraining_cycle_challenger,
)
from meteora_learner.storage import Storage


NOW = "2026-09-23T00:10:00+00:00"


def save_snapshot(storage, minute, active_id, checkpoint):
    bins = []
    for bin_id in range(-3, 4):
        bins.append(
            {
                "bin_id": bin_id,
                "price": str(Q64),
                "amount_x": "1000",
                "amount_y": "1000",
                "liquidity_supply": str(2000 * Q64),
                "fee_amount_x_per_token_stored": "0",
                "fee_amount_y_per_token_stored": str(checkpoint),
            }
        )
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": active_id,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
            "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
            "base_fee_rate": "0",
            "variable_fee_rate": "0",
            "total_fee_rate": "0",
            "deposit_total_fee_rate": "0",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": -3,
                    "upper_bin_id": 3,
                    "bins": bins,
                }
            ],
        },
        observed_at=f"2026-09-23T00:{minute:02d}:00+00:00",
    )


def seed(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status,
                train_end, validation_end, metrics_json
            ) VALUES (
                'champion', '2026-08-01T00:00:00+00:00',
                '2026-08-01T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION',
                '2026-09-22T23:00:00+00:00',
                '2026-09-22T23:30:00+00:00', '{}'
            )
            """
        )
    storage.save_phase_promotion_evidence(
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )
    save_snapshot(storage, 0, 0, 0)
    save_snapshot(storage, 5, 0, Q64)
    save_snapshot(storage, 10, 1, 2 * Q64)
    save_snapshot(storage, 15, 1, 3 * Q64)


def test_dataset_build_and_cycle_share_cutoff_and_hash(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    output = tmp_path / "retrain.csv"

    result = start_retraining_cycle_with_dataset(
        storage,
        pools=(
            MLRetrainPoolSpec(
                pool_address="pool",
                amount_x=0,
                amount_y=10,
                network_cost_y_atomic=0,
            ),
        ),
        output_file=output,
        cycle_id="cycle",
        as_of=NOW,
        criteria=ContinuousLearningCriteria(
            min_new_chain_observations=3,
            min_new_chain_pools=1,
            min_new_live_labels=0,
            max_champion_age_days=1,
        ),
        lookback_observations=2,
        forward_observations=2,
        half_widths=(1,),
        center_offsets=(0,),
        max_share_bps=500,
    )

    assert output.exists()
    assert result.cycle.plan_as_of == NOW
    assert result.cycle.target_dataset_version == (
        result.dataset.dataset_version
    )
    assert result.dataset.decision_points == 1
    evidence = storage.latest_model_live_evidence(
        "champion",
        evidence_type=RETRAIN_DATASET_EVIDENCE_TYPE,
    )
    assert evidence is not None
    assert evidence["status"] == "BUILT"
    assert evidence["evidence"]["cycle_id"] == "cycle"
    assert evidence["evidence"]["dataset"]["dataset_sha256"] == (
        result.dataset.dataset_sha256
    )
    assert Path(result.output_file) == output



def test_cycle_bound_training_rejects_tampered_dataset(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    output = tmp_path / "retrain.csv"

    result = start_retraining_cycle_with_dataset(
        storage,
        pools=(
            MLRetrainPoolSpec(
                pool_address="pool",
                amount_x=0,
                amount_y=10,
                network_cost_y_atomic=0,
            ),
        ),
        output_file=output,
        cycle_id="cycle",
        as_of=NOW,
        criteria=ContinuousLearningCriteria(
            min_new_chain_observations=3,
            min_new_chain_pools=1,
            min_new_live_labels=0,
            max_champion_age_days=1,
        ),
        lookback_observations=2,
        forward_observations=2,
        half_widths=(1,),
        center_offsets=(0,),
        max_share_bps=500,
    )
    assert result.cycle.target_dataset_version.startswith(
        "ML_ACTION_DATASET_V1:"
    )

    output.write_text(
        output.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    try:
        train_retraining_cycle_challenger(
            storage,
            cycle_id="cycle",
            dataset_file=output,
            model_id="challenger",
            artifact_directory=tmp_path / "artifacts",
            min_rows=1,
        )
    except ValueError as exc:
        assert "checksum/version" in str(exc)
    else:
        raise AssertionError("expected tampered dataset refusal")

    assert storage.model_registry_entry("challenger") is None


def test_retraining_dataset_persists_absolute_output_path(
    tmp_path, monkeypatch
):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    monkeypatch.chdir(tmp_path)

    result = start_retraining_cycle_with_dataset(
        storage,
        pools=(
            MLRetrainPoolSpec(
                pool_address="pool",
                amount_x=0,
                amount_y=10,
                network_cost_y_atomic=0,
            ),
        ),
        output_file="datasets/retrain.csv",
        cycle_id="cycle-absolute",
        as_of=NOW,
        criteria=ContinuousLearningCriteria(
            min_new_chain_observations=3,
            min_new_chain_pools=1,
            min_new_live_labels=0,
            max_champion_age_days=1,
        ),
        lookback_observations=2,
        forward_observations=2,
        half_widths=(1,),
        center_offsets=(0,),
        max_share_bps=500,
    )

    output = Path(result.output_file)
    assert output.is_absolute()
    assert output == (tmp_path / "datasets/retrain.csv").resolve()
    evidence = storage.latest_model_live_evidence(
        "champion",
        evidence_type=RETRAIN_DATASET_EVIDENCE_TYPE,
    )
    assert evidence is not None
    assert Path(evidence["evidence"]["output_file"]) == output
