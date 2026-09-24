import json
import sys
from pathlib import Path

import pytest

from meteora_learner import cli
from types import SimpleNamespace

import meteora_learner.phase8_evidence_run as run_module
from meteora_learner.phase8_evidence_plan import (
    Phase8EvidenceDebtItem,
    Phase8EvidencePlan,
)
from meteora_learner.phase8_evidence_run import (
    run_phase8_evidence_until_blocked,
)
from meteora_learner.phase8_evidence_step import (
    Phase8EvidenceStepReport,
)
from meteora_learner.storage import Storage


def item(kind, scope="scope", *, operator_required=False):
    return Phase8EvidenceDebtItem(
        priority=10,
        debt_type=kind,
        scope=scope,
        blocking=True,
        operator_required=operator_required,
        shell_command="safe",
        reason="test debt",
    )


def plan(action=None, *, ready=False, current=False):
    return Phase8EvidencePlan(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        as_of="2026-09-24T00:00:00+00:00",
        promotion_ready=ready,
        persisted_phase8_current=current,
        next_action=action,
        items=(() if action is None else (action,)),
        reasons=(),
    )


def step(
    status,
    *,
    progressed=False,
    action=None,
    ready=False,
    current=False,
    error=None,
):
    before = plan(action)
    after = plan(
        None if ready or current else action,
        ready=ready,
        current=current,
    )
    return Phase8EvidenceStepReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        status=status,
        debt_type=(action.debt_type if action else None),
        scope=(action.scope if action else None),
        progressed=progressed,
        operation=None,
        error=error,
        plan_before=before,
        plan_after=after,
    )


def test_phase8_run_chains_offline_steps_until_manual_boundary(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    build = item("RETRAIN_DATASET_BUILD_READY", "88")
    train = item("RETRAIN_OFFLINE_TRAIN_READY", "cycle-1")
    validate = item(
        "RETRAIN_OFFLINE_VALIDATION_READY",
        "cycle-1",
    )
    paper = item(
        "PAPER_CHALLENGER_START_REQUIRED",
        "challenger-1",
        operator_required=True,
    )
    reports = iter((
        step("COMPLETE", progressed=True, action=build),
        step("COMPLETE", progressed=True, action=train),
        step("COMPLETE", progressed=True, action=validate),
        step("MANUAL_REQUIRED", action=paper),
    ))
    monkeypatch.setattr(
        run_module,
        "run_phase8_evidence_step",
        lambda *args, **kwargs: next(reports),
    )

    report = run_phase8_evidence_until_blocked(
        storage,
        max_steps=5,
    )

    assert report.status == "MANUAL_REQUIRED"
    assert report.steps_attempted == 4
    assert report.steps_progressed == 3
    assert report.terminal_debt_type == (
        "PAPER_CHALLENGER_START_REQUIRED"
    )
    assert report.terminal_scope == "challenger-1"
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.offline_only is True


def test_phase8_run_stops_when_current(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    reports = iter((
        step(
            "READY",
            ready=True,
            current=True,
        ),
    ))
    monkeypatch.setattr(
        run_module,
        "run_phase8_evidence_step",
        lambda *args, **kwargs: next(reports),
    )

    report = run_phase8_evidence_until_blocked(storage)

    assert report.status == "READY"
    assert report.steps_attempted == 1
    assert report.persisted_phase8_current is True
    assert report.promotion_ready is True


def test_phase8_run_stops_waiting_for_real_evidence(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    waiting = item("WAITING_CHAIN_EVIDENCE", "champion-1")
    monkeypatch.setattr(
        run_module,
        "run_phase8_evidence_step",
        lambda *args, **kwargs: step(
            "WAITING",
            action=waiting,
        ),
    )

    report = run_phase8_evidence_until_blocked(storage)

    assert report.status == "WAITING"
    assert report.steps_attempted == 1
    assert report.steps_progressed == 0


def test_phase8_run_stops_when_offline_validation_not_qualified(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    validation = item(
        "RETRAIN_OFFLINE_VALIDATION_READY",
        "cycle-1",
    )
    monkeypatch.setattr(
        run_module,
        "run_phase8_evidence_step",
        lambda *args, **kwargs: step(
            "NOT_QUALIFIED",
            action=validation,
        ),
    )

    report = run_phase8_evidence_until_blocked(storage)

    assert report.status == "NOT_QUALIFIED"
    assert report.terminal_debt_type == (
        "RETRAIN_OFFLINE_VALIDATION_READY"
    )


def test_phase8_run_stops_on_failure(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    train = item("RETRAIN_OFFLINE_TRAIN_READY", "cycle-1")
    monkeypatch.setattr(
        run_module,
        "run_phase8_evidence_step",
        lambda *args, **kwargs: step(
            "FAILED",
            action=train,
            error="RuntimeError: training failed",
        ),
    )

    report = run_phase8_evidence_until_blocked(storage)

    assert report.status == "FAILED"
    assert "training failed" in report.reasons[0]


def test_phase8_run_stops_on_complete_without_progress(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    train = item("RETRAIN_OFFLINE_TRAIN_READY", "cycle-1")
    monkeypatch.setattr(
        run_module,
        "run_phase8_evidence_step",
        lambda *args, **kwargs: step(
            "COMPLETE",
            action=train,
            progressed=False,
        ),
    )

    report = run_phase8_evidence_until_blocked(storage)

    assert report.status == "NO_PROGRESS"
    assert any(
        "without reducing" in reason
        for reason in report.reasons
    )


def test_phase8_run_respects_max_steps(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    action = item("RETRAIN_OFFLINE_TRAIN_READY", "cycle-1")
    calls = []

    def fake_step(*args, **kwargs):
        calls.append(1)
        before = plan(action)
        after = plan(
            item(
                "RETRAIN_OFFLINE_TRAIN_READY",
                f"cycle-{len(calls) + 1}",
            )
        )
        return Phase8EvidenceStepReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="COMPLETE",
            debt_type=action.debt_type,
            scope=action.scope,
            progressed=True,
            operation={"call": len(calls)},
            error=None,
            plan_before=before,
            plan_after=after,
        )

    monkeypatch.setattr(
        run_module,
        "run_phase8_evidence_step",
        fake_step,
    )

    report = run_phase8_evidence_until_blocked(
        storage,
        max_steps=3,
    )

    assert report.status == "MAX_STEPS"
    assert report.steps_attempted == 3
    assert report.steps_progressed == 3
    assert len(calls) == 3


def test_phase8_run_validates_max_steps(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    try:
        run_phase8_evidence_until_blocked(storage, max_steps=0)
    except ValueError as exc:
        assert "max_steps" in str(exc)
    else:
        raise AssertionError("expected invalid max_steps failure")


def test_phase8_runner_sources_exclude_manual_state_transitions():
    root = Path(__file__).resolve().parents[1] / "src" / "meteora_learner"
    source = (
        (root / "phase8_evidence_step.py").read_text(encoding="utf-8")
        + (root / "phase8_evidence_run.py").read_text(encoding="utf-8")
    )

    forbidden = (
        "ml-start-paper",
        "promote_continuous_challenger",
        "rollback_live_champion",
        "persist_phase8_promotion",
        "phase8-validate --persist-ready",
    )
    for value in forbidden:
        assert value not in source


def test_phase8_evidence_run_cli_prints_bounded_report(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    report = SimpleNamespace(
        persisted_phase8_current=False,
        to_record=lambda: {
            "status": "MANUAL_REQUIRED",
            "offline_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "steps_attempted": 1,
            "terminal_debt_type": "PAPER_CHALLENGER_START_REQUIRED",
        },
    )
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        cli,
        "run_phase8_evidence_until_blocked",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["pio", "phase8-evidence-run", "--max-steps", "4"],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "MANUAL_REQUIRED"
    assert payload["offline_only"] is True
    assert payload["terminal_debt_type"] == (
        "PAPER_CHALLENGER_START_REQUIRED"
    )


def test_phase8_evidence_run_cli_require_current_fails_closed(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    report = SimpleNamespace(
        persisted_phase8_current=False,
        to_record=lambda: {
            "status": "WAITING",
            "persisted_phase8_current": False,
        },
    )
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        cli,
        "run_phase8_evidence_until_blocked",
        lambda *args, **kwargs: report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pio",
            "phase8-evidence-run",
            "--require-current",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "WAITING"
