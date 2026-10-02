from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/activate_phase2_isolated_evidence_timer.py"
SPEC = importlib.util.spec_from_file_location(
    "activate_phase2_isolated_evidence_timer",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Systemctl:
    def __init__(self, *, verify_active=True):
        self.calls = []
        self.enabled = set()
        self.active = set()
        self.verify_active = verify_active

    def __call__(self, command, **kwargs):
        action = command[1]
        self.calls.append(tuple(command[1:]))

        if action == "enable":
            unit = command[-1]
            self.enabled.add(unit)
            if "--now" in command and self.verify_active:
                self.active.add(unit)
            return subprocess.CompletedProcess(command, 0, "", "")

        unit = command[2]
        if action == "is-enabled":
            value = unit in self.enabled
            return subprocess.CompletedProcess(
                command,
                0 if value else 1,
                "enabled\n" if value else "disabled\n",
                "",
            )
        if action == "is-active":
            value = unit in self.active
            return subprocess.CompletedProcess(
                command,
                0 if value else 3,
                "active\n" if value else "inactive\n",
                "",
            )
        if action == "stop":
            self.active.discard(unit)
            return subprocess.CompletedProcess(command, 0, "", "")
        if action == "disable":
            self.enabled.discard(unit)
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(action)


def set_health(monkeypatch, *, ready):
    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_post_activation",
        lambda **kwargs: SimpleNamespace(
            health_ready_for_evidence_timer=ready
        ),
    )


def test_evidence_timer_dry_run_is_non_mutating(monkeypatch):
    set_health(monkeypatch, ready=True)
    systemctl = Systemctl()

    report = MODULE.activate_evidence_timer(
        apply=False,
        runner=systemctl,
    )

    assert report.health_ready is True
    assert report.applied is False
    assert report.timer_may_trigger_rpc is False
    assert systemctl.calls == []


def test_evidence_timer_activation_requires_health_gate(monkeypatch):
    set_health(monkeypatch, ready=False)
    systemctl = Systemctl()

    report = MODULE.activate_evidence_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.failure_step == "POST_ACTIVATION_HEALTH_NOT_READY"
    assert systemctl.calls == []


def test_evidence_timer_activation_enables_only_timer(monkeypatch):
    set_health(monkeypatch, ready=True)
    systemctl = Systemctl()

    report = MODULE.activate_evidence_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is True
    assert report.timer_enabled is True
    assert report.timer_active is True
    assert report.detector_untouched is True
    assert report.streams_untouched is True
    assert report.legacy_units_untouched is True
    assert report.rpc_called_directly is False
    assert report.timer_may_trigger_rpc is True
    assert systemctl.calls == [
        ("enable", "--now", MODULE.EVIDENCE_TIMER),
        ("is-enabled", MODULE.EVIDENCE_TIMER),
        ("is-active", MODULE.EVIDENCE_TIMER),
    ]


def test_evidence_timer_activation_rolls_back_failed_verification(monkeypatch):
    set_health(monkeypatch, ready=True)
    systemctl = Systemctl(verify_active=False)

    report = MODULE.activate_evidence_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.failure_step == "VERIFY_TIMER"
    assert report.rollback_performed is True
    assert report.rollback_succeeded is True
    assert MODULE.EVIDENCE_TIMER not in systemctl.enabled
    assert MODULE.EVIDENCE_TIMER not in systemctl.active
