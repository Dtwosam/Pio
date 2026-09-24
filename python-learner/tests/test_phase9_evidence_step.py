from types import SimpleNamespace

import meteora_learner.phase9_evidence_step as step_module
from meteora_learner.phase9_evidence_plan import (
    Phase9EvidenceDebtItem,
    Phase9EvidencePlan,
)
from meteora_learner.phase9_evidence_step import run_phase9_evidence_step
from meteora_learner.settings import Settings
from meteora_learner.storage import Storage


def debt(kind, scope, *, current=0, remaining=1, command="safe"):
    return Phase9EvidenceDebtItem(
        priority=10,
        debt_type=kind,
        scope=scope,
        blocking=True,
        actionable=command is not None,
        current=current,
        required=1,
        remaining=remaining,
        shell_command=command,
        reason="test debt",
    )


def plan(action=None, *, ready=False, history=0):
    return Phase9EvidencePlan(
        research_only=True,
        read_only_commands=True,
        policy_actionable=False,
        execution_wired=False,
        as_of="2026-09-24T00:00:00+00:00",
        research_bundle_ready=ready,
        source_ready=ready,
        history_capture_cycles_remaining=history,
        history_interval_seconds=3600,
        next_action=action,
        items=(() if action is None else (action,)),
        reasons=(),
    )


class DummyRecord:
    def __init__(self, **values):
        self.values = values

    def to_record(self):
        return dict(self.values)


def test_evidence_step_returns_ready_without_running_any_operation(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        step_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: plan(None, ready=True),
    )
    monkeypatch.setattr(
        step_module,
        "collect_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("operation should not run")
        ),
    )

    report = run_phase9_evidence_step(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "READY"
    assert report.debt_type is None
    assert report.progressed is False
    assert report.operation is None
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_evidence_step_stops_on_manual_explicit_inputs(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = debt(
        "EXPLICIT_RESEARCH_INPUTS",
        "USER_ASSUMPTIONS_REQUIRED",
        command="template",
    )
    monkeypatch.setattr(
        step_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: plan(action),
    )
    monkeypatch.setattr(
        step_module,
        "run_phase9_research_refresh",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("manual debt must not auto-run")
        ),
    )

    report = run_phase9_evidence_step(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "MANUAL_REQUIRED"
    assert report.debt_type == "EXPLICIT_RESEARCH_INPUTS"
    assert report.operation is None
    assert report.plan_after is report.plan_before


def test_evidence_step_runs_only_history_capture_for_history_debt(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    before_action = debt(
        "CHAIN_HISTORY_DEPTH",
        "pool-a,pool-b",
        current=None,
        remaining=3,
    )
    after_action = debt(
        "CHAIN_HISTORY_DEPTH",
        "pool-a,pool-b",
        current=None,
        remaining=2,
    )
    plans = iter((
        plan(before_action, history=3),
        plan(after_action, history=2),
    ))
    monkeypatch.setattr(
        step_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    calls = []

    def fake_history(*args, **kwargs):
        calls.append(kwargs)
        result = DummyRecord(
            pools_captured=2,
            pools_skipped_interval=0,
            pools_failed=0,
        )
        result.pools_captured = 2
        result.pools_skipped_interval = 0
        result.pools_failed = 0
        return result

    monkeypatch.setattr(
        step_module,
        "run_phase9_history_capture",
        fake_history,
    )
    monkeypatch.setattr(
        step_module,
        "run_phase9_chain_capture_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("wrong collector")
        ),
    )
    monkeypatch.setattr(
        step_module,
        "run_phase9_mint_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("wrong collector")
        ),
    )

    report = run_phase9_evidence_step(
        storage,
        settings=Settings(database_path=storage.path),
        history_interval_seconds=3600,
    )

    assert report.status == "COMPLETE"
    assert report.debt_type == "CHAIN_HISTORY_DEPTH"
    assert report.progressed is True
    assert report.operation == {
        "pools_captured": 2,
        "pools_skipped_interval": 0,
        "pools_failed": 0,
    }
    assert len(calls) == 1
    assert calls[0]["pool_addresses"] == ("pool-a", "pool-b")
    assert calls[0]["min_observation_interval_seconds"] == 3600


def test_evidence_step_reports_waiting_interval_without_false_progress(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = debt(
        "CHAIN_HISTORY_DEPTH",
        "pool-a,pool-b",
        current=None,
        remaining=3,
    )
    plans = iter((
        plan(action, history=3),
        plan(action, history=3),
    ))
    monkeypatch.setattr(
        step_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    monkeypatch.setattr(
        step_module,
        "run_phase9_history_capture",
        lambda *args, **kwargs: SimpleNamespace(
            pools_captured=0,
            pools_skipped_interval=2,
            pools_failed=0,
            to_record=lambda: {
                "pools_captured": 0,
                "pools_skipped_interval": 2,
                "pools_failed": 0,
            },
        ),
    )

    report = run_phase9_evidence_step(
        storage,
        settings=Settings(database_path=storage.path),
        history_interval_seconds=3600,
    )

    assert report.status == "WAITING_INTERVAL"
    assert report.progressed is False
    assert report.error is None
    assert report.operation["pools_skipped_interval"] == 2
    assert report.plan_after.history_capture_cycles_remaining == 3


def test_evidence_step_runs_research_refresh_directly(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    before_action = debt(
        "RESEARCH_REFRESH",
        "contextual_bandit",
        current=5,
        remaining=1,
    )
    plans = iter((plan(before_action), plan(None, ready=True)))
    monkeypatch.setattr(
        step_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    calls = []
    monkeypatch.setattr(
        step_module,
        "run_phase9_research_refresh",
        lambda *args, **kwargs: (
            calls.append(kwargs) or DummyRecord(bundle_ready_after=True)
        ),
    )

    report = run_phase9_evidence_step(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "COMPLETE"
    assert report.progressed is True
    assert report.plan_after.research_bundle_ready is True
    assert len(calls) == 1


def test_evidence_step_isolates_collector_failure(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = debt("API_POOL_COVERAGE", "METEORA_POOLS")
    plans = iter((plan(action), plan(action)))
    monkeypatch.setattr(
        step_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    monkeypatch.setattr(
        step_module,
        "collect_once",
        lambda settings: (_ for _ in ()).throw(
            RuntimeError("api unavailable")
        ),
    )

    report = run_phase9_evidence_step(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "FAILED"
    assert report.progressed is False
    assert report.operation is None
    assert "api unavailable" in report.error


def test_evidence_step_validates_bounds(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    settings = Settings(database_path=storage.path)

    try:
        run_phase9_evidence_step(
            storage,
            settings=settings,
            chain_max_candidates=0,
        )
    except ValueError as exc:
        assert "chain_max_candidates" in str(exc)
    else:
        raise AssertionError("expected invalid candidate count failure")


def wallet_capture_result(*, deferred=0):
    before = SimpleNamespace(
        events=12,
        unique_users=3,
        ready=False,
    )
    after = SimpleNamespace(
        events=12,
        unique_users=3,
        ready=False,
    )
    scan = SimpleNamespace(backfill_exhausted=True)
    values = {
        "source_before": before,
        "source_after": after,
        "historical_scan_state": scan,
        "discovery_truncated": False,
        "owner_expansion_deferred": 0,
        "candidate_positions_deferred": deferred,
        "owner_expansion_failures": 0,
        "historical_scan_failures": 0,
        "positions_failed": 0,
        "positions_attempted": 2,
        "candidate_positions_selected": 2,
    }
    return SimpleNamespace(
        **values,
        to_record=lambda: {
            "source_before": {
                "events": before.events,
                "unique_users": before.unique_users,
            },
            "source_after": {
                "events": after.events,
                "unique_users": after.unique_users,
            },
            "candidate_positions_deferred": deferred,
        },
    )


def test_evidence_step_waits_for_new_wallet_activity_when_sources_exhausted(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = debt("WALLET_FLOW_SOURCE", "pool-a")
    plans = iter((plan(action), plan(action)))
    monkeypatch.setattr(
        step_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    monkeypatch.setattr(
        step_module,
        "run_phase9_wallet_flow_capture",
        lambda *args, **kwargs: wallet_capture_result(),
    )

    report = run_phase9_evidence_step(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "WAITING_SOURCE_ACTIVITY"
    assert report.progressed is False
    assert report.error is None
    assert report.operation["candidate_positions_deferred"] == 0


def test_evidence_step_does_not_wait_when_wallet_candidates_are_deferred(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    action = debt("WALLET_FLOW_SOURCE", "pool-a")
    plans = iter((plan(action), plan(action)))
    monkeypatch.setattr(
        step_module,
        "build_phase9_evidence_plan",
        lambda *args, **kwargs: next(plans),
    )
    monkeypatch.setattr(
        step_module,
        "run_phase9_wallet_flow_capture",
        lambda *args, **kwargs: wallet_capture_result(deferred=1),
    )

    report = run_phase9_evidence_step(
        storage,
        settings=Settings(database_path=storage.path),
    )

    assert report.status == "COMPLETE"
    assert report.progressed is False
