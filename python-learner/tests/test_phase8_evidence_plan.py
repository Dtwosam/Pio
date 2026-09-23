from types import SimpleNamespace

import meteora_learner.phase8_evidence_plan as plan_module
from meteora_learner.phase8_evidence_plan import (
    build_phase8_evidence_plan,
)
from meteora_learner.phase8_validation import Phase8PromotionCriteria
from meteora_learner.storage import Storage


def status(**overrides):
    base = dict(
        phase7_promoted=True,
        champion_model_id="champion-1",
        champion_dataset_version="dataset-v1",
        champion_age_days=5.0,
        active_challenger_model_ids=(),
        retrain_status="WAITING_CHAIN_EVIDENCE",
        retrain_due=False,
        new_chain_observations=100,
        required_new_chain_observations=500,
        new_chain_pools=2,
        required_new_chain_pools=3,
        new_live_labels_since_champion=1,
        retrain_live_label_trigger=5,
        champion_age_trigger_days=14.0,
        active_cycle_id=None,
        active_cycle_status=None,
        active_cycle_challenger_model_id=None,
        active_cycle_challenger_status=None,
        completed_cycles=0,
        champion_cycle_id=None,
        continuous_promotion_evidence_id=None,
        live_champion_status="INSUFFICIENT_EVIDENCE",
        live_label_count=4,
        required_live_labels=10,
        live_distinct_pools=1,
        required_live_pools=2,
        live_rollback_recommended=False,
        promotion_ready=False,
        persisted_phase8_current=False,
        reasons=(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_phase8_plan_is_empty_when_persisted_promotion_is_current(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase8_evidence_status",
        lambda *args, **kwargs: status(
            promotion_ready=True,
            persisted_phase8_current=True,
        ),
    )

    plan = build_phase8_evidence_plan(storage)

    assert plan.persisted_phase8_current is True
    assert plan.promotion_ready is True
    assert plan.next_action is None
    assert plan.items == ()
    assert plan.reasons == ()


def test_phase8_plan_stops_at_phase7_dependency(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase8_evidence_status",
        lambda *args, **kwargs: status(
            phase7_promoted=False,
            champion_model_id=None,
            champion_dataset_version=None,
            retrain_status="BLOCKED_PHASE7",
            live_champion_status=None,
            reasons=("Phase 7 is not promoted",),
        ),
    )

    plan = build_phase8_evidence_plan(storage)

    assert plan.next_action is not None
    assert plan.next_action.debt_type == "PHASE7_DEPENDENCY"
    assert (
        plan.next_action.shell_command
        == "pio phase7-validate --require-ready"
    )
    assert any(
        item.debt_type == "INITIAL_CHAMPION_REQUIRED"
        for item in plan.items
    )


def test_phase8_plan_surfaces_retrain_dataset_inputs_when_due(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase8_evidence_status",
        lambda *args, **kwargs: status(
            retrain_status="RETRAIN_DUE",
            retrain_due=True,
            new_chain_observations=600,
            new_chain_pools=4,
            new_live_labels_since_champion=6,
        ),
    )
    monkeypatch.setattr(
        plan_module,
        "audit_phase8_retrain_inputs",
        lambda storage: SimpleNamespace(
            valid=False,
            evidence_id=None,
            reasons=("persisted Phase 8 retrain inputs are missing",),
        ),
    )

    plan = build_phase8_evidence_plan(storage)

    assert plan.next_action is not None
    assert plan.next_action.debt_type == (
        "RETRAIN_DATASET_INPUTS_REQUIRED"
    )
    assert (
        plan.next_action.shell_command
        == "pio phase8-retrain-input-template "
        "> phase8-retrain-inputs.json"
    )
    assert "must not be invented" in plan.next_action.reason
    assert "retrain inputs are missing" in plan.next_action.reason


def test_phase8_plan_unlocks_dataset_build_from_valid_inputs(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase8_evidence_status",
        lambda *args, **kwargs: status(
            retrain_status="RETRAIN_DUE",
            retrain_due=True,
            new_chain_observations=600,
            new_chain_pools=4,
            new_live_labels_since_champion=6,
        ),
    )
    monkeypatch.setattr(
        plan_module,
        "audit_phase8_retrain_inputs",
        lambda storage: SimpleNamespace(
            valid=True,
            evidence_id=88,
            reasons=(),
        ),
    )

    plan = build_phase8_evidence_plan(storage)

    assert plan.next_action is not None
    assert plan.next_action.debt_type == "RETRAIN_DATASET_BUILD_READY"
    assert plan.next_action.scope == "88"
    assert plan.next_action.operator_required is False
    assert plan.next_action.shell_command == (
        "pio phase8-retrain-build-run --input-evidence-id 88"
    )
    assert "model training" in plan.next_action.reason


def test_phase8_plan_maps_offline_qualified_cycle_to_paper_start(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase8_evidence_status",
        lambda *args, **kwargs: status(
            retrain_status="CHALLENGER_IN_PROGRESS",
            active_challenger_model_ids=("challenger-1",),
            active_cycle_id="cycle-1",
            active_cycle_status="OFFLINE_QUALIFIED",
            active_cycle_challenger_model_id="challenger-1",
            active_cycle_challenger_status="OFFLINE_QUALIFIED",
        ),
    )

    plan = build_phase8_evidence_plan(storage)

    assert plan.next_action is not None
    assert plan.next_action.debt_type == (
        "PAPER_CHALLENGER_START_REQUIRED"
    )
    assert plan.next_action.scope == "challenger-1"
    assert plan.next_action.shell_command == (
        "pio ml-start-paper --model-id challenger-1"
    )


def test_phase8_plan_keeps_paper_validation_operator_bound(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase8_evidence_status",
        lambda *args, **kwargs: status(
            retrain_status="CHALLENGER_IN_PROGRESS",
            active_challenger_model_ids=("challenger-1",),
            active_cycle_id="cycle-1",
            active_cycle_status="PAPER_CHALLENGER",
            active_cycle_challenger_model_id="challenger-1",
            active_cycle_challenger_status="PAPER_CHALLENGER",
        ),
    )

    plan = build_phase8_evidence_plan(storage)

    assert plan.next_action is not None
    assert plan.next_action.debt_type == (
        "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
    )
    assert plan.next_action.operator_required is True
    assert plan.next_action.shell_command is None
    assert "explicit PAPER account" in plan.next_action.reason


def test_phase8_plan_reports_chain_and_live_evidence_deficits(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase8_evidence_status",
        lambda *args, **kwargs: status(),
    )

    plan = build_phase8_evidence_plan(storage)

    assert plan.next_action is not None
    assert plan.next_action.debt_type == "WAITING_CHAIN_EVIDENCE"
    assert plan.next_action.operator_required is False
    assert "400 observations" in plan.next_action.reason
    assert "1 distinct pools" in plan.next_action.reason

    debt_types = {item.debt_type for item in plan.items}
    assert "LIVE_LABEL_DEPTH_REQUIRED" in debt_types
    assert "LIVE_POOL_DIVERSITY_REQUIRED" in debt_types
    assert "COMPLETED_CYCLE_EVIDENCE_REQUIRED" in debt_types
    assert "CHAMPION_CYCLE_LINEAGE_REQUIRED" in debt_types
    assert "CONTINUOUS_PROMOTION_EVIDENCE_REQUIRED" in debt_types


def test_phase8_plan_prioritizes_live_safety_breach(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase8_evidence_status",
        lambda *args, **kwargs: status(
            live_champion_status="BREACH",
            live_rollback_recommended=True,
        ),
    )

    plan = build_phase8_evidence_plan(storage)

    assert plan.next_action is not None
    assert plan.next_action.debt_type == "LIVE_CHAMPION_SAFETY_BREACH"
    assert "--persist" in plan.next_action.shell_command
    assert "--rollback" not in plan.next_action.shell_command


def test_phase8_plan_surfaces_promotion_persistence_only_after_gate_passes(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase8_evidence_status",
        lambda *args, **kwargs: status(
            completed_cycles=1,
            champion_cycle_id="cycle-1",
            continuous_promotion_evidence_id=91,
            live_champion_status="HEALTHY",
            live_label_count=12,
            live_distinct_pools=3,
            promotion_ready=True,
            persisted_phase8_current=False,
        ),
    )

    plan = build_phase8_evidence_plan(
        storage,
        promotion_criteria=Phase8PromotionCriteria(
            min_completed_cycles=1,
            min_live_labels=10,
            min_live_pools=2,
        ),
    )

    assert plan.next_action is not None
    assert plan.next_action.debt_type == (
        "PHASE8_PROMOTION_PERSISTENCE_REQUIRED"
    )
    assert plan.next_action.shell_command == (
        "pio phase8-validate --persist-ready --require-ready"
    )
    assert plan.next_action.operator_required is True
