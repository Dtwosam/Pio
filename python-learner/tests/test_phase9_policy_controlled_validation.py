from dataclasses import replace
from datetime import datetime, timedelta

import meteora_learner.phase9_policy_controlled_validation as validation_module
from meteora_learner.phase9_policy_authorization import (
    PHASE9_POLICY_GATE_EVIDENCE_TYPE,
    PHASE9_POLICY_GATE_SCOPE,
)
from meteora_learner.phase9_policy_controlled_validation import (
    Phase9PolicyControlledValidationCriteria,
    audit_persisted_phase9_policy_controlled_validation,
    evaluate_phase9_policy_controlled_validation,
    persist_phase9_policy_controlled_validation,
)
from meteora_learner.phase9_shadow import (
    Phase9ShadowCriteria,
    Phase9ShadowReport,
)
from meteora_learner.storage import Storage


class DummyAuthorizationAudit:
    current = True
    reasons = ()

    def to_record(self):
        return {"current": True, "reasons": []}


def save_authorization(storage):
    return storage.save_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_GATE_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_GATE_SCOPE,
        as_of=None,
        status="AUTHORIZATION_EVIDENCE_READY",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "qualifying_evidence_ids": [11],
            "shadow_evidence": [
                {
                    "evidence_id": 11,
                    "cycle_id": "cycle-a",
                    "dataset_sha256": "a" * 64,
                    "dataset_cutoff": "2026-09-23T12:00:00+00:00",
                }
            ],
        },
    )


def shadow_report(cycle_id, *, dataset_sha, cutoff):
    criteria = Phase9ShadowCriteria(
        min_decisions=50,
        min_pools=3,
        min_selected_arms=2,
        min_mean_uplift_vs_baseline_bps=0.0,
        max_mean_regret_vs_oracle_bps=250.0,
    )
    return Phase9ShadowReport(
        research_only=True,
        policy_actionable=False,
        phase9_promotion_exists=True,
        phase9_current=True,
        phase9_promoted_at="2026-09-23T10:00:00+00:00",
        cycle_id=cycle_id,
        dataset_version=f"ML_ACTION_DATASET_V1:{dataset_sha[:16]}",
        dataset_sha256=dataset_sha,
        dataset_cutoff=cutoff,
        cutoff_after_promotion=True,
        seconds_after_promotion=7200.0,
        bandit_research_qualified=True,
        shadow_ready=True,
        criteria=criteria,
        phase9_audit={"current": True},
        cycle_result={
            "lineage": {
                "cycle_id": cycle_id,
                "dataset_sha256": dataset_sha,
                "cutoff": cutoff,
            },
            "report": {
                "research_qualified": True,
                "decisions_evaluated": 60,
                "pools_evaluated": 3,
                "selected_arms": 2,
                "mean_uplift_vs_baseline_bps": 10.0,
                "mean_regret_vs_oracle_bps": 100.0,
            },
        },
        reasons=(),
    )


def fresh_cutoff(storage):
    latest = storage.latest_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_GATE_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_GATE_SCOPE,
    )
    assert latest is not None
    created_at = datetime.fromisoformat(
        latest["created_at"].replace("Z", "+00:00")
    )
    return (created_at + timedelta(minutes=5)).isoformat()


def patch_authorization(monkeypatch):
    monkeypatch.setattr(
        validation_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: DummyAuthorizationAudit(),
    )


def test_fresh_independent_holdout_can_pass_controlled_validation(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_authorization(storage)
    cutoff = fresh_cutoff(storage)
    report = shadow_report(
        "cycle-b",
        dataset_sha="b" * 64,
        cutoff=cutoff,
    )
    patch_authorization(monkeypatch)
    monkeypatch.setattr(
        validation_module,
        "evaluate_phase9_shadow",
        lambda storage, cycle_id, criteria: report,
    )

    result = evaluate_phase9_policy_controlled_validation(
        storage,
        cycle_id="cycle-b",
    )

    assert result.controlled_validation_ready is True
    assert result.simulation_only is True
    assert result.policy_actionable is False
    assert result.execution_wired is False
    assert result.cycle_independent is True
    assert result.dataset_independent is True
    assert result.cutoff_after_authorization is True


def test_reused_authorization_dataset_fails_controlled_validation(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_authorization(storage)
    cutoff = fresh_cutoff(storage)
    report = shadow_report(
        "cycle-b",
        dataset_sha="a" * 64,
        cutoff=cutoff,
    )
    patch_authorization(monkeypatch)
    monkeypatch.setattr(
        validation_module,
        "evaluate_phase9_shadow",
        lambda storage, cycle_id, criteria: report,
    )

    result = evaluate_phase9_policy_controlled_validation(
        storage,
        cycle_id="cycle-b",
    )

    assert result.controlled_validation_ready is False
    assert result.dataset_independent is False
    assert any(
        "dataset was already used" in reason
        for reason in result.reasons
    )


def test_controlled_validation_persistence_refuses_execution_wiring(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_authorization(storage)
    cutoff = fresh_cutoff(storage)
    report = shadow_report(
        "cycle-b",
        dataset_sha="b" * 64,
        cutoff=cutoff,
    )
    patch_authorization(monkeypatch)
    monkeypatch.setattr(
        validation_module,
        "evaluate_phase9_shadow",
        lambda storage, cycle_id, criteria: report,
    )
    result = evaluate_phase9_policy_controlled_validation(
        storage,
        cycle_id="cycle-b",
    )
    assert result.controlled_validation_ready is True

    evidence_id = persist_phase9_policy_controlled_validation(
        storage,
        report=result,
    )
    assert evidence_id > 0

    forged = replace(result, execution_wired=True)
    try:
        persist_phase9_policy_controlled_validation(
            storage,
            report=forged,
        )
    except ValueError as exc:
        assert "must not be wired to execution" in str(exc)
    else:
        raise AssertionError("expected execution-wired evidence refusal")


def test_controlled_validation_audit_is_current_when_replay_matches(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_authorization(storage)
    cutoff = fresh_cutoff(storage)
    shadow = shadow_report(
        "cycle-b",
        dataset_sha="b" * 64,
        cutoff=cutoff,
    )
    patch_authorization(monkeypatch)
    monkeypatch.setattr(
        validation_module,
        "evaluate_phase9_shadow",
        lambda storage, cycle_id, criteria: shadow,
    )
    result = evaluate_phase9_policy_controlled_validation(
        storage,
        cycle_id="cycle-b",
        criteria=Phase9PolicyControlledValidationCriteria(),
    )
    persist_phase9_policy_controlled_validation(
        storage,
        report=result,
    )

    audit = audit_persisted_phase9_policy_controlled_validation(storage)

    assert audit.current is True
    assert audit.current_validation_ready is True
    assert audit.persisted_matches_current is True
    assert audit.reasons == ()


def test_malformed_authorization_lineage_fails_closed_without_crashing(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_POLICY_GATE_EVIDENCE_TYPE,
        pool_address=PHASE9_POLICY_GATE_SCOPE,
        as_of=None,
        status="AUTHORIZATION_EVIDENCE_READY",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "qualifying_evidence_ids": ["not-an-id"],
            "shadow_evidence": [
                {
                    "evidence_id": "also-bad",
                    "cycle_id": "cycle-a",
                    "dataset_sha256": "a" * 64,
                }
            ],
        },
    )
    cutoff = fresh_cutoff(storage)
    report = shadow_report(
        "cycle-b",
        dataset_sha="b" * 64,
        cutoff=cutoff,
    )
    monkeypatch.setattr(
        validation_module,
        "audit_persisted_phase9_policy_authorization",
        lambda storage: type(
            "Audit",
            (),
            {
                "current": False,
                "reasons": ("authorization evidence is malformed",),
                "to_record": lambda self: {
                    "current": False,
                    "reasons": ["authorization evidence is malformed"],
                },
            },
        )(),
    )
    monkeypatch.setattr(
        validation_module,
        "evaluate_phase9_shadow",
        lambda storage, cycle_id, criteria: report,
    )

    result = evaluate_phase9_policy_controlled_validation(
        storage,
        cycle_id="cycle-b",
    )

    assert result.controlled_validation_ready is False
    assert result.authorization_current is False
    assert any(
        "authorization currentness:" in reason
        for reason in result.reasons
    )
