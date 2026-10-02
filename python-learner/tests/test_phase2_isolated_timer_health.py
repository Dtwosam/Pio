from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_timer_health.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_timer_health",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


POOL = "pool"


def write_db(tmp_path: Path, cycles) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    db = root / "pio.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE advanced_edge_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edge_type TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                as_of TEXT,
                status TEXT NOT NULL,
                qualified INTEGER NOT NULL,
                evidence_json TEXT NOT NULL
            )
            """
        )
        for item in cycles:
            conn.execute(
                """
                INSERT INTO advanced_edge_evidence(
                    edge_type, pool_address, as_of, status,
                    qualified, evidence_json
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (
                    MODULE.PROGRESS_EDGE_TYPE,
                    POOL,
                    item["as_of"],
                    item["status"],
                    json.dumps(item["evidence"]),
                ),
            )
    return root


def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "pio.env"
    path.write_text(
        "SOLANA_RPC_URL=https://rpc.invalid/?api-key=secret\n"
        f"PIO_PHASE2_POSITION_POOL={POOL}\n",
        encoding="utf-8",
    )
    return path


def unit(name, *, active=False, enabled=False):
    return SimpleNamespace(
        name=name,
        active=active,
        enabled=enabled,
    )


def install_base(monkeypatch, tmp_path: Path):
    streams = tuple(
        name
        for name in MODULE.ACTIVATION.NEW_INACTIVE_UNITS
        if name.startswith("pio-phase2-isolated-prestate-stream@")
    )
    states = [
        unit(MODULE.DETECTOR_UNIT, active=True, enabled=True),
        *[unit(name, active=True, enabled=False) for name in streams],
        unit(MODULE.EVIDENCE_SERVICE, active=False, enabled=False),
        unit(MODULE.TIMER_UNIT, active=True, enabled=True),
        *[
            unit(name, active=False, enabled=False)
            for name in MODULE.ACTIVATION.LEGACY_UNITS
        ],
    ]
    monkeypatch.setattr(
        MODULE.ACTIVATION,
        "inspect_activation",
        lambda **kwargs: SimpleNamespace(
            runtime_ready=True,
            installed_units_exact=True,
            env_file_regular=True,
            env_keys=(
                SimpleNamespace(name="SOLANA_RPC_URL", configured=True),
                SimpleNamespace(
                    name="PIO_PHASE2_POSITION_POOL",
                    configured=True,
                ),
            ),
            position_pool_matches_detector_topology=True,
            data_files=(
                SimpleNamespace(
                    exists=True,
                    regular_file=True,
                    symlink=False,
                ),
            ),
            detector_state_valid=True,
            detector_cursors_complete=True,
            unit_states=tuple(states),
        ),
    )
    return streams


def systemctl_runner(command, **kwargs):
    action = command[1]
    if action == "is-active":
        return subprocess.CompletedProcess(
            command, 3, "inactive\n", ""
        )
    if action == "is-enabled":
        return subprocess.CompletedProcess(
            command, 1, "disabled\n", ""
        )
    raise AssertionError(action)


def cycle(*, at, status="COLLECTION_SUCCESS", limited=False):
    return {
        "as_of": at,
        "status": status,
        "evidence": {
            "stages_failed": 1 if status == "COLLECTION_FAILED" else 0,
            "stages_skipped": 2 if limited else 0,
            "rpc_rate_limited": limited,
            "rpc_circuit_open": limited,
            "stage_outcomes": (
                [
                    [
                        "POSITION_OBSERVATIONS",
                        "FAILED",
                        "RPC_RATE_LIMITED",
                    ],
                    [
                        "TRANSACTION_REINSPECTION",
                        "SKIPPED",
                        "RPC_CIRCUIT_OPEN",
                    ],
                ]
                if limited
                else []
            ),
        },
    }


def inspect(tmp_path, monkeypatch, cycles, *, threshold=2):
    install_base(monkeypatch, tmp_path)
    root = write_db(tmp_path, cycles)
    env = env_file(tmp_path)
    return MODULE.inspect_timer_health(
        runtime_root=tmp_path / "runtime",
        unit_destination=tmp_path / "systemd",
        env_file=env,
        data_root=root,
        rate_limit_streak_threshold=threshold,
        now=lambda: datetime(
            2026, 10, 2, 20, 30, tzinfo=timezone.utc
        ),
        runner=systemctl_runner,
    )


def test_running_timer_health_is_green_after_recent_success(
    tmp_path,
    monkeypatch,
):
    report = inspect(
        tmp_path,
        monkeypatch,
        [
            cycle(at="2026-10-02T20:20:00+00:00"),
            cycle(at="2026-10-02T20:05:00+00:00"),
        ],
    )

    assert report.collection_healthy is True
    assert report.pause_recommended is False
    assert report.consecutive_rpc_rate_limited == 0
    assert report.latest_cycle_recent is True
    assert report.latest_cycle_failed is False
    assert report.timer_active is True
    assert report.timer_enabled is True
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False


def test_two_consecutive_rate_limits_recommend_pause(
    tmp_path,
    monkeypatch,
):
    report = inspect(
        tmp_path,
        monkeypatch,
        [
            cycle(
                at="2026-10-02T20:20:00+00:00",
                status="COLLECTION_FAILED",
                limited=True,
            ),
            cycle(
                at="2026-10-02T20:05:00+00:00",
                status="COLLECTION_FAILED",
                limited=True,
            ),
            cycle(at="2026-10-02T19:50:00+00:00"),
        ],
    )

    assert report.consecutive_rpc_rate_limited == 2
    assert report.pause_recommended is True
    assert report.collection_healthy is False


def test_single_rate_limit_does_not_recommend_pause(
    tmp_path,
    monkeypatch,
):
    report = inspect(
        tmp_path,
        monkeypatch,
        [
            cycle(
                at="2026-10-02T20:20:00+00:00",
                status="COLLECTION_FAILED",
                limited=True,
            ),
            cycle(at="2026-10-02T20:05:00+00:00"),
        ],
    )

    assert report.consecutive_rpc_rate_limited == 1
    assert report.pause_recommended is False
    assert report.collection_healthy is False


def test_non_rpc_failure_degrades_health_without_pause(
    tmp_path,
    monkeypatch,
):
    report = inspect(
        tmp_path,
        monkeypatch,
        [
            cycle(
                at="2026-10-02T20:20:00+00:00",
                status="COLLECTION_FAILED",
                limited=False,
            ),
        ],
    )

    assert report.latest_cycle_failed is True
    assert report.consecutive_rpc_rate_limited == 0
    assert report.pause_recommended is False
    assert report.collection_healthy is False


def test_old_stage_outcomes_can_still_identify_rate_limit(
    tmp_path,
    monkeypatch,
):
    install_base(monkeypatch, tmp_path)
    root = write_db(
        tmp_path,
        [
            {
                "as_of": "2026-10-02T20:20:00+00:00",
                "status": "COLLECTION_FAILED",
                "evidence": {
                    "stages_failed": 1,
                    "stages_skipped": 2,
                    "stage_outcomes": [
                        [
                            "POSITION_OBSERVATIONS",
                            "FAILED",
                            "RPC_RATE_LIMITED",
                        ]
                    ],
                },
            }
        ],
    )
    env = env_file(tmp_path)

    report = MODULE.inspect_timer_health(
        env_file=env,
        data_root=root,
        rate_limit_streak_threshold=1,
        now=lambda: datetime(
            2026, 10, 2, 20, 30, tzinfo=timezone.utc
        ),
        runner=systemctl_runner,
    )

    assert report.consecutive_rpc_rate_limited == 1
    assert report.pause_recommended is True



def test_malformed_latest_telemetry_breaks_rate_limit_streak(
    tmp_path,
    monkeypatch,
):
    install_base(monkeypatch, tmp_path)
    root = tmp_path / "data"
    root.mkdir()
    db = root / "pio.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE advanced_edge_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edge_type TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                as_of TEXT,
                status TEXT NOT NULL,
                qualified INTEGER NOT NULL,
                evidence_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO advanced_edge_evidence(
                edge_type, pool_address, as_of, status,
                qualified, evidence_json
            ) VALUES (?, ?, ?, ?, 0, ?)
            """,
            (
                MODULE.PROGRESS_EDGE_TYPE,
                POOL,
                "2026-10-02T20:05:00+00:00",
                "COLLECTION_FAILED",
                json.dumps(
                    {
                        "rpc_rate_limited": True,
                        "rpc_circuit_open": True,
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO advanced_edge_evidence(
                edge_type, pool_address, as_of, status,
                qualified, evidence_json
            ) VALUES (?, ?, ?, ?, 0, ?)
            """,
            (
                MODULE.PROGRESS_EDGE_TYPE,
                POOL,
                "2026-10-02T20:20:00+00:00",
                "COLLECTION_FAILED",
                "{not-json",
            ),
        )

    env = env_file(tmp_path)
    report = MODULE.inspect_timer_health(
        env_file=env,
        data_root=root,
        rate_limit_streak_threshold=1,
        now=lambda: datetime(
            2026, 10, 2, 20, 30, tzinfo=timezone.utc
        ),
        runner=systemctl_runner,
    )

    assert report.cycles[0].as_of == "2026-10-02T20:20:00+00:00"
    assert report.cycles[0].rpc_rate_limited is False
    assert report.consecutive_rpc_rate_limited == 0
    assert report.pause_recommended is False
