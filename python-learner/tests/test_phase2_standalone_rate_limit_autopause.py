from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/autopause_phase2_isolated_timer.py"
INSTALLER = ROOT / "deploy/tools/install_phase2_isolated_systemd_units.py"
EVIDENCE_SERVICE = (
    ROOT / "deploy/systemd/pio-phase2-isolated-evidence-cycle.service"
)
PAUSE_SERVICE = (
    ROOT / "deploy/systemd/pio-phase2-isolated-rate-limit-pause.service"
)

SPEC = importlib.util.spec_from_file_location(
    "autopause_phase2_isolated_timer",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

INSTALL_SPEC = importlib.util.spec_from_file_location(
    "phase2_installer_for_autopause_test",
    INSTALLER,
)
assert INSTALL_SPEC is not None and INSTALL_SPEC.loader is not None
INSTALL = importlib.util.module_from_spec(INSTALL_SPEC)
sys.modules[INSTALL_SPEC.name] = INSTALL
INSTALL_SPEC.loader.exec_module(INSTALL)


def cycle(
    evidence_id,
    *,
    at,
    limited=True,
    status="COLLECTION_FAILED",
):
    return SimpleNamespace(
        evidence_id=evidence_id,
        as_of=at,
        status=status,
        rpc_rate_limited=limited,
        rpc_circuit_open=limited,
        stages_failed=1 if status == "COLLECTION_FAILED" else 0,
        stages_skipped=2 if limited else 0,
    )


def health(
    *,
    pool="configured-pool",
    limited=2,
    pause=True,
    timer_enabled=True,
    timer_active=True,
    latest_recent=True,
    evidence_start=10,
):
    cycles = tuple(
        cycle(
            evidence_start - index,
            at=f"2026-10-02T20:{30 - index * 15:02d}:00+00:00",
        )
        for index in range(limited)
    )
    if limited == 0:
        cycles = (
            cycle(
                evidence_start,
                at="2026-10-02T20:30:00+00:00",
                limited=False,
                status="COLLECTION_SUCCESS",
            ),
        )
    return SimpleNamespace(
        data_ready=True,
        pool_address=pool,
        cycles=cycles,
        latest_cycle_age_seconds=300.0,
        latest_cycle_recent=latest_recent,
        latest_cycle_failed=bool(limited),
        consecutive_rpc_rate_limited=limited,
        rate_limit_streak_threshold=2,
        timer_enabled=timer_enabled,
        timer_active=timer_active,
        pause_recommended=pause,
    )


def set_health(monkeypatch, *reports):
    values = list(reports)

    def inspect(**kwargs):
        if len(values) > 1:
            return values.pop(0)
        return values[0]

    monkeypatch.setattr(
        MODULE.HEALTH,
        "inspect_timer_health",
        inspect,
    )
    monkeypatch.setattr(
        MODULE,
        "_health_source_identity",
        lambda: ("commit", "sha256"),
    )


def stateful_runner(*, active=True, enabled=True):
    state = {"active": active, "enabled": enabled, "commands": []}

    def runner(command, **kwargs):
        state["commands"].append(tuple(command))
        action = command[1]
        if action == "is-active":
            return subprocess.CompletedProcess(
                command,
                0 if state["active"] else 3,
                "active\n" if state["active"] else "inactive\n",
                "",
            )
        if action == "is-enabled":
            return subprocess.CompletedProcess(
                command,
                0 if state["enabled"] else 1,
                "enabled\n" if state["enabled"] else "disabled\n",
                "",
            )
        if action == "disable":
            assert command[2:] == ["--now", MODULE.TIMER_UNIT]
            state["active"] = False
            state["enabled"] = False
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    return state, runner


def run_autopause(monkeypatch, *reports, apply=True, runner=None):
    set_health(monkeypatch, *reports)
    if runner is None:
        _, runner = stateful_runner(active=True, enabled=True)
    return MODULE.autopause(
        database_path="/tmp/pio.db",
        apply=apply,
        now=lambda: datetime(
            2026, 10, 2, 20, 35, tzinfo=timezone.utc
        ),
        runner=runner,
    )


def test_autopause_disables_only_timer_after_stable_reviewed_health(
    monkeypatch,
):
    first = health(limited=2, pause=True)
    second = health(limited=2, pause=True)
    state, runner = stateful_runner(active=True, enabled=True)

    report = run_autopause(
        monkeypatch,
        first,
        second,
        runner=runner,
    )

    assert report.pool_address == "configured-pool"
    assert report.consecutive_rpc_rate_limited == 2
    assert report.repeated_provider_rejection is True
    assert report.pause_recommended is True
    assert report.applied is True
    assert report.future_timer_cycles_paused is True
    assert report.timer_enabled_after is False
    assert report.timer_active_after is False
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is True
    assert state["commands"] == [
        ("systemctl", "is-enabled", MODULE.TIMER_UNIT),
        ("systemctl", "is-active", MODULE.TIMER_UNIT),
        ("systemctl", "disable", "--now", MODULE.TIMER_UNIT),
        ("systemctl", "is-enabled", MODULE.TIMER_UNIT),
        ("systemctl", "is-active", MODULE.TIMER_UNIT),
    ]


def test_autopause_does_not_limit_healthy_timer_after_single_rejection(
    monkeypatch,
):
    report = run_autopause(
        monkeypatch,
        health(limited=1, pause=False),
    )

    assert report.consecutive_rpc_rate_limited == 1
    assert report.repeated_provider_rejection is False
    assert report.pause_recommended is False
    assert report.applied is False
    assert report.timer_enabled_after is True
    assert report.future_timer_cycles_paused is False


def test_autopause_uses_configured_health_pool_not_latest_database_pool(
    monkeypatch,
):
    report = run_autopause(
        monkeypatch,
        health(
            pool="configured-production-pool",
            limited=1,
            pause=False,
        ),
    )

    assert report.pool_address == "configured-production-pool"


def test_autopause_aborts_if_health_evidence_changes_before_disable(
    monkeypatch,
):
    first = health(limited=2, pause=True, evidence_start=10)
    second = health(limited=0, pause=False, evidence_start=11)
    state, runner = stateful_runner(active=True, enabled=True)

    report = run_autopause(
        monkeypatch,
        first,
        second,
        runner=runner,
    )

    assert report.applied is False
    assert report.failure_step == "HEALTH_CHANGED_BEFORE_APPLY"
    assert report.service_control_performed is False
    assert not any(
        command[:3] == ("systemctl", "disable", "--now")
        for command in state["commands"]
    )


def test_autopause_aborts_if_configured_pool_changes_before_disable(
    monkeypatch,
):
    first = health(pool="pool-a", limited=2, pause=True)
    second = health(pool="pool-b", limited=2, pause=True)
    state, runner = stateful_runner(active=True, enabled=True)

    report = run_autopause(
        monkeypatch,
        first,
        second,
        runner=runner,
    )

    assert report.applied is False
    assert report.failure_step == "HEALTH_CHANGED_BEFORE_APPLY"
    assert report.pool_address == "pool-a"
    assert state["commands"] == []


def test_autopause_aborts_if_timer_state_changes_after_health_revalidation(
    monkeypatch,
):
    first = health(limited=2, pause=True)
    second = health(limited=2, pause=True)
    state, runner = stateful_runner(active=False, enabled=True)

    report = run_autopause(
        monkeypatch,
        first,
        second,
        runner=runner,
    )

    assert report.applied is False
    assert report.failure_step == "TIMER_STATE_CHANGED_BEFORE_APPLY"
    assert report.service_control_performed is False
    assert not any(
        command[:3] == ("systemctl", "disable", "--now")
        for command in state["commands"]
    )


def test_autopause_dry_run_is_non_mutating(monkeypatch):
    state, runner = stateful_runner(active=True, enabled=True)
    report = run_autopause(
        monkeypatch,
        health(limited=2, pause=True),
        apply=False,
        runner=runner,
    )

    assert report.pause_recommended is True
    assert report.applied is False
    assert report.service_control_performed is False
    assert state["commands"] == []


def test_onfailure_unit_is_local_secret_free_and_pinned():
    evidence = EVIDENCE_SERVICE.read_text(encoding="utf-8")
    pause = PAUSE_SERVICE.read_text(encoding="utf-8")

    assert (
        "OnFailure=pio-phase2-isolated-rate-limit-pause.service"
        in evidence
    )
    assert (
        "autopause_phase2_isolated_timer.py --apply"
        in pause
    )
    assert "PrivateNetwork=true" in pause
    assert "RestrictAddressFamilies=AF_UNIX" in pause
    assert "EnvironmentFile=" not in pause
    assert "SOLANA_RPC_URL" not in pause
    assert "[Install]" not in pause
    assert "pio-phase2-isolated-add-detector" not in pause
    assert "prestate-stream" not in pause

    contract = INSTALL.UNIT_CONTRACT
    assert contract["pio-phase2-isolated-evidence-cycle.service"] == (
        INSTALL.git_blob_sha(EVIDENCE_SERVICE)
    )
    assert contract["pio-phase2-isolated-rate-limit-pause.service"] == (
        INSTALL.git_blob_sha(PAUSE_SERVICE)
    )
