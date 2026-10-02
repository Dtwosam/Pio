from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/activate_phase2_isolated_detector.py"
SPEC = importlib.util.spec_from_file_location(
    "activate_phase2_isolated_detector",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Systemctl:
    def __init__(self, *, fail_step=None):
        self.active = set()
        self.enabled = set()
        self.calls = []
        self.fail_step = fail_step

    def __call__(self, command, **kwargs):
        action = command[1]
        unit = command[2] if len(command) > 2 else None
        label = action if unit is None else f"{action}:{unit}"
        self.calls.append(label)

        if label == self.fail_step:
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="failure"
            )

        if action == "daemon-reload":
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if action == "start":
            self.active.add(unit)
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if action == "stop":
            self.active.discard(unit)
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if action == "enable":
            self.enabled.add(unit)
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if action == "disable":
            self.enabled.discard(unit)
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if action == "is-active":
            active = unit in self.active
            return subprocess.CompletedProcess(
                command,
                0 if active else 3,
                stdout=("active\n" if active else "inactive\n"),
                stderr="",
            )
        if action == "is-enabled":
            enabled = unit in self.enabled
            return subprocess.CompletedProcess(
                command,
                0 if enabled else 1,
                stdout=("enabled\n" if enabled else "disabled\n"),
                stderr="",
            )
        raise AssertionError(f"unexpected systemctl action: {action}")


def set_preflight(monkeypatch, *, ready=True):
    monkeypatch.setattr(
        MODULE.PREFLIGHT,
        "inspect_activation",
        lambda **kwargs: SimpleNamespace(
            activation_ready=ready,
        ),
    )


def test_detector_activation_dry_run_is_non_mutating(monkeypatch):
    set_preflight(monkeypatch, ready=True)
    systemctl = Systemctl()

    report = MODULE.activate_detector(
        apply=False,
        runner=systemctl,
    )

    assert report.preflight_ready is True
    assert report.applied is False
    assert report.service_control_performed is False
    assert report.evidence_timer_untouched is True
    assert systemctl.calls == []


def test_detector_activation_requires_fresh_preflight(monkeypatch):
    set_preflight(monkeypatch, ready=False)
    systemctl = Systemctl()

    report = MODULE.activate_detector(
        apply=True,
        runner=systemctl,
    )

    assert report.preflight_ready is False
    assert report.applied is False
    assert report.failure_step == "PREFLIGHT_NOT_READY"
    assert report.service_control_performed is False
    assert systemctl.calls == []


def test_detector_activation_starts_streams_then_detector(monkeypatch):
    set_preflight(monkeypatch, ready=True)
    systemctl = Systemctl()

    report = MODULE.activate_detector(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is True
    assert report.daemon_reload_performed is True
    assert report.detector_enabled is True
    assert set(report.activated_units) == {
        *MODULE.PRESTATE_STREAM_UNITS,
        MODULE.DETECTOR_UNIT,
    }
    assert MODULE.DETECTOR_UNIT in systemctl.active
    assert MODULE.DETECTOR_UNIT in systemctl.enabled
    assert all(
        unit in systemctl.active
        for unit in MODULE.PRESTATE_STREAM_UNITS
    )
    assert report.evidence_timer_untouched is True
    assert report.legacy_services_untouched is True
    assert report.rpc_called_directly is False
    assert report.activated_services_may_call_rpc is True

    expected_prefix = ["daemon-reload"]
    for unit in MODULE.PRESTATE_STREAM_UNITS:
        expected_prefix.extend(
            [f"start:{unit}", f"is-active:{unit}"]
        )
    expected_prefix.extend(
        [
            f"enable:{MODULE.DETECTOR_UNIT}",
            f"start:{MODULE.DETECTOR_UNIT}",
            f"is-active:{MODULE.DETECTOR_UNIT}",
        ]
    )
    assert systemctl.calls == expected_prefix


def test_detector_activation_rolls_back_after_detector_start_failure(
    monkeypatch,
):
    set_preflight(monkeypatch, ready=True)
    systemctl = Systemctl(
        fail_step=f"start:{MODULE.DETECTOR_UNIT}",
    )

    report = MODULE.activate_detector(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.activated_services_may_call_rpc is True
    assert report.failure_step == f"START:{MODULE.DETECTOR_UNIT}"
    assert report.rollback_performed is True
    assert report.rollback_succeeded is True
    assert MODULE.DETECTOR_UNIT not in systemctl.enabled
    assert MODULE.DETECTOR_UNIT not in systemctl.active
    assert all(
        unit not in systemctl.active
        for unit in MODULE.PRESTATE_STREAM_UNITS
    )
    assert report.evidence_timer_untouched is True


def test_detector_activation_rolls_back_if_second_stream_fails(monkeypatch):
    set_preflight(monkeypatch, ready=True)
    second = MODULE.PRESTATE_STREAM_UNITS[1]
    systemctl = Systemctl(fail_step=f"start:{second}")

    report = MODULE.activate_detector(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.activated_services_may_call_rpc is True
    assert report.failure_step == f"START:{second}"
    assert report.rollback_performed is True
    assert report.rollback_succeeded is True
    assert all(
        unit not in systemctl.active
        for unit in MODULE.PRESTATE_STREAM_UNITS
    )
    assert MODULE.DETECTOR_UNIT not in systemctl.enabled


def test_detector_activation_reports_rollback_failure(monkeypatch):
    set_preflight(monkeypatch, ready=True)
    first = MODULE.PRESTATE_STREAM_UNITS[0]
    systemctl = Systemctl(
        fail_step=f"start:{MODULE.PRESTATE_STREAM_UNITS[1]}",
    )

    original = systemctl.__call__

    def runner(command, **kwargs):
        if command[1] == "stop" and command[2] == first:
            systemctl.calls.append(f"stop:{first}")
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="failure"
            )
        return original(command, **kwargs)

    report = MODULE.activate_detector(
        apply=True,
        runner=runner,
    )

    assert report.applied is False
    assert report.rollback_performed is True
    assert report.rollback_succeeded is False
