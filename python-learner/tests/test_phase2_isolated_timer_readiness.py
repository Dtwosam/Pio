from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import stat
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_timer_readiness.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_timer_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


FINISHED = "2026-10-02T19:00:00+00:00"


def runtime_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "runtime"
    release = root / "releases" / "pin"
    executor = release / "rust-executor/target/release/meteora-executor"
    executor.parent.mkdir(parents=True)
    executor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executor.chmod(executor.stat().st_mode | stat.S_IXUSR)
    watcher = (
        release
        / "rust-executor/target/release/pio-phase2-account-watch"
    )
    watcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    watcher.chmod(watcher.stat().st_mode | stat.S_IXUSR)
    identity = {
        "format_version": 1,
        "pinned_source_head": "pin",
        "release_path": str(release),
        "reviewed_check_commit": "a" * 40,
        "reviewed_check_sha256": "b" * 64,
        "executor_sha256": hashlib.sha256(executor.read_bytes()).hexdigest(),
        "watch_executor_sha256": hashlib.sha256(
            watcher.read_bytes()
        ).hexdigest(),
    }
    (release / MODULE.SMOKE._IDENTITY_FILENAME).write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    current = root / "current"
    current.symlink_to(Path("releases") / "pin")
    return root, release


def create_database(tmp_path: Path, evidence_id=123) -> Path:
    path = tmp_path / "pio.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE advanced_edge_evidence (
                id INTEGER PRIMARY KEY,
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
                id, edge_type, pool_address, as_of,
                status, qualified, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                MODULE.PROGRESS_EDGE_TYPE,
                "pool",
                FINISHED,
                "COLLECTION_PARTIAL",
                0,
                json.dumps(
                    {
                        "qualified": False,
                        "promotion_gate_evaluated": False,
                        "phase_promotion_performed": False,
                        "live_authorized": False,
                        "actionable": False,
                    }
                ),
            ),
        )
    return path


def receipt(tmp_path: Path, release: Path, evidence_id=123) -> Path:
    path = tmp_path / "receipt.json"
    executor = release / "rust-executor/target/release/meteora-executor"
    watcher = (
        release
        / "rust-executor/target/release/pio-phase2-account-watch"
    )
    identity_path = release / MODULE.SMOKE._IDENTITY_FILENAME
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "runtime_target": str(release),
                "runtime_executor_sha256": hashlib.sha256(
                    executor.read_bytes()
                ).hexdigest(),
                "runtime_watch_executor_sha256": hashlib.sha256(
                    watcher.read_bytes()
                ).hexdigest(),
                "stage_identity_sha256": hashlib.sha256(
                    identity_path.read_bytes()
                ).hexdigest(),
                "progress_evidence_id": evidence_id,
                "finished_at": FINISHED,
                "stage_statuses": [
                    {
                        "name": "POSITION_OBSERVATIONS",
                        "status": "PARTIAL",
                        "failure_category": None,
                    },
                    {
                        "name": "RECONCILIATION_CORPUS",
                        "status": "PARTIAL",
                        "failure_category": None,
                    },
                ],
                "smoke_passed": True,
                "eligible_for_timer_enable_preflight": True,
            }
        ),
        encoding="utf-8",
    )
    return path


def install_smoke_ready(monkeypatch, *, ready=True):
    monkeypatch.setattr(
        MODULE.SMOKE.READINESS,
        "inspect_smoke_readiness",
        lambda **kwargs: SimpleNamespace(smoke_ready=ready),
    )


def test_timer_readiness_verifies_receipt_and_immutable_progress_row(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    db = create_database(data)
    receipt_path = receipt(tmp_path, release)

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=data,
        receipt_path=receipt_path,
        now=lambda: datetime(
            2026, 10, 2, 19, 10, tzinfo=timezone.utc
        ),
    )

    assert db.exists()
    assert report.timer_ready is True
    assert report.receipt_runtime_matches is True
    assert report.receipt_identity_fields_valid is True
    assert report.receipt_executor_identity_matches is True
    assert report.receipt_watch_executor_identity_matches is True
    assert report.receipt_stage_identity_matches is True
    assert report.receipt_fresh is True
    assert report.evidence_row_present is True
    assert report.evidence_row_matches_receipt is True
    assert report.latest_progress_evidence_id == 123
    assert report.receipt_is_latest_for_pool is True
    assert report.evidence_row_non_qualified is True
    assert report.evidence_row_no_promotion is True
    assert report.read_only is True
    assert report.rpc_called is False
    assert report.database_write_performed is False
    assert report.service_control_performed is False


def test_timer_readiness_rejects_stale_receipt(tmp_path, monkeypatch):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    create_database(data)
    receipt_path = receipt(tmp_path, release)

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=data,
        receipt_path=receipt_path,
        max_receipt_age_seconds=1800,
        now=lambda: datetime(
            2026, 10, 2, 20, 0, tzinfo=timezone.utc
        ),
    )

    assert report.receipt_fresh is False
    assert report.timer_ready is False


def test_timer_readiness_rejects_runtime_mismatch(tmp_path, monkeypatch):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    create_database(data)
    receipt_path = receipt(tmp_path, release)
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["runtime_target"] = str(tmp_path / "other-release")
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=data,
        receipt_path=receipt_path,
        now=lambda: datetime(
            2026, 10, 2, 19, 10, tzinfo=timezone.utc
        ),
    )

    assert report.receipt_runtime_matches is False
    assert report.timer_ready is False


def test_timer_readiness_rejects_failed_stage_receipt(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    create_database(data)
    receipt_path = receipt(tmp_path, release)
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["stage_statuses"][0] = {
        "name": "POSITION_OBSERVATIONS",
        "status": "FAILED",
        "failure_category": "RPC_RATE_LIMITED",
    }
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=data,
        receipt_path=receipt_path,
        now=lambda: datetime(
            2026, 10, 2, 19, 10, tzinfo=timezone.utc
        ),
    )

    assert report.receipt_stage_statuses_valid is False
    assert report.timer_ready is False


def test_timer_readiness_rejects_missing_database_evidence(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    create_database(data, evidence_id=999)
    receipt_path = receipt(tmp_path, release, evidence_id=123)

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=data,
        receipt_path=receipt_path,
        now=lambda: datetime(
            2026, 10, 2, 19, 10, tzinfo=timezone.utc
        ),
    )

    assert report.evidence_row_present is False
    assert report.timer_ready is False


def test_timer_readiness_rejects_promoting_evidence_row(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    create_database(data)
    with sqlite3.connect(data / "pio.db") as conn:
        evidence = {
            "qualified": False,
            "promotion_gate_evaluated": True,
            "phase_promotion_performed": False,
            "live_authorized": False,
            "actionable": False,
        }
        conn.execute(
            "UPDATE advanced_edge_evidence SET evidence_json = ? WHERE id = 123",
            (json.dumps(evidence),),
        )
    receipt_path = receipt(tmp_path, release)

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=data,
        receipt_path=receipt_path,
        now=lambda: datetime(
            2026, 10, 2, 19, 10, tzinfo=timezone.utc
        ),
    )

    assert report.evidence_row_no_promotion is False
    assert report.timer_ready is False



def test_timer_readiness_rejects_receipt_older_than_latest_pool_progress(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    create_database(data)
    with sqlite3.connect(data / "pio.db") as conn:
        conn.execute(
            """
            INSERT INTO advanced_edge_evidence(
                id, edge_type, pool_address, as_of,
                status, qualified, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                124,
                MODULE.PROGRESS_EDGE_TYPE,
                "pool",
                "2026-10-02T19:05:00+00:00",
                "COLLECTION_FAILED",
                0,
                json.dumps(
                    {
                        "qualified": False,
                        "promotion_gate_evaluated": False,
                        "phase_promotion_performed": False,
                        "live_authorized": False,
                        "actionable": False,
                        "rpc_rate_limited": True,
                    }
                ),
            ),
        )
    receipt_path = receipt(tmp_path, release, evidence_id=123)

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=data,
        receipt_path=receipt_path,
        now=lambda: datetime(
            2026, 10, 2, 19, 10, tzinfo=timezone.utc
        ),
    )

    assert report.evidence_row_matches_receipt is True
    assert report.latest_progress_evidence_id == 124
    assert report.receipt_is_latest_for_pool is False
    assert report.timer_ready is False


def test_timer_readiness_ignores_newer_progress_for_other_pool(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    create_database(data)
    with sqlite3.connect(data / "pio.db") as conn:
        conn.execute(
            """
            INSERT INTO advanced_edge_evidence(
                id, edge_type, pool_address, as_of,
                status, qualified, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                124,
                MODULE.PROGRESS_EDGE_TYPE,
                "other-pool",
                "2026-10-02T19:05:00+00:00",
                "COLLECTION_FAILED",
                0,
                json.dumps(
                    {
                        "qualified": False,
                        "promotion_gate_evaluated": False,
                        "phase_promotion_performed": False,
                        "live_authorized": False,
                        "actionable": False,
                    }
                ),
            ),
        )
    receipt_path = receipt(tmp_path, release, evidence_id=123)

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=data,
        receipt_path=receipt_path,
        now=lambda: datetime(
            2026, 10, 2, 19, 10, tzinfo=timezone.utc
        ),
    )

    assert report.latest_progress_evidence_id == 123
    assert report.receipt_is_latest_for_pool is True
    assert report.timer_ready is True



def test_timer_readiness_rejects_same_content_receipt_replacement(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    create_database(data)
    receipt_path = receipt(tmp_path, release)
    original = MODULE._read_progress_snapshot

    def replacing_reader(database_path, evidence_id):
        result = original(database_path, evidence_id)
        encoded = receipt_path.read_bytes()
        receipt_path.unlink()
        receipt_path.write_bytes(encoded)
        return result

    monkeypatch.setattr(MODULE, "_read_progress_snapshot", replacing_reader)

    with pytest.raises(ValueError, match="smoke receipt path changed"):
        MODULE.inspect_timer_readiness(
            runtime_root=runtime_root,
            data_root=data,
            receipt_path=receipt_path,
            now=lambda: datetime(
                2026, 10, 2, 19, 10, tzinfo=timezone.utc
            ),
        )


def test_timer_readiness_rejects_same_content_database_replacement(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    database = create_database(data)
    receipt_path = receipt(tmp_path, release)
    original = MODULE._read_progress_snapshot

    def replacing_reader(database_path, evidence_id):
        result = original(database_path, evidence_id)
        encoded = database.read_bytes()
        database.unlink()
        database.write_bytes(encoded)
        return result

    monkeypatch.setattr(MODULE, "_read_progress_snapshot", replacing_reader)

    with pytest.raises(ValueError, match="database path changed"):
        MODULE.inspect_timer_readiness(
            runtime_root=runtime_root,
            data_root=data,
            receipt_path=receipt_path,
            now=lambda: datetime(
                2026, 10, 2, 19, 10, tzinfo=timezone.utc
            ),
        )


def test_timer_readiness_rejects_runtime_current_retarget(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    other = runtime_root / "releases" / "other"
    other.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    create_database(data)
    receipt_path = receipt(tmp_path, release)
    current = runtime_root / "current"
    original = MODULE._read_progress_snapshot

    def retargeting_reader(database_path, evidence_id):
        result = original(database_path, evidence_id)
        current.unlink()
        current.symlink_to(Path("releases") / "other")
        return result

    monkeypatch.setattr(MODULE, "_read_progress_snapshot", retargeting_reader)

    with pytest.raises(ValueError, match="runtime current changed"):
        MODULE.inspect_timer_readiness(
            runtime_root=runtime_root,
            data_root=data,
            receipt_path=receipt_path,
            now=lambda: datetime(
                2026, 10, 2, 19, 10, tzinfo=timezone.utc
            ),
        )


def test_timer_readiness_snapshot_guard_can_be_rechecked(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    create_database(data)
    receipt_path = receipt(tmp_path, release)

    report, snapshot = MODULE.capture_timer_readiness(
        runtime_root=runtime_root,
        data_root=data,
        receipt_path=receipt_path,
        now=lambda: datetime(
            2026, 10, 2, 19, 10, tzinfo=timezone.utc
        ),
    )

    assert report.timer_ready is True
    MODULE.assert_timer_readiness_snapshot_stable(snapshot)



def test_timer_readiness_rejects_legacy_receipt_without_executor_identity(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    database = create_database(tmp_path)
    receipt_path = receipt(tmp_path, release)
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload.pop("runtime_executor_sha256")
    payload.pop("runtime_watch_executor_sha256")
    payload.pop("stage_identity_sha256")
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=database.parent,
        receipt_path=receipt_path,
        now=lambda: datetime(2026, 10, 2, 19, 5, tzinfo=timezone.utc),
    )

    assert report.receipt_identity_fields_valid is False
    assert report.receipt_valid is False
    assert report.timer_ready is False


def test_timer_readiness_rejects_executor_hash_mismatch(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    database = create_database(tmp_path)
    receipt_path = receipt(tmp_path, release)
    executor = release / "rust-executor/target/release/meteora-executor"
    executor.write_text("#!/bin/sh\necho tampered\n", encoding="utf-8")
    executor.chmod(executor.stat().st_mode | stat.S_IXUSR)

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=database.parent,
        receipt_path=receipt_path,
        now=lambda: datetime(2026, 10, 2, 19, 5, tzinfo=timezone.utc),
    )

    assert report.receipt_executor_identity_matches is False
    assert report.timer_ready is False


def test_timer_readiness_rejects_account_watch_hash_mismatch(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    database = create_database(tmp_path)
    receipt_path = receipt(tmp_path, release)
    watcher = (
        release
        / "rust-executor/target/release/pio-phase2-account-watch"
    )
    watcher.write_text("#!/bin/sh\necho tampered\n", encoding="utf-8")
    watcher.chmod(watcher.stat().st_mode | stat.S_IXUSR)

    report = MODULE.inspect_timer_readiness(
        runtime_root=runtime_root,
        data_root=database.parent,
        receipt_path=receipt_path,
        now=lambda: datetime(2026, 10, 2, 19, 5, tzinfo=timezone.utc),
    )

    assert report.receipt_watch_executor_identity_matches is False
    assert report.timer_ready is False


def test_timer_readiness_snapshot_detects_identity_sidecar_replacement(
    tmp_path,
    monkeypatch,
):
    install_smoke_ready(monkeypatch)
    runtime_root, release = runtime_tree(tmp_path)
    database = create_database(tmp_path)
    receipt_path = receipt(tmp_path, release)

    report, snapshot = MODULE.capture_timer_readiness(
        runtime_root=runtime_root,
        data_root=database.parent,
        receipt_path=receipt_path,
        now=lambda: datetime(2026, 10, 2, 19, 5, tzinfo=timezone.utc),
    )
    assert report.timer_ready is True

    identity_path = release / MODULE.SMOKE._IDENTITY_FILENAME
    encoded = identity_path.read_bytes()
    identity_path.unlink()
    identity_path.write_bytes(encoded)

    with pytest.raises(
        ValueError,
        match="staged runtime identity manifest path changed",
    ):
        MODULE.assert_timer_readiness_snapshot_stable(snapshot)
