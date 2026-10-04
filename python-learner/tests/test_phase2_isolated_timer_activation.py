from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/activate_phase2_isolated_timer.py"
SPEC = importlib.util.spec_from_file_location(
    "activate_phase2_isolated_timer",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Systemctl:
    def __init__(self, *, fail=None, verify_enabled=True, verify_active=True):
        self.calls = []
        self.enabled = False
        self.active = False
        self.fail = fail
        self.verify_enabled = verify_enabled
        self.verify_active = verify_active

    def __call__(self, command, **kwargs):
        action = command[1]
        label = ":".join(command[1:])
        self.calls.append(label)

        if label == self.fail:
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="failure"
            )

        if action == "daemon-reload":
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if action == "enable":
            self.enabled = True
            self.active = True
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if action == "disable":
            self.enabled = False
            self.active = False
            return subprocess.CompletedProcess(
                command, 0, stdout="", stderr=""
            )
        if action == "is-enabled":
            value = self.enabled and self.verify_enabled
            return subprocess.CompletedProcess(
                command,
                0 if value else 1,
                stdout=("enabled\n" if value else "disabled\n"),
                stderr="",
            )
        if action == "is-active":
            value = self.active and self.verify_active
            return subprocess.CompletedProcess(
                command,
                0 if value else 3,
                stdout=("active\n" if value else "inactive\n"),
                stderr="",
            )
        raise AssertionError(f"unexpected action: {action}")


def install_readiness(monkeypatch, *, ready=True, revalidated=None):
    states = [ready]
    if revalidated is not None:
        states.append(revalidated)

    def capture(**kwargs):
        value = states.pop(0) if len(states) > 1 else states[0]
        return (
            SimpleNamespace(timer_ready=value),
            SimpleNamespace(token="snapshot"),
        )

    monkeypatch.setattr(
        MODULE.READINESS,
        "capture_timer_readiness",
        capture,
    )
    monkeypatch.setattr(
        MODULE.READINESS,
        "assert_timer_readiness_snapshot_stable",
        lambda snapshot: None,
    )
    monkeypatch.setattr(
        MODULE,
        "_readiness_source_identity",
        lambda: ("commit", "sha256"),
    )
    monkeypatch.setattr(
        MODULE,
        "_capture_activation_inputs",
        lambda **kwargs: SimpleNamespace(token="inputs"),
    )
    monkeypatch.setattr(
        MODULE,
        "_assert_activation_inputs_stable",
        lambda inputs: None,
    )


def test_timer_activation_dry_run_is_non_mutating(monkeypatch):
    install_readiness(monkeypatch)
    systemctl = Systemctl()

    report = MODULE.activate_timer(
        apply=False,
        runner=systemctl,
    )

    assert report.timer_ready is True
    assert report.applied is False
    assert report.service_control_performed is False
    assert report.timer_may_trigger_rpc_cycles is False
    assert systemctl.calls == []


def test_timer_activation_requires_receipt_backed_readiness(monkeypatch):
    install_readiness(monkeypatch, ready=False)
    systemctl = Systemctl()

    report = MODULE.activate_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.timer_ready is False
    assert report.failure_step == "PREFLIGHT_NOT_READY"
    assert report.applied is False
    assert systemctl.calls == []


def test_timer_activation_enables_only_evidence_timer(monkeypatch):
    install_readiness(monkeypatch)
    systemctl = Systemctl()

    report = MODULE.activate_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is True
    assert report.timer_enabled is True
    assert report.timer_active is True
    assert report.detector_services_untouched is True
    assert report.legacy_services_untouched is True
    assert report.direct_rpc_called is False
    assert report.timer_may_trigger_rpc_cycles is True
    assert systemctl.calls == [
        f"is-enabled:{MODULE.TIMER_UNIT}",
        f"is-active:{MODULE.TIMER_UNIT}",
        "daemon-reload",
        f"enable:--now:{MODULE.TIMER_UNIT}",
        f"is-enabled:{MODULE.TIMER_UNIT}",
        f"is-active:{MODULE.TIMER_UNIT}",
    ]


def test_timer_activation_rolls_back_failed_active_verification(monkeypatch):
    install_readiness(monkeypatch)
    systemctl = Systemctl(verify_active=False)

    report = MODULE.activate_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.failure_step == "VERIFY_ACTIVE"
    assert report.timer_may_trigger_rpc_cycles is True
    assert report.rollback_performed is True
    assert report.rollback_succeeded is True
    assert report.timer_enabled is False
    assert report.timer_active is False
    assert systemctl.enabled is False
    assert systemctl.active is False
    assert systemctl.calls[-1] == f"disable:--now:{MODULE.TIMER_UNIT}"


def test_timer_activation_rolls_back_failed_enabled_verification(monkeypatch):
    install_readiness(monkeypatch)
    systemctl = Systemctl(verify_enabled=False)

    report = MODULE.activate_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.failure_step == "VERIFY_ENABLED"
    assert report.rollback_performed is True
    assert report.rollback_succeeded is True


def test_timer_activation_stops_before_mutation_if_daemon_reload_fails(
    monkeypatch,
):
    install_readiness(monkeypatch)
    systemctl = Systemctl(fail="daemon-reload")

    report = MODULE.activate_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.failure_step == "DAEMON_RELOAD"
    assert report.service_control_performed is False
    assert report.rollback_performed is False
    assert systemctl.calls == [
        f"is-enabled:{MODULE.TIMER_UNIT}",
        f"is-active:{MODULE.TIMER_UNIT}",
        "daemon-reload",
    ]



def test_timer_activation_revalidates_readiness_before_systemd_mutation(
    monkeypatch,
):
    install_readiness(monkeypatch, ready=True, revalidated=False)
    systemctl = Systemctl()

    report = MODULE.activate_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.failure_step == "PREFLIGHT_CHANGED_BEFORE_APPLY"
    assert report.service_control_performed is False
    assert systemctl.calls == []


def test_timer_activation_rejects_concurrently_enabled_timer(monkeypatch):
    install_readiness(monkeypatch)
    systemctl = Systemctl()
    systemctl.enabled = True

    report = MODULE.activate_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.failure_step == "TIMER_STATE_CHANGED_BEFORE_APPLY"
    assert report.service_control_performed is False
    assert systemctl.calls == [
        f"is-enabled:{MODULE.TIMER_UNIT}",
        f"is-active:{MODULE.TIMER_UNIT}",
    ]


def test_timer_activation_aborts_on_input_drift_before_daemon_reload(
    monkeypatch,
):
    install_readiness(monkeypatch)
    systemctl = Systemctl()
    checks = {"count": 0}

    def guard(_inputs):
        checks["count"] += 1
        if checks["count"] >= 2:
            raise ValueError("installed unit changed after capture")

    monkeypatch.setattr(
        MODULE,
        "_assert_activation_inputs_stable",
        guard,
    )

    import pytest

    with pytest.raises(ValueError, match="installed unit changed"):
        MODULE.activate_timer(
            apply=True,
            runner=systemctl,
        )

    assert systemctl.calls == []


def test_timer_activation_rechecks_readiness_snapshot_before_enable(
    monkeypatch,
):
    install_readiness(monkeypatch)
    systemctl = Systemctl()
    def readiness_guard(_snapshot):
        # Earlier snapshot checks remain valid. Simulate drift only after
        # daemon-reload has consumed the reviewed unit bytes and prove the
        # activation still aborts before enable --now.
        if "daemon-reload" in systemctl.calls:
            raise ValueError("smoke receipt path changed after capture")

    monkeypatch.setattr(
        MODULE.READINESS,
        "assert_timer_readiness_snapshot_stable",
        readiness_guard,
    )

    import pytest

    with pytest.raises(ValueError, match="smoke receipt path changed"):
        MODULE.activate_timer(
            apply=True,
            runner=systemctl,
        )

    assert "daemon-reload" in systemctl.calls
    assert f"enable:--now:{MODULE.TIMER_UNIT}" not in systemctl.calls



def test_timer_activation_rolls_back_on_input_drift_after_enable(monkeypatch):
    install_readiness(monkeypatch)
    systemctl = Systemctl()

    def guard(_inputs):
        if f"enable:--now:{MODULE.TIMER_UNIT}" in systemctl.calls:
            raise ValueError("environment file path changed after capture")

    monkeypatch.setattr(
        MODULE,
        "_assert_activation_inputs_stable",
        guard,
    )

    report = MODULE.activate_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.timer_ready is False
    assert report.failure_step == "POST_ENABLE_INPUT_DRIFT"
    assert report.rollback_performed is True
    assert report.rollback_succeeded is True
    assert report.timer_enabled is False
    assert report.timer_active is False
    assert systemctl.calls[-1] == f"disable:--now:{MODULE.TIMER_UNIT}"
    assert systemctl.calls.count(
        f"is-enabled:{MODULE.TIMER_UNIT}"
    ) == 1
    assert systemctl.calls.count(
        f"is-active:{MODULE.TIMER_UNIT}"
    ) == 1


def test_timer_activation_rolls_back_on_drift_after_state_verification(
    monkeypatch,
):
    install_readiness(monkeypatch)
    systemctl = Systemctl()

    def guard(_inputs):
        if systemctl.calls.count(
            f"is-active:{MODULE.TIMER_UNIT}"
        ) >= 2:
            raise ValueError("runtime current changed after capture")

    monkeypatch.setattr(
        MODULE,
        "_assert_activation_inputs_stable",
        guard,
    )

    report = MODULE.activate_timer(
        apply=True,
        runner=systemctl,
    )

    assert report.applied is False
    assert report.timer_ready is False
    assert report.failure_step == "POST_VERIFY_INPUT_DRIFT"
    assert report.rollback_performed is True
    assert report.rollback_succeeded is True
    assert report.timer_enabled is False
    assert report.timer_active is False
    assert f"enable:--now:{MODULE.TIMER_UNIT}" in systemctl.calls
    assert systemctl.calls.count(
        f"is-enabled:{MODULE.TIMER_UNIT}"
    ) == 2
    assert systemctl.calls.count(
        f"is-active:{MODULE.TIMER_UNIT}"
    ) == 2
    assert systemctl.calls[-1] == f"disable:--now:{MODULE.TIMER_UNIT}"
