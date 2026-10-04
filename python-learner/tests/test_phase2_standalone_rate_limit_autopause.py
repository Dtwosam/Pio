from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import os
import shutil
import sqlite3
import subprocess
import sys


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


def init_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE advanced_edge_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edge_type TEXT NOT NULL,
                pool_address TEXT,
                as_of TEXT NOT NULL,
                status TEXT NOT NULL,
                qualified INTEGER NOT NULL,
                evidence_json TEXT NOT NULL
            )
            """
        )


def add_cycle(
    path: Path,
    *,
    as_of: str,
    rate_limited: bool | None,
    raw_json: str | None = None,
) -> None:
    evidence = (
        raw_json
        if raw_json is not None
        else json.dumps(
            {
                "rpc_rate_limited": rate_limited,
                "rpc_circuit_open": bool(rate_limited),
            }
        )
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO advanced_edge_evidence(
                edge_type, pool_address, as_of, status,
                qualified, evidence_json
            ) VALUES (?, 'pool', ?, 'COLLECTION_FAILED', 0, ?)
            """,
            (MODULE.PROGRESS_EDGE_TYPE, as_of, evidence),
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


def test_autopause_disables_only_timer_after_two_recent_rate_limits(tmp_path):
    db = tmp_path / "pio.db"
    init_db(db)
    add_cycle(
        db,
        as_of="2026-10-02T20:30:00+00:00",
        rate_limited=True,
    )
    add_cycle(
        db,
        as_of="2026-10-02T20:15:00+00:00",
        rate_limited=True,
    )
    state, runner = stateful_runner(active=True, enabled=True)

    report = MODULE.autopause(
        database_path=db,
        apply=True,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner,
    )

    assert report.database_ready is True
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
    assert (
        "systemctl",
        "disable",
        "--now",
        MODULE.TIMER_UNIT,
    ) in state["commands"]


def test_autopause_does_not_limit_healthy_timer_after_single_rejection(tmp_path):
    db = tmp_path / "pio.db"
    init_db(db)
    add_cycle(
        db,
        as_of="2026-10-02T20:30:00+00:00",
        rate_limited=True,
    )
    state, runner = stateful_runner(active=True, enabled=True)

    report = MODULE.autopause(
        database_path=db,
        apply=True,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner,
    )

    assert report.consecutive_rpc_rate_limited == 1
    assert report.repeated_provider_rejection is False
    assert report.pause_recommended is False
    assert report.applied is False
    assert report.timer_enabled_after is True
    assert report.future_timer_cycles_paused is False
    assert not any(
        command[:3] == ("systemctl", "disable", "--now")
        for command in state["commands"]
    )


def test_autopause_malformed_latest_telemetry_breaks_old_rate_limit_streak(
    tmp_path,
):
    db = tmp_path / "pio.db"
    init_db(db)
    add_cycle(
        db,
        as_of="2026-10-02T20:15:00+00:00",
        rate_limited=True,
    )
    add_cycle(
        db,
        as_of="2026-10-02T20:30:00+00:00",
        rate_limited=None,
        raw_json="{not-json",
    )
    state, runner = stateful_runner(active=True, enabled=True)

    report = MODULE.autopause(
        database_path=db,
        apply=True,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner,
    )

    assert report.consecutive_rpc_rate_limited == 0
    assert report.pause_recommended is False
    assert report.applied is False
    assert state["enabled"] is True


def test_autopause_uses_latest_pool_only(tmp_path):
    db = tmp_path / "pio.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO advanced_edge_evidence(
                edge_type, pool_address, as_of, status,
                qualified, evidence_json
            ) VALUES (?, 'old-pool', ?, 'COLLECTION_FAILED', 0, ?)
            """,
            (
                MODULE.PROGRESS_EDGE_TYPE,
                "2026-10-02T20:00:00+00:00",
                json.dumps({"rpc_rate_limited": True}),
            ),
        )
        conn.execute(
            """
            INSERT INTO advanced_edge_evidence(
                edge_type, pool_address, as_of, status,
                qualified, evidence_json
            ) VALUES (?, 'pool', ?, 'COLLECTION_SUCCESS', 0, ?)
            """,
            (
                MODULE.PROGRESS_EDGE_TYPE,
                "2026-10-02T20:30:00+00:00",
                json.dumps({"rpc_rate_limited": False}),
            ),
        )
    _, runner = stateful_runner(active=True, enabled=True)

    report = MODULE.autopause(
        database_path=db,
        apply=True,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner,
    )

    assert report.pool_address == "pool"
    assert report.consecutive_rpc_rate_limited == 0
    assert report.pause_recommended is False


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



def test_autopause_does_not_pause_on_stale_rate_limit_streak(tmp_path):
    db = tmp_path / "pio.db"
    init_db(db)
    add_cycle(
        db,
        as_of="2026-10-02T18:30:00+00:00",
        rate_limited=True,
    )
    add_cycle(
        db,
        as_of="2026-10-02T18:15:00+00:00",
        rate_limited=True,
    )
    state, runner = stateful_runner(active=True, enabled=True)

    report = MODULE.autopause(
        database_path=db,
        apply=True,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner,
    )

    assert report.consecutive_rpc_rate_limited == 2
    assert report.repeated_provider_rejection is True
    assert report.latest_cycle_recent is False
    assert report.pause_recommended is False
    assert report.applied is False
    assert state["enabled"] is True



def test_autopause_refuses_database_replacement_before_pause(tmp_path):
    db = tmp_path / "pio.db"
    init_db(db)
    add_cycle(
        db,
        as_of="2026-10-02T20:30:00+00:00",
        rate_limited=True,
    )
    add_cycle(
        db,
        as_of="2026-10-02T20:15:00+00:00",
        rate_limited=True,
    )
    replacement = tmp_path / "replacement.db"
    shutil.copy2(db, replacement)
    state = {"active": True, "enabled": True, "commands": []}

    def runner(command, **kwargs):
        state["commands"].append(tuple(command))
        action = command[1]
        if action == "is-enabled":
            return subprocess.CompletedProcess(
                command, 0, "enabled\n", ""
            )
        if action == "is-active":
            if not state.get("replaced"):
                os.replace(replacement, db)
                state["replaced"] = True
            return subprocess.CompletedProcess(
                command, 0, "active\n", ""
            )
        if action == "disable":
            raise AssertionError("timer must not be disabled after DB replacement")
        raise AssertionError(command)

    report = MODULE.autopause(
        database_path=db,
        apply=True,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner,
    )

    assert report.pause_recommended is True
    assert report.applied is False
    assert report.failure_step == "EVIDENCE_REVALIDATION"
    assert report.service_control_performed is False
    assert not any(
        command[:3] == ("systemctl", "disable", "--now")
        for command in state["commands"]
    )


def test_autopause_refuses_newer_evidence_before_pause(tmp_path):
    db = tmp_path / "pio.db"
    init_db(db)
    add_cycle(
        db,
        as_of="2026-10-02T20:30:00+00:00",
        rate_limited=True,
    )
    add_cycle(
        db,
        as_of="2026-10-02T20:15:00+00:00",
        rate_limited=True,
    )
    state = {"commands": [], "inserted": False}

    def runner(command, **kwargs):
        state["commands"].append(tuple(command))
        action = command[1]
        if action == "is-enabled":
            return subprocess.CompletedProcess(
                command, 0, "enabled\n", ""
            )
        if action == "is-active":
            if not state["inserted"]:
                add_cycle(
                    db,
                    as_of="2026-10-02T20:34:00+00:00",
                    rate_limited=False,
                )
                state["inserted"] = True
            return subprocess.CompletedProcess(
                command, 0, "active\n", ""
            )
        if action == "disable":
            raise AssertionError("timer must not be disabled on evidence drift")
        raise AssertionError(command)

    report = MODULE.autopause(
        database_path=db,
        apply=True,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner,
    )

    assert report.pause_recommended is True
    assert report.applied is False
    assert report.failure_step == "EVIDENCE_CHANGED_BEFORE_PAUSE"
    assert report.service_control_performed is False


def test_autopause_refuses_timer_state_drift_before_pause(tmp_path):
    db = tmp_path / "pio.db"
    init_db(db)
    add_cycle(
        db,
        as_of="2026-10-02T20:30:00+00:00",
        rate_limited=True,
    )
    add_cycle(
        db,
        as_of="2026-10-02T20:15:00+00:00",
        rate_limited=True,
    )
    state = {"commands": [], "enabled_checks": 0}

    def runner(command, **kwargs):
        state["commands"].append(tuple(command))
        action = command[1]
        if action == "is-enabled":
            state["enabled_checks"] += 1
            enabled = state["enabled_checks"] == 1
            return subprocess.CompletedProcess(
                command,
                0 if enabled else 1,
                "enabled\n" if enabled else "disabled\n",
                "",
            )
        if action == "is-active":
            return subprocess.CompletedProcess(
                command, 0, "active\n", ""
            )
        if action == "disable":
            raise AssertionError("timer must not be disabled after state drift")
        raise AssertionError(command)

    report = MODULE.autopause(
        database_path=db,
        apply=True,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner,
    )

    assert report.pause_recommended is True
    assert report.applied is False
    assert report.failure_step == "TIMER_STATE_CHANGED_BEFORE_PAUSE"
    assert report.timer_enabled_after is False
    assert report.timer_active_after is True
    assert report.future_timer_cycles_paused is True
    assert report.service_control_performed is False


def test_autopause_symlink_database_is_not_ready(tmp_path):
    target = tmp_path / "pio-real.db"
    init_db(target)
    db = tmp_path / "pio.db"
    db.symlink_to(target)
    state, runner = stateful_runner(active=True, enabled=True)

    report = MODULE.autopause(
        database_path=db,
        apply=True,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner,
    )

    assert report.database_ready is False
    assert report.pause_recommended is False
    assert report.applied is False
    assert state["enabled"] is True
