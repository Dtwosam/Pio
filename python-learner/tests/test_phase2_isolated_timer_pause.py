from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/pause_phase2_isolated_timer.py"
SPEC = importlib.util.spec_from_file_location(
    "pause_phase2_isolated_timer",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def set_health(monkeypatch, *, recommended, active=True, enabled=True):
    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_timer_health",
        lambda **kwargs: SimpleNamespace(
            pause_recommended=recommended,
            timer_active=active,
            timer_enabled=enabled,
        ),
    )


def autopause_report(**overrides):
    values = dict(
        pause_recommended=True,
        applied=True,
        timer_active_before=True,
        timer_enabled_before=True,
        timer_active_after=False,
        timer_enabled_after=False,
        future_timer_cycles_paused=True,
        failure_step=None,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_pause_dry_run_is_non_mutating(monkeypatch):
    set_health(monkeypatch, recommended=True)
    called = False

    def autopause(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("autopause must not run during dry-run")

    monkeypatch.setattr(MODULE.AUTOPAUSE, "autopause", autopause)

    report = MODULE.pause_timer(apply=False)

    assert report.pause_recommended is True
    assert report.applied is False
    assert report.future_rpc_cycles_paused is False
    assert report.service_control_performed is False
    assert called is False


def test_pause_apply_refuses_when_health_does_not_recommend(monkeypatch):
    set_health(monkeypatch, recommended=False)
    called = False

    def autopause(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("autopause must not run without recommendation")

    monkeypatch.setattr(MODULE.AUTOPAUSE, "autopause", autopause)

    report = MODULE.pause_timer(apply=True)

    assert report.applied is False
    assert report.failure_step == "PAUSE_NOT_RECOMMENDED"
    assert report.service_control_performed is False
    assert called is False


def test_pause_apply_delegates_to_stable_autopause(monkeypatch, tmp_path):
    set_health(monkeypatch, recommended=True)
    captured = {}

    def autopause(**kwargs):
        captured.update(kwargs)
        return autopause_report()

    monkeypatch.setattr(MODULE.AUTOPAUSE, "autopause", autopause)

    def forbidden_runner(*args, **kwargs):
        raise AssertionError(
            "manual pause wrapper must not issue systemctl directly"
        )

    report = MODULE.pause_timer(
        data_root=tmp_path,
        history_limit=6,
        max_cycle_age_seconds=1800,
        rate_limit_streak_threshold=3,
        apply=True,
        runner=forbidden_runner,
    )

    assert captured["database_path"] == tmp_path / "pio.db"
    assert captured["history_limit"] == 6
    assert captured["rate_limit_streak_threshold"] == 3
    assert captured["max_cycle_gap_seconds"] == 1800
    assert captured["max_latest_age_seconds"] == 1800
    assert captured["apply"] is True
    assert captured["runner"] is forbidden_runner

    assert report.applied is True
    assert report.future_rpc_cycles_paused is True
    assert report.timer_active_after is False
    assert report.timer_enabled_after is False
    assert report.detector_untouched is True
    assert report.streams_untouched is True
    assert report.evidence_service_untouched is True
    assert report.legacy_services_untouched is True
    assert report.direct_rpc_called is False
    assert report.service_control_performed is True
    assert report.failure_step is None


def test_pause_maps_stable_autopause_revalidation_failure(monkeypatch):
    set_health(monkeypatch, recommended=True)
    monkeypatch.setattr(
        MODULE.AUTOPAUSE,
        "autopause",
        lambda **kwargs: autopause_report(
            applied=False,
            timer_active_after=True,
            timer_enabled_after=True,
            future_timer_cycles_paused=False,
            failure_step="EVIDENCE_CHANGED_BEFORE_PAUSE",
            service_control_performed=False,
        ),
    )

    report = MODULE.pause_timer(apply=True)

    assert report.pause_recommended is True
    assert report.applied is False
    assert report.failure_step == (
        "AUTOPAUSE_EVIDENCE_CHANGED_BEFORE_PAUSE"
    )
    assert report.future_rpc_cycles_paused is False
    assert report.service_control_performed is False


def test_pause_fails_closed_if_autopause_no_longer_recommends(monkeypatch):
    set_health(monkeypatch, recommended=True)
    monkeypatch.setattr(
        MODULE.AUTOPAUSE,
        "autopause",
        lambda **kwargs: autopause_report(
            pause_recommended=False,
            applied=False,
            timer_active_after=True,
            timer_enabled_after=True,
            future_timer_cycles_paused=False,
            service_control_performed=False,
        ),
    )

    report = MODULE.pause_timer(apply=True)

    assert report.applied is False
    assert report.failure_step == "AUTOPAUSE_NO_LONGER_RECOMMENDED"
    assert report.service_control_performed is False


@pytest.mark.parametrize(
    ("flag", "value"),
    (
        ("rpc_called", True),
        ("database_write_performed", True),
    ),
)
def test_pause_rejects_autopause_boundary_crossing(
    monkeypatch,
    flag,
    value,
):
    set_health(monkeypatch, recommended=True)
    unsafe = autopause_report()
    setattr(unsafe, flag, value)
    monkeypatch.setattr(
        MODULE.AUTOPAUSE,
        "autopause",
        lambda **kwargs: unsafe,
    )

    with pytest.raises(ValueError, match="local safety boundary"):
        MODULE.pause_timer(apply=True)


def test_manual_pause_source_contains_no_direct_systemctl_disable():
    source = TOOL.read_text(encoding="utf-8")

    assert "AUTOPAUSE.autopause(" in source
    assert '["systemctl", "disable"' not in source
    assert "subprocess.run(" not in source
