from pathlib import Path
from types import SimpleNamespace
import hashlib

import pytest

import meteora_learner.phase8_offline_retraining as offline_module
from meteora_learner.phase8_offline_retraining import (
    phase8_cycle_dataset_lineage,
    train_phase8_cycle_challenger,
    validate_phase8_cycle_challenger_offline,
)
from meteora_learner.retraining_workflow import (
    RETRAIN_DATASET_EVIDENCE_TYPE,
)
from meteora_learner.storage import Storage


def cycle(**overrides):
    base = dict(
        cycle_id="cycle-1",
        status="PLANNED",
        champion_model_id="champion-1",
        plan_as_of="2026-09-24T00:00:00+00:00",
        target_dataset_version=None,
        challenger_model_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def seed_model(storage, model_id="champion-1"):
    storage.register_model(
        model_id=model_id,
        model_family="ML_V1_HIST_GRADIENT_BOOSTING",
        feature_version="ML_ACTION_FEATURES_V1",
        dataset_version="dataset-v1",
        metrics={},
    )


def dataset_file(tmp_path):
    path = (tmp_path / "dataset.csv").resolve()
    raw = (
        "decision_observed_at,forward_end_observed_at,value\n"
        "2026-09-23T22:00:00+00:00,"
        "2026-09-23T23:00:00+00:00,1\n"
    ).encode("utf-8")
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    return path, digest, f"ML_ACTION_DATASET_V1:{digest[:16]}"


def test_cycle_dataset_lineage_verifies_file_bytes(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_model(storage)
    path, digest, version = dataset_file(tmp_path)
    selected_cycle = cycle(target_dataset_version=version)
    monkeypatch.setattr(
        offline_module,
        "_selected_cycle",
        lambda storage, cycle_id: selected_cycle,
    )
    storage.save_model_live_evidence(
        model_id="champion-1",
        evidence_type=RETRAIN_DATASET_EVIDENCE_TYPE,
        status="BUILT",
        evidence={
            "cycle_id": "cycle-1",
            "cutoff": selected_cycle.plan_as_of,
            "target_dataset_version": version,
            "dataset": {
                "dataset_sha256": digest,
                "dataset_version": version,
            },
            "output_file": str(path),
        },
    )

    lineage = phase8_cycle_dataset_lineage(
        storage,
        cycle_id="cycle-1",
    )

    assert lineage.cycle_id == "cycle-1"
    assert lineage.dataset_sha256 == digest
    assert lineage.dataset_version == version
    assert lineage.output_file == str(path)

    path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        phase8_cycle_dataset_lineage(
            storage,
            cycle_id="cycle-1",
        )


def test_offline_train_uses_deterministic_model_and_artifact_paths(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    selected_cycle = cycle()
    lineage = SimpleNamespace(
        output_file=str((tmp_path / "dataset.csv").resolve()),
        to_record=lambda: {},
    )
    calls = []

    monkeypatch.setattr(
        offline_module,
        "_selected_cycle",
        lambda storage, cycle_id: selected_cycle,
    )
    monkeypatch.setattr(
        offline_module,
        "phase8_cycle_dataset_lineage",
        lambda *args, **kwargs: lineage,
    )

    def fake_train(storage, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            to_record=lambda: {"model_id": kwargs["model_id"]}
        )

    monkeypatch.setattr(
        offline_module,
        "train_retraining_cycle_challenger",
        fake_train,
    )

    report = train_phase8_cycle_challenger(storage)

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert len(calls) == 1
    assert calls[0]["cycle_id"] == "cycle-1"
    assert calls[0]["model_id"] == "phase8-challenger-cycle-1"
    assert "phase8_ml_artifacts" in str(
        calls[0]["artifact_directory"]
    )


def test_offline_train_refuses_non_planned_cycle(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        offline_module,
        "_selected_cycle",
        lambda storage, cycle_id: cycle(
            status="CHALLENGER_REGISTERED",
            challenger_model_id="challenger-1",
        ),
    )

    with pytest.raises(ValueError, match="unattached PLANNED"):
        train_phase8_cycle_challenger(storage)


def test_offline_validation_stops_when_walk_forward_fails(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    selected_cycle = cycle(
        status="CHALLENGER_REGISTERED",
        challenger_model_id="challenger-1",
    )
    monkeypatch.setattr(
        offline_module,
        "_selected_cycle",
        lambda storage, cycle_id: selected_cycle,
    )
    monkeypatch.setattr(
        storage,
        "model_registry_entry",
        lambda model_id: {
            "model_id": model_id,
            "status": "OFFLINE_CANDIDATE",
        },
    )
    monkeypatch.setattr(
        offline_module,
        "phase8_cycle_dataset_lineage",
        lambda *args, **kwargs: SimpleNamespace(
            output_file=str(tmp_path / "dataset.csv"),
        ),
    )
    walk = SimpleNamespace(
        report=SimpleNamespace(walk_forward_qualified=False),
        to_record=lambda: {"qualified": False},
    )
    monkeypatch.setattr(
        offline_module,
        "evaluate_retraining_cycle_walk_forward",
        lambda *args, **kwargs: walk,
    )
    monkeypatch.setattr(
        offline_module,
        "evaluate_registered_offline_challenger",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("held-out validation must not run")
        ),
    )
    monkeypatch.setattr(
        offline_module,
        "sync_retraining_cycle",
        lambda *args, **kwargs: SimpleNamespace(
            status="CHALLENGER_REGISTERED"
        ),
    )

    report = validate_phase8_cycle_challenger_offline(storage)

    assert report.walk_forward_qualified is False
    assert report.offline_validation is None
    assert report.offline_qualified is False
    assert report.model_status_after == "OFFLINE_CANDIDATE"
    assert report.cycle_status_after == "CHALLENGER_REGISTERED"


def test_offline_validation_qualifies_only_after_both_gates(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    selected_cycle = cycle(
        status="CHALLENGER_REGISTERED",
        challenger_model_id="challenger-1",
    )
    dataset = tmp_path / "dataset.csv"
    dataset.write_text("x\n1\n", encoding="utf-8")
    model_status = {"value": "OFFLINE_CANDIDATE"}
    calls = []

    monkeypatch.setattr(
        offline_module,
        "_selected_cycle",
        lambda storage, cycle_id: selected_cycle,
    )
    monkeypatch.setattr(
        storage,
        "model_registry_entry",
        lambda model_id: {
            "model_id": model_id,
            "status": model_status["value"],
        },
    )
    monkeypatch.setattr(
        offline_module,
        "phase8_cycle_dataset_lineage",
        lambda *args, **kwargs: SimpleNamespace(
            output_file=str(dataset),
        ),
    )
    walk = SimpleNamespace(
        report=SimpleNamespace(walk_forward_qualified=True),
        to_record=lambda: {"qualified": True},
    )
    monkeypatch.setattr(
        offline_module,
        "evaluate_retraining_cycle_walk_forward",
        lambda *args, **kwargs: walk,
    )
    preview = SimpleNamespace(
        offline_qualified=True,
        to_record=lambda: {"offline_qualified": True},
    )
    monkeypatch.setattr(
        offline_module,
        "evaluate_registered_offline_challenger",
        lambda *args, **kwargs: preview,
    )

    def qualify(*args, **kwargs):
        calls.append("qualified")
        model_status["value"] = "OFFLINE_QUALIFIED"
        return preview, SimpleNamespace(status="OFFLINE_QUALIFIED")

    monkeypatch.setattr(
        offline_module,
        "qualify_registered_offline_challenger",
        qualify,
    )
    monkeypatch.setattr(
        offline_module,
        "sync_retraining_cycle",
        lambda *args, **kwargs: SimpleNamespace(
            status="OFFLINE_QUALIFIED"
        ),
    )

    report = validate_phase8_cycle_challenger_offline(storage)

    assert calls == ["qualified"]
    assert report.walk_forward_qualified is True
    assert report.offline_qualified is True
    assert report.model_status_after == "OFFLINE_QUALIFIED"
    assert report.cycle_status_after == "OFFLINE_QUALIFIED"
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_offline_validation_does_not_qualify_failed_held_out_gate(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    selected_cycle = cycle(
        status="CHALLENGER_REGISTERED",
        challenger_model_id="challenger-1",
    )
    dataset = tmp_path / "dataset.csv"
    dataset.write_text("x\n1\n", encoding="utf-8")
    monkeypatch.setattr(
        offline_module,
        "_selected_cycle",
        lambda storage, cycle_id: selected_cycle,
    )
    monkeypatch.setattr(
        storage,
        "model_registry_entry",
        lambda model_id: {
            "model_id": model_id,
            "status": "OFFLINE_CANDIDATE",
        },
    )
    monkeypatch.setattr(
        offline_module,
        "phase8_cycle_dataset_lineage",
        lambda *args, **kwargs: SimpleNamespace(
            output_file=str(dataset),
        ),
    )
    monkeypatch.setattr(
        offline_module,
        "evaluate_retraining_cycle_walk_forward",
        lambda *args, **kwargs: SimpleNamespace(
            report=SimpleNamespace(walk_forward_qualified=True),
            to_record=lambda: {"qualified": True},
        ),
    )
    preview = SimpleNamespace(
        offline_qualified=False,
        to_record=lambda: {"offline_qualified": False},
    )
    monkeypatch.setattr(
        offline_module,
        "evaluate_registered_offline_challenger",
        lambda *args, **kwargs: preview,
    )
    monkeypatch.setattr(
        offline_module,
        "qualify_registered_offline_challenger",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("failed held-out gate must not qualify")
        ),
    )
    monkeypatch.setattr(
        offline_module,
        "sync_retraining_cycle",
        lambda *args, **kwargs: SimpleNamespace(
            status="CHALLENGER_REGISTERED"
        ),
    )

    report = validate_phase8_cycle_challenger_offline(storage)

    assert report.walk_forward_qualified is True
    assert report.offline_qualified is False
    assert report.offline_validation == {
        "offline_qualified": False
    }
