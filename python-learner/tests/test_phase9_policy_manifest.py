from dataclasses import replace

import meteora_learner.phase9_policy_manifest as manifest_module
from meteora_learner.phase9_policy_authorization import (
    PHASE9_POLICY_GATE_EVIDENCE_TYPE,
    PHASE9_POLICY_GATE_SCOPE,
)
from meteora_learner.phase9_policy_controlled_validation import (
    PHASE9_POLICY_CONTROLLED_VALIDATION_EVIDENCE_TYPE,
    PHASE9_POLICY_CONTROLLED_VALIDATION_SCOPE,
)
from meteora_learner.phase9_policy_manifest import (
    audit_persisted_phase9_policy_prewire_manifest,
    evaluate_phase9_policy_prewire_manifest,
    persist_phase9_policy_prewire_manifest,
)
from meteora_learner.phase9_policy_rollout_simulation import (
    PHASE9_POLICY_ROLLOUT_SIMULATION_EVIDENCE_TYPE,
    PHASE9_POLICY_ROLLOUT_SIMULATION_SCOPE,
)
from meteora_learner.phase9_policy_rollback_simulation import (
    PHASE9_POLICY_ROLLBACK_SIMULATION_EVIDENCE_TYPE,
    PHASE9_POLICY_ROLLBACK_SIMULATION_SCOPE,
)
from meteora_learner.storage import Storage


class DummyPrewire:
    ready = True
    reasons = ()

    def to_record(self):
        return {
            "ready": True,
            "research_only": True,
            "simulation_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "reasons": [],
        }


def seed_component(storage, edge_type, scope, *, marker):
    return storage.save_advanced_edge_evidence(
        edge_type=edge_type,
        pool_address=scope,
        as_of=None,
        status="READY",
        qualified=True,
        evidence={
            "research_only": True,
            "simulation_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "marker": marker,
        },
    )


def seed_policy_chain(storage):
    seed_component(
        storage,
        PHASE9_POLICY_GATE_EVIDENCE_TYPE,
        PHASE9_POLICY_GATE_SCOPE,
        marker="authorization",
    )
    seed_component(
        storage,
        PHASE9_POLICY_CONTROLLED_VALIDATION_EVIDENCE_TYPE,
        PHASE9_POLICY_CONTROLLED_VALIDATION_SCOPE,
        marker="controlled",
    )
    seed_component(
        storage,
        PHASE9_POLICY_ROLLOUT_SIMULATION_EVIDENCE_TYPE,
        PHASE9_POLICY_ROLLOUT_SIMULATION_SCOPE,
        marker="rollout",
    )
    seed_component(
        storage,
        PHASE9_POLICY_ROLLBACK_SIMULATION_EVIDENCE_TYPE,
        PHASE9_POLICY_ROLLBACK_SIMULATION_SCOPE,
        marker="rollback",
    )


def patch_prewire(monkeypatch):
    monkeypatch.setattr(
        manifest_module,
        "evaluate_phase9_policy_prewire_audit",
        lambda storage: DummyPrewire(),
    )


def test_prewire_manifest_binds_exact_component_evidence(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_policy_chain(storage)
    patch_prewire(monkeypatch)

    report = evaluate_phase9_policy_prewire_manifest(storage)

    assert report.manifest_ready is True
    assert report.prewire_ready is True
    assert report.manifest_sha256 is not None
    assert len(report.manifest_sha256) == 64
    assert report.authorization is not None
    assert report.controlled_validation is not None
    assert report.rollout_simulation is not None
    assert report.rollback_simulation is not None
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_persisted_prewire_manifest_is_current_when_chain_matches(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_policy_chain(storage)
    patch_prewire(monkeypatch)
    report = evaluate_phase9_policy_prewire_manifest(storage)

    evidence_id = persist_phase9_policy_prewire_manifest(
        storage,
        report=report,
    )
    audit = audit_persisted_phase9_policy_prewire_manifest(storage)

    assert evidence_id > 0
    assert audit.current is True
    assert audit.persisted_matches_current is True
    assert audit.current_manifest_ready is True
    assert audit.manifest_sha256 == report.manifest_sha256
    assert audit.reasons == ()


def test_prewire_manifest_becomes_stale_after_new_component_evidence(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_policy_chain(storage)
    patch_prewire(monkeypatch)
    report = evaluate_phase9_policy_prewire_manifest(storage)
    persist_phase9_policy_prewire_manifest(
        storage,
        report=report,
    )

    seed_component(
        storage,
        PHASE9_POLICY_GATE_EVIDENCE_TYPE,
        PHASE9_POLICY_GATE_SCOPE,
        marker="authorization-new",
    )
    audit = audit_persisted_phase9_policy_prewire_manifest(storage)

    assert audit.current is False
    assert audit.persisted_matches_current is False
    assert any(
        "stale versus current evidence" in reason
        for reason in audit.reasons
    )


def test_prewire_manifest_persistence_refuses_actionable_forgery(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_policy_chain(storage)
    patch_prewire(monkeypatch)
    report = evaluate_phase9_policy_prewire_manifest(storage)
    forged = replace(report, policy_actionable=True)

    try:
        persist_phase9_policy_prewire_manifest(
            storage,
            report=forged,
        )
    except ValueError as exc:
        assert "must not grant LIVE policy authority" in str(exc)
    else:
        raise AssertionError("expected actionable manifest refusal")
