from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


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


class Systemctl:
    def __init__(self, *, disable_ok=True):
        self.active = True
        self.enabled = True
        self.calls = []
        self.disable_ok = disable_ok

    def __call__(self, command, **kwargs):
        action = command[1]
        self.calls.append(tuple(command[1:]))

        if action == "disable":
            if not self.disable_ok:
                return subprocess.CompletedProcess(
                    command, 1, "", "failed"
                )
            self.active = False
            self.enabled = False
            return subprocess.CompletedProcess(command, 0, "", "")

        if action == "is-active":
            return subprocess.CompletedProcess(
                command,
                0 if self.active else 3,
                "active\n" if self.active else "inactive\n",
                "",
            )
        if action == "is-enabled":
            return subprocess.CompletedProcess(
                command,
                0 if self.enabled else 1,
                "enabled\n" if self.enabled else "disabled\n",
                "",
            )
        raise AssertionError(action)


def set_health(monkeypatch, *, recommended):
    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_timer_health",
        lambda **kwargs: SimpleNamespace(
            pause_recommended=recommended,
            timer_active=True,
            timer_enabled=True,
        ),
    )


def test_pause_dry_run_is_non_mutating(monkeypatch):
    set_health(monkeypatch, recommended=True)
    systemctl = Systemctl()

    report = MODULE.pause_timer(
        apply=False,
        runner=systemctl,
    )

    assert report.pause_recommended is True
    assert report.applied is False
    assert report.future_rpc_cycles_paused is False
    assert systemctl.calls == []


def test_pause_apply_refuses_when_not_recommended(monkeypatch):
    set_health(monkeypatch, recommended=False)
    systemctl = Systemctl()

    report = MODULE.pause_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.failure_step == "PAUSE_NOT_RECOMMENDED"
    assert report.service_control_performed is False
    assert systemctl.calls == []


def test_pause_disables_only_recurring_timer(monkeypatch):
    set_health(monkeypatch, recommended=True)
    systemctl = Systemctl()

    report = MODULE.pause_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is True
    assert report.future_rpc_cycles_paused is True
    assert report.timer_active_after is False
    assert report.timer_enabled_after is False
    assert report.detector_untouched is True
    assert report.streams_untouched is True
    assert report.evidence_service_untouched is True
    assert report.legacy_services_untouched is True
    assert report.direct_rpc_called is False
    assert systemctl.calls == [
        ("disable", "--now", MODULE.TIMER_UNIT),
        ("is-active", MODULE.TIMER_UNIT),
        ("is-enabled", MODULE.TIMER_UNIT),
    ]


def test_pause_reports_disable_failure_without_touching_other_services(
    monkeypatch,
):
    set_health(monkeypatch, recommended=True)
    systemctl = Systemctl(disable_ok=False)

    report = MODULE.pause_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.failure_step == "DISABLE_NOW"
    assert report.detector_untouched is True
    assert report.streams_untouched is True
    assert report.evidence_service_untouched is True
