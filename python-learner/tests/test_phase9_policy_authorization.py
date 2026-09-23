from dataclasses import replace

import meteora_learner.phase9_policy_authorization as gate_module
from meteora_learner.phase9_policy_authorization import (
    Phase9PolicyAuthorizationCriteria,
    audit_persisted_phase9_policy_authorization,
    evaluate_phase9_policy_authorization,
    persist_phase9_policy_authorization,
)
from meteora_learner.phase9_shadow import (
    Phase9ShadowCriteria,
    Phase9ShadowReport,
    persist_phase9_shadow,
)
from meteora_learner.phase9_validation import Phase9ResearchBundleCriteria
from meteora_learner.storage import Storage


class DummyAudit:
    current = True
    reasons = ()

    def to_record(self):
        return {"current": True, "reasons": []}


def shadow_report(
    cycle_id,
    *,
    dataset_sha,
    cutoff,
    decisions=60,
    pools=3,
    selected_arms=2,
    uplift=10.0,
    regret=100.0,
    criteria=None,
):
    criteria = criteria or Phase9ShadowCriteria(
        min_decisions=50,
        min_pools=3,
        min_selected_arms=2,
        min_mean_uplift_vs_baseline_bps=0.0,
        max_mean_regret_vs_oracle_bps=300.0,
    )
    return Phase9ShadowReport(
        research_only=True,
        policy_actionable=False,
        phase9_promotion_exists=True,
        phase9_current=True,
        phase9_promoted_at="2026-09-23T12:00:00+00:00",
        cycle_id=cycle_id,
        dataset_version=f"ML_ACTION_DATASET_V1:{dataset_sha[:16]}",
        dataset_sha256=dataset_sha,
        dataset_cutoff=cutoff,
        cutoff_after_promotion=True,
        seconds_after_promotion=3600.0,
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
                "decisions_evaluated": decisions,
                "pools_evaluated": pools,
                "selected_arms": selected_arms,
                "mean_uplift_vs_baseline_bps": uplift,
                "mean_regret_vs_oracle_bps": regret,
            },
        },
        reasons=(),
    )


def patch_phase9(monkeypatch, reports):
    monkeypatch.setattr(
        gate_module,
        "_persisted_phase9_context",
        lambda storage: (
            "2026-09-23T12:00:00+00:00",
            Phase9ResearchBundleCriteria(),
            (),
        ),
    )
    monkeypatch.setattr(
        gate_module,
        "audit_persisted_phase9_promotion",
        lambda storage, criteria: DummyAudit(),
    )
    monkeypatch.setattr(
        gate_module,
        "evaluate_phase9_shadow",
        lambda storage, cycle_id, criteria: reports[cycle_id],
    )


def test_three_independent_shadow_corpora_can_make_gate_ready(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    reports = {
        "cycle-a": shadow_report(
            "cycle-a",
            dataset_sha="a" * 64,
            cutoff="2026-09-23T13:00:00+00:00",
        ),
        "cycle-b": shadow_report(
            "cycle-b",
            dataset_sha="b" * 64,
            cutoff="2026-09-23T14:00:00+00:00",
        ),
        "cycle-c": shadow_report(
            "cycle-c",
            dataset_sha="c" * 64,
            cutoff="2026-09-23T15:00:00+00:00",
        ),
    }
    patch_phase9(monkeypatch, reports)
    for report in reports.values():
        persist_phase9_shadow(storage, report=report)

    gate = evaluate_phase9_policy_authorization(storage)

    assert gate.authorization_ready is True
    assert gate.replay_verified_shadow_runs == 3
    assert gate.qualifying_shadow_runs == 3
    assert gate.distinct_dataset_hashes == 3
    assert gate.distinct_cutoffs == 3
    assert gate.total_decisions == 180
    assert gate.policy_actionable is False
    assert gate.execution_wired is False
    assert gate.reasons == ()


def test_duplicate_persistence_does_not_inflate_shadow_run_count(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    report = shadow_report(
        "cycle-a",
        dataset_sha="a" * 64,
        cutoff="2026-09-23T13:00:00+00:00",
    )
    reports = {"cycle-a": report}
    patch_phase9(monkeypatch, reports)
    persist_phase9_shadow(storage, report=report)
    persist_phase9_shadow(storage, report=report)

    gate = evaluate_phase9_policy_authorization(
        storage,
        criteria=Phase9PolicyAuthorizationCriteria(
            min_shadow_runs=2,
            min_distinct_dataset_hashes=2,
            min_distinct_cutoffs=2,
            min_decisions_per_run=50,
            min_pools_per_run=3,
            min_selected_arms_per_run=2,
            min_total_decisions=100,
        ),
    )

    assert gate.shadow_records_seen == 2
    assert gate.unique_shadow_cycles_seen == 1
    assert gate.qualifying_shadow_runs == 1
    assert gate.authorization_ready is False


def test_weaker_shadow_threshold_does_not_satisfy_gate(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    weak = Phase9ShadowCriteria(
        min_decisions=10,
        min_pools=1,
        min_selected_arms=1,
        min_mean_uplift_vs_baseline_bps=-100.0,
        max_mean_regret_vs_oracle_bps=1_000.0,
    )
    report = shadow_report(
        "cycle-a",
        dataset_sha="a" * 64,
        cutoff="2026-09-23T13:00:00+00:00",
        decisions=100,
        pools=5,
        selected_arms=3,
        criteria=weak,
    )
    reports = {"cycle-a": report}
    patch_phase9(monkeypatch, reports)
    persist_phase9_shadow(storage, report=report)

    gate = evaluate_phase9_policy_authorization(
        storage,
        criteria=Phase9PolicyAuthorizationCriteria(
            min_shadow_runs=1,
            min_distinct_dataset_hashes=1,
            min_distinct_cutoffs=1,
            min_decisions_per_run=50,
            min_pools_per_run=3,
            min_selected_arms_per_run=2,
            min_total_decisions=50,
        ),
    )

    assert gate.authorization_ready is False
    assert gate.qualifying_shadow_runs == 0
    assert gate.shadow_evidence[0].criteria_strong_enough is False


def test_authorization_evidence_cannot_be_execution_wired(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    report = shadow_report(
        "cycle-a",
        dataset_sha="a" * 64,
        cutoff="2026-09-23T13:00:00+00:00",
    )
    reports = {"cycle-a": report}
    patch_phase9(monkeypatch, reports)
    persist_phase9_shadow(storage, report=report)
    gate = evaluate_phase9_policy_authorization(
        storage,
        criteria=Phase9PolicyAuthorizationCriteria(
            min_shadow_runs=1,
            min_distinct_dataset_hashes=1,
            min_distinct_cutoffs=1,
            min_decisions_per_run=50,
            min_pools_per_run=3,
            min_selected_arms_per_run=2,
            min_total_decisions=50,
        ),
    )
    assert gate.authorization_ready is True

    evidence_id = persist_phase9_policy_authorization(
        storage,
        report=gate,
    )
    assert evidence_id > 0
    persisted = storage.latest_advanced_edge_evidence(
        edge_type="PHASE9_LIVE_POLICY_AUTHORIZATION_GATE_V1",
        pool_address="__PHASE9_LIVE_POLICY_GATE__",
    )
    assert persisted is not None
    assert persisted["evidence"]["policy_actionable"] is False
    assert persisted["evidence"]["execution_wired"] is False

    forged = replace(gate, execution_wired=True)
    try:
        persist_phase9_policy_authorization(
            storage,
            report=forged,
        )
    except ValueError as exc:
        assert "must not be wired to execution" in str(exc)
    else:
        raise AssertionError("expected execution-wired evidence refusal")


def test_authorization_audit_is_current_when_replay_matches(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    report = shadow_report(
        "cycle-a",
        dataset_sha="a" * 64,
        cutoff="2026-09-23T13:00:00+00:00",
    )
    reports = {"cycle-a": report}
    patch_phase9(monkeypatch, reports)
    persist_phase9_shadow(storage, report=report)
    gate = evaluate_phase9_policy_authorization(
        storage,
        criteria=Phase9PolicyAuthorizationCriteria(
            min_shadow_runs=1,
            min_distinct_dataset_hashes=1,
            min_distinct_cutoffs=1,
            min_decisions_per_run=50,
            min_pools_per_run=3,
            min_selected_arms_per_run=2,
            min_total_decisions=50,
        ),
    )
    persist_phase9_policy_authorization(
        storage,
        report=gate,
    )

    monkeypatch.setattr(
        gate_module,
        "evaluate_phase9_policy_authorization",
        lambda storage, criteria: gate,
    )
    audit = audit_persisted_phase9_policy_authorization(storage)

    assert audit.current is True
    assert audit.current_authorization_ready is True
    assert audit.persisted_matches_current is True
    assert audit.reasons == ()


def test_authorization_audit_revokes_stale_ready_evidence(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    report = shadow_report(
        "cycle-a",
        dataset_sha="a" * 64,
        cutoff="2026-09-23T13:00:00+00:00",
    )
    reports = {"cycle-a": report}
    patch_phase9(monkeypatch, reports)
    persist_phase9_shadow(storage, report=report)
    gate = evaluate_phase9_policy_authorization(
        storage,
        criteria=Phase9PolicyAuthorizationCriteria(
            min_shadow_runs=1,
            min_distinct_dataset_hashes=1,
            min_distinct_cutoffs=1,
            min_decisions_per_run=50,
            min_pools_per_run=3,
            min_selected_arms_per_run=2,
            min_total_decisions=50,
        ),
    )
    persist_phase9_policy_authorization(
        storage,
        report=gate,
    )

    stale = replace(
        gate,
        authorization_ready=False,
        total_decisions=0,
        reasons=("shadow corpus no longer passes",),
    )
    monkeypatch.setattr(
        gate_module,
        "evaluate_phase9_policy_authorization",
        lambda storage, criteria: stale,
    )
    audit = audit_persisted_phase9_policy_authorization(storage)

    assert audit.current is False
    assert audit.current_authorization_ready is False
    assert audit.persisted_matches_current is False
    assert any(
        "no longer passes" in reason
        for reason in audit.reasons
    )
