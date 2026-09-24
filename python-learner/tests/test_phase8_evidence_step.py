import json
import sys

from meteora_learner import cli
from types import SimpleNamespace

import meteora_learner.phase8_evidence_step as step_module
from meteora_learner.phase8_evidence_plan import (
    Phase8EvidenceDebtItem,
    Phase8EvidencePlan,
)
from meteora_learner.phase8_evidence_step import run_phase8_evidence_step
from meteora_learner.storage import Storage


def item(
    debt_type,
    scope,
    *,
    operator_required=False,
    shell_command="safe",
):
    return Phase8EvidenceDebtItem(
        priority=10,
        debt_type=debt_type,
        scope=scope,
        blocking=True,
        operator_required=operator_required,
        shell_command=shell_command,
        reason="test",
    )


def plan(action=None, *, current=False):
    return Phase8EvidencePlan(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of="2026-09-24T00:00:00+00:00",
        promotion_ready=current,
        persisted_phase8_current=current,
        next_action=action,
        items=(() if action is None else (action,)),
        reasons=(),
    )


def test_phase8_step_returns_ready_without_operation(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        step_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: plan(None, current=True),
    )

    report = run_phase8_evidence_step(storage)

    assert report.status == "READY"
    assert report.debt_type is None
    assert report.operation is None
    assert report.progressed is False
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_phase8_step_stops_at_manual_paper_boundary(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = item(
        "PAPER_CHALLENGER_START_REQUIRED",
        "challenger-1",
        operator_required=True,
        shell_command="pio ml-start-paper --model-id challenger-1",
    )
    monkeypatch.setattr(
        step_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: plan(action),
    )
    monkeypatch.setattr(
        step_module,
        "train_phase8_cycle_challenger",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("manual boundary must not execute")
        ),
    )

    report = run_phase8_evidence_step(storage)

    assert report.status == "MANUAL_REQUIRED"
    assert report.debt_type == "PAPER_CHALLENGER_START_REQUIRED"
    assert report.scope == "challenger-1"
    assert report.operation is None


def test_phase8_step_waits_for_real_chain_evidence(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    action = item(
        "WAITING_CHAIN_EVIDENCE",
        "champion-1",
        operator_required=False,
        shell_command=None,
    )
    monkeypatch.setattr(
        step_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: plan(action),
    )

    report = run_phase8_evidence_step(storage)

    assert report.status == "WAITING"
    assert report.debt_type == "WAITING_CHAIN_EVIDENCE"
    assert report.progressed is False


def test_phase8_step_builds_cycle_from_valid_input_artifact(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    before = item("RETRAIN_DATASET_BUILD_READY", "88")
    after = item("RETRAIN_OFFLINE_TRAIN_READY", "cycle-1")
    plans = iter((plan(before), plan(after)))
    monkeypatch.setattr(
        step_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    artifact = SimpleNamespace(evidence_id=88)
    monkeypatch.setattr(
        step_module,
        "load_phase8_retrain_inputs",
        lambda *args, **kwargs: artifact,
    )
    calls = []
    monkeypatch.setattr(
        step_module,
        "run_phase8_retrain_build_from_inputs",
        lambda *args, **kwargs: (
            calls.append(kwargs)
            or SimpleNamespace(
                to_record=lambda: {"cycle_id": "cycle-1"}
            )
        ),
    )

    report = run_phase8_evidence_step(storage)

    assert report.status == "COMPLETE"
    assert report.progressed is True
    assert report.operation == {"cycle_id": "cycle-1"}
    assert calls[0]["artifact"] is artifact


def test_phase8_step_trains_only_offline_challenger(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    before = item("RETRAIN_OFFLINE_TRAIN_READY", "cycle-1")
    after = item(
        "RETRAIN_OFFLINE_VALIDATION_READY",
        "cycle-1",
    )
    plans = iter((plan(before), plan(after)))
    monkeypatch.setattr(
        step_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    calls = []
    monkeypatch.setattr(
        step_module,
        "train_phase8_cycle_challenger",
        lambda *args, **kwargs: (
            calls.append(kwargs)
            or SimpleNamespace(
                to_record=lambda: {"model_id": "challenger-1"}
            )
        ),
    )

    report = run_phase8_evidence_step(storage)

    assert report.status == "COMPLETE"
    assert report.progressed is True
    assert calls == [{"cycle_id": "cycle-1"}]
    assert report.operation == {"model_id": "challenger-1"}


def test_phase8_step_offline_validation_stops_before_paper(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    before = item(
        "RETRAIN_OFFLINE_VALIDATION_READY",
        "cycle-1",
    )
    after = item(
        "PAPER_CHALLENGER_START_REQUIRED",
        "challenger-1",
        operator_required=True,
        shell_command="pio ml-start-paper --model-id challenger-1",
    )
    plans = iter((plan(before), plan(after)))
    monkeypatch.setattr(
        step_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    monkeypatch.setattr(
        step_module,
        "validate_phase8_cycle_challenger_offline",
        lambda *args, **kwargs: SimpleNamespace(
            offline_qualified=True,
            to_record=lambda: {
                "offline_qualified": True,
                "model_status_after": "OFFLINE_QUALIFIED",
            },
        ),
    )

    report = run_phase8_evidence_step(storage)

    assert report.status == "COMPLETE"
    assert report.progressed is True
    assert report.operation["offline_qualified"] is True
    assert report.plan_after.next_action.debt_type == (
        "PAPER_CHALLENGER_START_REQUIRED"
    )


def test_phase8_step_reports_not_qualified_without_promotion(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = item(
        "RETRAIN_OFFLINE_VALIDATION_READY",
        "cycle-1",
    )
    plans = iter((plan(action), plan(action)))
    monkeypatch.setattr(
        step_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    monkeypatch.setattr(
        step_module,
        "validate_phase8_cycle_challenger_offline",
        lambda *args, **kwargs: SimpleNamespace(
            offline_qualified=False,
            to_record=lambda: {"offline_qualified": False},
        ),
    )

    report = run_phase8_evidence_step(storage)

    assert report.status == "NOT_QUALIFIED"
    assert report.progressed is False
    assert report.operation == {"offline_qualified": False}


def test_phase8_step_isolates_operation_failure(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    action = item("RETRAIN_OFFLINE_TRAIN_READY", "cycle-1")
    plans = iter((plan(action), plan(action)))
    monkeypatch.setattr(
        step_module,
        "build_phase8_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    monkeypatch.setattr(
        step_module,
        "train_phase8_cycle_challenger",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("training failed")
        ),
    )

    report = run_phase8_evidence_step(storage)

    assert report.status == "FAILED"
    assert report.progressed is False
    assert "training failed" in report.error


def test_phase8_evidence_step_cli_points_to_operator_handoff(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    report = SimpleNamespace(
        status="MANUAL_REQUIRED",
        to_record=lambda: {
            "status": "MANUAL_REQUIRED",
            "debt_type": "PAPER_CHALLENGER_START_REQUIRED",
            "scope": "challenger-1",
        },
    )
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        cli,
        "run_phase8_evidence_step",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["pio", "phase8-evidence-step-run"],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "MANUAL_REQUIRED"
    assert payload["operator_handoff_command"] == (
        "pio phase8-operator-handoff"
    )
