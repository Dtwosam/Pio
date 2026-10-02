from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_rpc_efficiency.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_rpc_efficiency",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

POOL = "pool"


def init_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE phase2_position_observation_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempted_at TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                position_address TEXT NOT NULL,
                succeeded INTEGER NOT NULL,
                failure_category TEXT,
                capture_slot INTEGER
            );
            CREATE TABLE phase2_collection_task_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempted_at TEXT NOT NULL,
                stage TEXT NOT NULL,
                task_key TEXT NOT NULL,
                succeeded INTEGER NOT NULL,
                outcome_category TEXT NOT NULL
            );
            CREATE TABLE advanced_edge_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edge_type TEXT NOT NULL,
                pool_address TEXT,
                as_of TEXT NOT NULL,
                status TEXT NOT NULL,
                qualified INTEGER NOT NULL,
                evidence_json TEXT NOT NULL
            );
            """
        )


def write_env(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                f"PIO_PHASE2_POSITION_POOL={POOL}",
                "PIO_PHASE2_POSITION_DISCOVERY_CACHE_SECONDS=3600",
                "SOLANA_RPC_URL=https://secret.invalid/?api-key=never-print",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def runner_state(*, active: bool, enabled: bool):
    def runner(command, **kwargs):
        action = command[1]
        if action == "is-active":
            return subprocess.CompletedProcess(
                command,
                0 if active else 3,
                "active\n" if active else "inactive\n",
                "",
            )
        if action == "is-enabled":
            return subprocess.CompletedProcess(
                command,
                0 if enabled else 1,
                "enabled\n" if enabled else "disabled\n",
                "",
            )
        raise AssertionError(command)
    return runner


def test_status_reads_local_efficiency_signals_without_rpc_or_secrets(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "pio.db"
    init_db(db)
    env = tmp_path / "pio.env"
    write_env(env)

    with sqlite3.connect(db) as conn:
        conn.executemany(
            """
            INSERT INTO phase2_position_observation_attempts(
                attempted_at, pool_address, position_address,
                succeeded, failure_category, capture_slot
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ("2026-10-02T20:00:00+00:00", POOL, "a", 1, None, 10),
                ("2026-10-02T20:05:00+00:00", POOL, "b", 0, "EXECUTOR_FAILED", None),
            ),
        )
        conn.executemany(
            """
            INSERT INTO phase2_collection_task_attempts(
                attempted_at, stage, task_key, succeeded, outcome_category
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                ("2026-10-02T20:10:00+00:00", "TRANSACTION_REINSPECTION", "sig-a", 1, "INGESTED"),
                ("2026-10-02T20:11:00+00:00", "PRESTATE_VERIFICATION", "sig-b:1", 1, "VERDICT_ELIGIBLE"),
            ),
        )
        conn.execute(
            """
            INSERT INTO advanced_edge_evidence(
                edge_type, pool_address, as_of, status, qualified, evidence_json
            ) VALUES (?, ?, ?, ?, 0, ?)
            """,
            (
                MODULE.PROGRESS_EDGE_TYPE,
                POOL,
                "2026-10-02T20:15:00+00:00",
                "COLLECTION_SUCCESS",
                json.dumps(
                    {
                        "rpc_rate_limited": False,
                        "rpc_circuit_open": False,
                        "stages_failed": 0,
                        "stages_skipped": 0,
                    }
                ),
            ),
        )

    cache = {
        "format_version": 1,
        "pool_address": POOL,
        "captured_at": "2026-10-02T20:00:00+00:00",
        "discovery": {
            "positions_found": 2,
            "positions_returned": 2,
            "truncated": False,
            "positions": [
                {"position_address": "a"},
                {"position_address": "b"},
            ],
        },
    }
    (data / "phase2-position-discovery-cache.json").write_text(
        json.dumps(cache),
        encoding="utf-8",
    )

    report = MODULE.inspect_rpc_efficiency(
        data_root=data,
        env_file=env,
        now=lambda: datetime(2026, 10, 2, 20, 30, tzinfo=timezone.utc),
        runner=runner_state(active=True, enabled=True),
    )

    assert report.database_ready is True
    assert report.discovery_cache.reusable_now is True
    assert report.discovery_cache.positions_cached == 2
    assert report.position_attempts.attempts == 2
    assert report.position_attempts.succeeded == 1
    assert report.position_attempts.failed == 1
    assert report.reinspection_attempts.succeeded == 1
    assert report.prestate_attempts.succeeded == 1
    assert report.consecutive_rpc_rate_limited_cycles == 0
    assert report.pause_recommended is False
    assert report.attention_required is False
    assert report.collector_attempts_are_not_provider_credits is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False

    encoded = json.dumps(report.to_record())
    assert "never-print" not in encoded
    assert "SOLANA_RPC_URL" not in encoded


def test_status_recommends_pause_only_for_repeated_provider_rejection(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "pio.db"
    init_db(db)
    env = tmp_path / "pio.env"
    write_env(env)

    with sqlite3.connect(db) as conn:
        for as_of in (
            "2026-10-02T20:30:00+00:00",
            "2026-10-02T20:15:00+00:00",
        ):
            conn.execute(
                """
                INSERT INTO advanced_edge_evidence(
                    edge_type, pool_address, as_of, status, qualified,
                    evidence_json
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (
                    MODULE.PROGRESS_EDGE_TYPE,
                    POOL,
                    as_of,
                    "COLLECTION_FAILED",
                    json.dumps(
                        {
                            "rpc_rate_limited": True,
                            "rpc_circuit_open": True,
                            "stages_failed": 1,
                            "stages_skipped": 2,
                        }
                    ),
                ),
            )

    report = MODULE.inspect_rpc_efficiency(
        data_root=data,
        env_file=env,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner_state(active=True, enabled=True),
    )

    assert report.consecutive_rpc_rate_limited_cycles == 2
    assert report.repeated_provider_rejection is True
    assert report.pause_recommended is True
    assert report.protected_from_future_timer_cycles is False
    assert report.attention_required is True


def test_status_recognizes_timer_already_paused_after_rate_limits(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "pio.db"
    init_db(db)
    env = tmp_path / "pio.env"
    write_env(env)

    with sqlite3.connect(db) as conn:
        for as_of in (
            "2026-10-02T20:30:00+00:00",
            "2026-10-02T20:15:00+00:00",
        ):
            conn.execute(
                """
                INSERT INTO advanced_edge_evidence(
                    edge_type, pool_address, as_of, status, qualified,
                    evidence_json
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (
                    MODULE.PROGRESS_EDGE_TYPE,
                    POOL,
                    as_of,
                    "COLLECTION_FAILED",
                    json.dumps({"rpc_rate_limited": True}),
                ),
            )

    report = MODULE.inspect_rpc_efficiency(
        data_root=data,
        env_file=env,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner_state(active=False, enabled=False),
    )

    assert report.repeated_provider_rejection is True
    assert report.timer_paused is True
    assert report.pause_recommended is False
    assert report.protected_from_future_timer_cycles is True
    assert report.attention_required is False


def test_status_counts_rate_limited_attempt_rows_without_calling_provider(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "pio.db"
    init_db(db)
    env = tmp_path / "pio.env"
    write_env(env)

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO phase2_position_observation_attempts(
                attempted_at, pool_address, position_address,
                succeeded, failure_category, capture_slot
            ) VALUES (?, ?, ?, 0, 'RPC_RATE_LIMITED', NULL)
            """,
            ("2026-10-02T20:00:00+00:00", POOL, "a"),
        )
        conn.executemany(
            """
            INSERT INTO phase2_collection_task_attempts(
                attempted_at, stage, task_key, succeeded, outcome_category
            ) VALUES (?, ?, ?, 0, 'RPC_RATE_LIMITED')
            """,
            (
                ("2026-10-02T20:01:00+00:00", "TRANSACTION_REINSPECTION", "sig"),
                ("2026-10-02T20:02:00+00:00", "PRESTATE_VERIFICATION", "sig:1"),
            ),
        )

    report = MODULE.inspect_rpc_efficiency(
        data_root=data,
        env_file=env,
        now=lambda: datetime(2026, 10, 2, 20, 30, tzinfo=timezone.utc),
        runner=runner_state(active=False, enabled=False),
    )

    assert report.position_attempts.rpc_rate_limited == 1
    assert report.reinspection_attempts.rpc_rate_limited == 1
    assert report.prestate_attempts.rpc_rate_limited == 1
    assert report.rpc_called is False


def test_status_flags_symlinked_cache_without_following_it(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "pio.db"
    init_db(db)
    env = tmp_path / "pio.env"
    write_env(env)

    target = tmp_path / "external.json"
    target.write_text("{}", encoding="utf-8")
    (data / "phase2-position-discovery-cache.json").symlink_to(target)

    report = MODULE.inspect_rpc_efficiency(
        data_root=data,
        env_file=env,
        now=lambda: datetime(2026, 10, 2, 20, 30, tzinfo=timezone.utc),
        runner=runner_state(active=False, enabled=False),
    )

    assert report.discovery_cache.symlink is True
    assert report.discovery_cache.regular_file is False
    assert report.discovery_cache.reusable_now is False
    assert report.attention_required is True



def test_status_treats_enabled_inactive_timer_as_future_rpc_work(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "pio.db"
    init_db(db)
    env = tmp_path / "pio.env"
    write_env(env)

    with sqlite3.connect(db) as conn:
        for as_of in (
            "2026-10-02T20:30:00+00:00",
            "2026-10-02T20:15:00+00:00",
        ):
            conn.execute(
                """
                INSERT INTO advanced_edge_evidence(
                    edge_type, pool_address, as_of, status, qualified,
                    evidence_json
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (
                    MODULE.PROGRESS_EDGE_TYPE,
                    POOL,
                    as_of,
                    "COLLECTION_FAILED",
                    json.dumps({"rpc_rate_limited": True}),
                ),
            )

    report = MODULE.inspect_rpc_efficiency(
        data_root=data,
        env_file=env,
        now=lambda: datetime(2026, 10, 2, 20, 35, tzinfo=timezone.utc),
        runner=runner_state(active=False, enabled=True),
    )

    assert report.timer_active is False
    assert report.timer_enabled is True
    assert report.timer_paused is False
    assert report.pause_recommended is True
    assert report.protected_from_future_timer_cycles is False
    assert report.attention_required is True
