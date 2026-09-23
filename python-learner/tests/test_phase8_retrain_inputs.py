from types import SimpleNamespace

import pytest

import meteora_learner.phase8_retrain_inputs as inputs_module
from meteora_learner.phase8_retrain_inputs import (
    audit_phase8_retrain_inputs,
    build_phase8_retrain_input_template,
    load_phase8_retrain_inputs,
    parse_phase8_retrain_inputs,
    persist_phase8_retrain_inputs,
    run_phase8_retrain_build_from_inputs,
)
from meteora_learner.storage import Storage


def seed_champion(storage, model_id="champion-1", dataset="dataset-v1"):
    storage.register_model(
        model_id=model_id,
        model_family="ML_V1_HIST_GRADIENT_BOOSTING",
        feature_version="ML_ACTION_FEATURES_V1",
        dataset_version=dataset,
        metrics={},
    )
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE model_registry
            SET status = 'CHAMPION'
            WHERE model_id = ?
            """,
            (model_id,),
        )


def seed_chain_pool(storage, pool, count):
    for index in range(count):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": pool,
                "active_bin_id": 0,
                "bin_step": 25,
                "token_x_mint": f"{pool}-x",
                "token_y_mint": f"{pool}-y",
                "bin_arrays": [],
            },
            observed_at=(
                f"2026-09-23T12:{index // 60:02d}:"
                f"{index % 60:02d}+00:00"
            ),
        )


def filled_payload():
    return {
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "champion_model_id": "champion-1",
        "champion_dataset_version": "dataset-v1",
        "pools": [
            {
                "pool_address": "pool-a",
                "amount_x": 100,
                "amount_y": 200,
                "network_cost_y_atomic": 1000,
            },
            {
                "pool_address": "pool-b",
                "amount_x": 120,
                "amount_y": 180,
                "network_cost_y_atomic": 1100,
            },
            {
                "pool_address": "pool-c",
                "amount_x": 90,
                "amount_y": 210,
                "network_cost_y_atomic": 900,
            },
        ],
    }


def test_phase8_retrain_template_prefills_pools_but_not_economics(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    seed_chain_pool(storage, "pool-a", 5)
    seed_chain_pool(storage, "pool-b", 4)
    seed_chain_pool(storage, "pool-c", 3)

    template = build_phase8_retrain_input_template(storage)

    assert template["champion_model_id"] == "champion-1"
    assert template["champion_dataset_version"] == "dataset-v1"
    assert [item["pool_address"] for item in template["pools"]] == [
        "pool-a",
        "pool-b",
        "pool-c",
    ]
    assert all(item["amount_x"] is None for item in template["pools"])
    assert all(item["amount_y"] is None for item in template["pools"])
    assert all(
        item["network_cost_y_atomic"] is None
        for item in template["pools"]
    )

    with pytest.raises(ValueError, match="amount_x is required"):
        parse_phase8_retrain_inputs(template)


def test_phase8_retrain_inputs_round_trip_and_deduplicate(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    inputs = parse_phase8_retrain_inputs(filled_payload())

    first = persist_phase8_retrain_inputs(storage, inputs=inputs)
    second = persist_phase8_retrain_inputs(storage, inputs=inputs)
    loaded = load_phase8_retrain_inputs(
        storage,
        evidence_id=first.evidence_id,
    )
    audit = audit_phase8_retrain_inputs(storage)

    assert first.evidence_id == second.evidence_id
    assert loaded is not None
    assert loaded.artifact_sha256 == first.artifact_sha256
    assert loaded.inputs.to_record() == inputs.to_record()
    assert audit.exists is True
    assert audit.valid is True
    assert audit.champion_matches is True
    assert audit.evidence_id == first.evidence_id
    assert audit.artifact_sha256 == first.artifact_sha256
    assert audit.reasons == ()


def test_phase8_retrain_inputs_fail_closed_after_champion_rotation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    artifact = persist_phase8_retrain_inputs(
        storage,
        inputs=parse_phase8_retrain_inputs(filled_payload()),
    )
    storage.register_model(
        model_id="champion-2",
        model_family="ML_V1_HIST_GRADIENT_BOOSTING",
        feature_version="ML_ACTION_FEATURES_V1",
        dataset_version="dataset-v2",
        metrics={},
    )
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE model_registry
            SET status = 'ROLLED_BACK'
            WHERE model_id = 'champion-1'
            """
        )
        conn.execute(
            """
            UPDATE model_registry
            SET status = 'CHAMPION'
            WHERE model_id = 'champion-2'
            """
        )

    audit = audit_phase8_retrain_inputs(storage)

    assert artifact.evidence_id is not None
    assert audit.valid is False
    assert audit.exists is False
    assert audit.champion_matches is False
    assert "missing" in audit.reasons[0]


def test_phase8_retrain_inputs_reject_wrong_champion(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    payload = filled_payload()
    payload["champion_model_id"] = "different"

    with pytest.raises(ValueError, match="current champion"):
        persist_phase8_retrain_inputs(
            storage,
            inputs=parse_phase8_retrain_inputs(payload),
        )


def test_phase8_retrain_build_uses_validated_artifact_without_promotion(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    artifact = persist_phase8_retrain_inputs(
        storage,
        inputs=parse_phase8_retrain_inputs(filled_payload()),
    )
    calls = []

    monkeypatch.setattr(
        inputs_module,
        "build_continuous_learning_plan",
        lambda *args, **kwargs: SimpleNamespace(
            retrain_due=True,
            status="RETRAIN_DUE",
            champion_model_id="champion-1",
            champion_dataset_version="dataset-v1",
        ),
    )

    def fake_start(storage, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            to_record=lambda: {
                "cycle": {"cycle_id": "cycle-1"},
                "dataset": {"examples_built": 100},
                "output_file": str(kwargs["output_file"]),
            }
        )

    monkeypatch.setattr(
        inputs_module,
        "start_retraining_cycle_with_dataset",
        fake_start,
    )

    report = run_phase8_retrain_build_from_inputs(
        storage,
        artifact=artifact,
        as_of="2026-09-24T00:00:00+00:00",
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.input_evidence_id == artifact.evidence_id
    assert report.input_artifact_sha256 == artifact.artifact_sha256
    assert len(calls) == 1
    specs = calls[0]["pools"]
    assert [item.pool_address for item in specs] == [
        "pool-a",
        "pool-b",
        "pool-c",
    ]
    assert [item.amount_x for item in specs] == [100, 120, 90]
    assert calls[0]["as_of"] == "2026-09-24T00:00:00+00:00"
    assert "phase8_retraining_datasets" in str(
        calls[0]["output_file"]
    )


def test_phase8_retrain_build_refuses_when_retraining_is_not_due(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    artifact = persist_phase8_retrain_inputs(
        storage,
        inputs=parse_phase8_retrain_inputs(filled_payload()),
    )
    monkeypatch.setattr(
        inputs_module,
        "build_continuous_learning_plan",
        lambda *args, **kwargs: SimpleNamespace(
            retrain_due=False,
            status="WAITING_CHAIN_EVIDENCE",
            champion_model_id="champion-1",
            champion_dataset_version="dataset-v1",
        ),
    )

    with pytest.raises(ValueError, match="not due"):
        run_phase8_retrain_build_from_inputs(
            storage,
            artifact=artifact,
        )
