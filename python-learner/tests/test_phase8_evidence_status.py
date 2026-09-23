from types import SimpleNamespace

import meteora_learner.phase8_evidence_status as status_module
from meteora_learner.continuous_learning import (
    ContinuousLearningCriteria,
)
from meteora_learner.phase8_evidence_status import (
    evaluate_phase8_evidence_status,
)
from meteora_learner.phase8_validation import Phase8PromotionCriteria
from meteora_learner.storage import Storage


def test_phase8_evidence_status_consolidates_learning_cycle_and_live_gate(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    learning = SimpleNamespace(
        phase7_promoted=True,
        champion_model_id="champion-1",
        champion_dataset_version="dataset-v1",
        champion_age_days=15.5,
        active_challenger_model_ids=("challenger-1",),
        status="CHALLENGER_IN_PROGRESS",
        retrain_due=False,
        new_chain_observations=650,
        new_chain_pools=4,
        new_live_labels=7,
        reasons=("challenger already active",),
    )
    live = SimpleNamespace(
        status="HEALTHY",
        label_count=14,
        distinct_pools=3,
        rollback_recommended=False,
    )
    promotion = SimpleNamespace(
        live_champion=live,
        completed_cycles=2,
        champion_cycle_id="cycle-2",
        continuous_promotion_evidence_id=91,
        promotion_ready=True,
        reasons=(),
    )
    persisted = SimpleNamespace(
        current=True,
        reasons=(),
    )
    cycle = SimpleNamespace(
        cycle_id="cycle-3",
        status="PLANNED",
        challenger_model_id="challenger-1",
    )

    monkeypatch.setattr(
        status_module,
        "build_continuous_learning_plan",
        lambda *args, **kwargs: learning,
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase8_promotion",
        lambda *args, **kwargs: promotion,
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion",
        lambda storage: persisted,
    )
    monkeypatch.setattr(
        status_module,
        "active_retraining_cycle",
        lambda storage: cycle,
    )

    report = evaluate_phase8_evidence_status(
        storage,
        learning_criteria=ContinuousLearningCriteria(
            min_new_chain_observations=500,
            min_new_chain_pools=3,
            min_new_live_labels=5,
            max_champion_age_days=14.0,
        ),
        promotion_criteria=Phase8PromotionCriteria(
            min_completed_cycles=1,
            min_live_labels=10,
            min_live_pools=2,
        ),
        as_of="2026-09-24T00:00:00+00:00",
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.phase7_promoted is True
    assert report.champion_model_id == "champion-1"
    assert report.champion_dataset_version == "dataset-v1"
    assert report.champion_age_days == 15.5
    assert report.retrain_status == "CHALLENGER_IN_PROGRESS"
    assert report.retrain_due is False
    assert report.new_chain_observations == 650
    assert report.required_new_chain_observations == 500
    assert report.new_chain_pools == 4
    assert report.required_new_chain_pools == 3
    assert report.new_live_labels_since_champion == 7
    assert report.retrain_live_label_trigger == 5
    assert report.champion_age_trigger_days == 14.0
    assert report.active_cycle_id == "cycle-3"
    assert report.active_cycle_status == "PLANNED"
    assert report.active_cycle_challenger_model_id == "challenger-1"
    assert report.active_cycle_challenger_status == "MISSING"
    assert report.completed_cycles == 2
    assert report.champion_cycle_id == "cycle-2"
    assert report.continuous_promotion_evidence_id == 91
    assert report.live_champion_status == "HEALTHY"
    assert report.live_label_count == 14
    assert report.required_live_labels == 10
    assert report.live_distinct_pools == 3
    assert report.required_live_pools == 2
    assert report.live_rollback_recommended is False
    assert report.promotion_ready is True
    assert report.persisted_phase8_current is True
    assert report.reasons == ("challenger already active",)


def test_phase8_evidence_status_surfaces_missing_prerequisites(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    learning = SimpleNamespace(
        phase7_promoted=False,
        champion_model_id=None,
        champion_dataset_version=None,
        champion_age_days=None,
        active_challenger_model_ids=(),
        status="BLOCKED_PHASE7",
        retrain_due=False,
        new_chain_observations=0,
        new_chain_pools=0,
        new_live_labels=0,
        reasons=(
            "Phase 7 must be persistently promoted before continuous retraining",
        ),
    )
    promotion = SimpleNamespace(
        live_champion=None,
        completed_cycles=0,
        champion_cycle_id=None,
        continuous_promotion_evidence_id=None,
        promotion_ready=False,
        reasons=(
            "Phase 7 must be persistently promoted before Phase 8",
            "Phase 8 requires a current champion",
        ),
    )
    persisted = SimpleNamespace(
        current=False,
        reasons=("persisted Phase 8 promotion evidence is missing",),
    )

    monkeypatch.setattr(
        status_module,
        "build_continuous_learning_plan",
        lambda *args, **kwargs: learning,
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase8_promotion",
        lambda *args, **kwargs: promotion,
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion",
        lambda storage: persisted,
    )
    monkeypatch.setattr(
        status_module,
        "active_retraining_cycle",
        lambda storage: None,
    )

    report = evaluate_phase8_evidence_status(storage)

    assert report.phase7_promoted is False
    assert report.champion_model_id is None
    assert report.active_cycle_id is None
    assert report.active_cycle_challenger_status is None
    assert report.live_champion_status is None
    assert report.live_label_count == 0
    assert report.live_distinct_pools == 0
    assert report.promotion_ready is False
    assert report.persisted_phase8_current is False
    assert any("Phase 7" in reason for reason in report.reasons)
    assert any("current champion" in reason for reason in report.reasons)
    assert any("promotion evidence is missing" in reason for reason in report.reasons)


def test_phase8_evidence_status_uses_requested_criteria(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seen = {}

    def learning(storage, *, criteria, as_of):
        seen["learning"] = criteria
        seen["as_of"] = as_of
        return SimpleNamespace(
            phase7_promoted=True,
            champion_model_id="champion",
            champion_dataset_version="v",
            champion_age_days=1.0,
            active_challenger_model_ids=(),
            status="WAITING_CHAIN_EVIDENCE",
            retrain_due=False,
            new_chain_observations=10,
            new_chain_pools=1,
            new_live_labels=0,
            reasons=(),
        )

    def promotion(storage, *, criteria):
        seen["promotion"] = criteria
        return SimpleNamespace(
            live_champion=SimpleNamespace(
                status="INSUFFICIENT_EVIDENCE",
                label_count=1,
                distinct_pools=1,
                rollback_recommended=False,
            ),
            completed_cycles=0,
            champion_cycle_id=None,
            continuous_promotion_evidence_id=None,
            promotion_ready=False,
            reasons=(),
        )

    monkeypatch.setattr(
        status_module,
        "build_continuous_learning_plan",
        learning,
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase8_promotion",
        promotion,
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=False, reasons=()),
    )
    monkeypatch.setattr(
        status_module,
        "active_retraining_cycle",
        lambda storage: None,
    )

    learning_criteria = ContinuousLearningCriteria(
        min_new_chain_observations=100,
        min_new_chain_pools=2,
        min_new_live_labels=3,
        max_champion_age_days=7.0,
    )
    promotion_criteria = Phase8PromotionCriteria(
        min_completed_cycles=2,
        min_live_labels=20,
        min_live_pools=3,
    )
    report = evaluate_phase8_evidence_status(
        storage,
        learning_criteria=learning_criteria,
        promotion_criteria=promotion_criteria,
        as_of="2026-09-24T01:00:00+00:00",
    )

    assert seen["learning"] is learning_criteria
    assert seen["promotion"] is promotion_criteria
    assert seen["as_of"] == "2026-09-24T01:00:00+00:00"
    assert report.required_new_chain_observations == 100
    assert report.required_new_chain_pools == 2
    assert report.retrain_live_label_trigger == 3
    assert report.required_live_labels == 20
    assert report.required_live_pools == 3
