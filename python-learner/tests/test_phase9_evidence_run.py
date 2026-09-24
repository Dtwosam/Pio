from types import SimpleNamespace

import meteora_learner.phase9_evidence_run as run_module
from meteora_learner.phase9_evidence_plan import (
    Phase9EvidenceDebtItem,
    Phase9EvidencePlan,
)
from meteora_learner.phase9_evidence_run import (
    run_phase9_evidence_until_blocked,
)
from meteora_learner.phase9_evidence_step import (
    Phase9EvidenceStepReport,
)
from meteora_learner.settings import Settings
from meteora_learner.storage import Storage


def debt(kind, scope):
    return Phase9EvidenceDebtItem(
        priority=10,
        debt_type=kind,
        scope=scope,
        blocking=True,
        actionable=True,
        current=0,
        required=1,
        remaining=1,
        shell_command="safe",
        reason="test debt",
    )


def plan(action=None, *, ready=False):
    return Phase9EvidencePlan(
        research_only=True,
        read_only_commands=True,
        policy_actionable=False,
        execution_wired=False,
        as_of="2026-09-24T00:00:00+00:00",
        research_bundle_ready=ready,
        source_ready=ready,
        history_capture_cycles_remaining=0,
        history_interval_seconds=3600,
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
    error=None,
):
    p = plan(action, ready=ready)
    return Phase9EvidenceStepReport(
        research_only=True,
        read_only_external=True,
        policy_actionable=False,
        execution_wired=False,
        status=status,
        debt_type=(action.debt_type if action else None),
        scope=(action.scope if action else None),
        progressed=progressed,
        operation=None,
        error=error,
        plan_before=p,
        plan_after=p,
    )


def test_evidence_run_continues_progress_until_ready(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    first = debt("API_POOL_COVERAGE", "METEORA_POOLS")
    second = debt("MINT_INPUTS", "pool-a")
    reports = iter((
        step("COMPLETE", progressed=True, action=first),
        step("COMPLETE", progressed=True, action=second),
        step("READY", ready=True),
    ))
    monkeypatch.setattr(
        run_module,
        "run_phase9_evidence_step",
        lambda *args, **kwargs: next(reports),
    )

    report = run_phase9_evidence_until_blocked(
        storage,
        settings=Settings(database_path=storage.path),
        max_steps=5,
    )

    assert report.status == "READY"
    assert report.steps_attempted == 3
    assert report.steps_progressed == 2
    assert report.research_bundle_ready is True
    assert report.terminal_debt_type is None


def test_evidence_run_stops_on_manual_required(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    action = debt(
        "EXPLICIT_RESEARCH_INPUTS",
        "USER_ASSUMPTIONS_REQUIRED",
    )
    monkeypatch.setattr(
        run_module,
        "run_phase9_evidence_step",
        lambda *args, **kwargs: step(
            "MANUAL_REQUIRED",
            action=action,
        ),
    )

    report = run_phase9_evidence_until_blocked(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "MANUAL_REQUIRED"
    assert report.steps_attempted == 1
    assert report.steps_progressed == 0
    assert report.terminal_debt_type == "EXPLICIT_RESEARCH_INPUTS"


def test_evidence_run_stops_on_interval_wait(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    action = debt("CHAIN_HISTORY_DEPTH", "pool-a,pool-b")
    monkeypatch.setattr(
        run_module,
        "run_phase9_evidence_step",
        lambda *args, **kwargs: step(
            "WAITING_INTERVAL",
            action=action,
        ),
    )

    report = run_phase9_evidence_until_blocked(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "WAITING_INTERVAL"
    assert report.steps_attempted == 1
    assert report.steps_progressed == 0


def test_evidence_run_stops_on_failure(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    action = debt("API_POOL_COVERAGE", "METEORA_POOLS")
    monkeypatch.setattr(
        run_module,
        "run_phase9_evidence_step",
        lambda *args, **kwargs: step(
            "FAILED",
            action=action,
            error="RuntimeError: api unavailable",
        ),
    )

    report = run_phase9_evidence_until_blocked(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "FAILED"
    assert "api unavailable" in report.reasons[0]


def test_evidence_run_stops_on_complete_without_progress(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = debt("RESEARCH_REFRESH", "adaptive_regime")
    monkeypatch.setattr(
        run_module,
        "run_phase9_evidence_step",
        lambda *args, **kwargs: step(
            "COMPLETE",
            progressed=False,
            action=action,
        ),
    )

    report = run_phase9_evidence_until_blocked(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "NO_PROGRESS"
    assert report.steps_attempted == 1
    assert any(
        "without reducing" in reason
        for reason in report.reasons
    )


def test_evidence_run_respects_max_steps(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    action = debt("API_POOL_COVERAGE", "METEORA_POOLS")
    calls = []

    def fake_step(*args, **kwargs):
        calls.append(1)
        return step(
            "COMPLETE",
            progressed=True,
            action=action,
        )

    monkeypatch.setattr(
        run_module,
        "run_phase9_evidence_step",
        fake_step,
    )

    report = run_phase9_evidence_until_blocked(
        storage,
        settings=Settings(database_path=storage.path),
        max_steps=3,
    )

    assert report.status == "MAX_STEPS"
    assert report.steps_attempted == 3
    assert report.steps_progressed == 3
    assert len(calls) == 3


def test_evidence_run_validates_max_steps(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    try:
        run_phase9_evidence_until_blocked(
            storage,
            settings=Settings(database_path=storage.path),
            max_steps=0,
        )
    except ValueError as exc:
        assert "max_steps" in str(exc)
    else:
        raise AssertionError("expected invalid max_steps failure")
