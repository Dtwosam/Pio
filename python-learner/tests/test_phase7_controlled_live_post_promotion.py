from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_post_promotion.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_post_promotion",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


EVIDENCE = {
    "promotion_ready": True,
    "closed_positions": 3,
    "open_positions": 0,
    "reasons": [],
}


def _phase6_record() -> dict:
    return {
        "phase_name": MODULE.PHASE6,
        "promoted_at": "2026-09-29T17:00:00+00:00",
        "evidence_type": MODULE.PHASE6_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": {"promotion_ready": True},
    }


def _report() -> dict:
    evidence_sha = hashlib.sha256(
        MODULE._canonical_bytes(EVIDENCE)
    ).hexdigest()
    current = {
        "phase_name": MODULE.PHASE7,
        "promoted_at": "2026-09-29T19:00:00+00:00",
        "evidence_type": MODULE.PHASE7_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": dict(EVIDENCE),
    }
    history = {"id": 2, **current}
    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                MODULE.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "persistence_receipt_sha256": "1" * 64,
        "promotion_request_sha256": "2" * 64,
        "fresh_phase7_evidence_status_sha256": "3" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "phase_name": MODULE.PHASE7,
        "evidence_type": MODULE.PHASE7_EVIDENCE_TYPE,
        "promoted_at": current["promoted_at"],
        "history_id": 2,
        "evidence_sha256": evidence_sha,
        "current_record": current,
        "history_record": history,
        "phase6_current_record": _phase6_record(),
        "phase6_dependency_still_promoted": True,
        "current_record_matches_receipt": True,
        "history_record_matches_receipt": True,
        "phase7_history_count": 1,
        "exact_single_history_entry": True,
        "fresh_phase7_report_matches_receipt": True,
        "pio_database_sha256_before": "4" * 64,
        "pio_database_sha256_after": "4" * 64,
        "pio_wal_sha256_before": None,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_before": None,
        "pio_shm_sha256_after": None,
        "promotion_snapshot_read_only": True,
        "source_database_unchanged": True,
        "post_promotion_audit_ready": True,
        "phase7_promotion_confirmed": True,
        "phase7_promotion_persisted": True,
        "requires_separate_phase8_workflow": True,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified_by_audit": False,
    }
    return {
        **identity,
        "post_promotion_audit_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _create_db(
    path: Path,
    *,
    extra_phase7_history: bool = False,
) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE phase_promotion_evidence (
                phase_name TEXT PRIMARY KEY,
                promoted_at TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                qualified INTEGER NOT NULL,
                evidence_json TEXT NOT NULL
            );
            CREATE TABLE phase_promotion_evidence_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phase_name TEXT NOT NULL,
                promoted_at TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                qualified INTEGER NOT NULL,
                evidence_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence(
                phase_name, promoted_at, evidence_type, qualified, evidence_json
            ) VALUES (?, '2026-09-29T17:00:00+00:00', ?, 1, ?)
            """,
            (
                MODULE.PHASE6,
                MODULE.PHASE6_EVIDENCE_TYPE,
                json.dumps({"promotion_ready": True}),
            ),
        )
        evidence_json = json.dumps(EVIDENCE, separators=(",", ":"))
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence(
                phase_name, promoted_at, evidence_type, qualified, evidence_json
            ) VALUES (?, '2026-09-29T19:00:00+00:00', ?, 1, ?)
            """,
            (
                MODULE.PHASE7,
                MODULE.PHASE7_EVIDENCE_TYPE,
                evidence_json,
            ),
        )
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence_history(
                id, phase_name, promoted_at, evidence_type, qualified, evidence_json
            ) VALUES (2, ?, '2026-09-29T19:00:00+00:00', ?, 1, ?)
            """,
            (
                MODULE.PHASE7,
                MODULE.PHASE7_EVIDENCE_TYPE,
                evidence_json,
            ),
        )
        if extra_phase7_history:
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence_history(
                    phase_name, promoted_at, evidence_type, qualified, evidence_json
                ) VALUES (?, '2026-09-29T19:01:00+00:00', ?, 1, ?)
                """,
                (
                    MODULE.PHASE7,
                    MODULE.PHASE7_EVIDENCE_TYPE,
                    evidence_json,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["post_promotion_audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_audit_confirms_phase7_but_authorizes_no_live_work():
    report = _report()
    MODULE.validate_post_promotion_audit(report)

    assert report["phase7_promotion_confirmed"] is True
    assert report["phase7_promotion_persisted"] is True
    assert report["requires_separate_phase8_workflow"] is True
    assert report["live_submit_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False


def test_real_promotion_snapshot_is_read_only_and_exact():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _create_db(database)
        before = database.read_bytes()

        snapshot = MODULE._promotion_snapshot(database)

        after = database.read_bytes()
        assert before == after
        assert snapshot["current_record"]["phase_name"] == MODULE.PHASE7
        assert len(snapshot["history"]) == 1
        assert snapshot["history"][0]["id"] == 2
        assert snapshot["phase6_current_record"]["phase_name"] == MODULE.PHASE6
        assert snapshot["state_before"] == snapshot["state_after"]


def test_snapshot_exposes_extra_phase7_history_for_fail_closed_audit():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _create_db(database, extra_phase7_history=True)

        snapshot = MODULE._promotion_snapshot(database)

        assert len(snapshot["history"]) == 2


def test_resealed_audit_cannot_hide_extra_history():
    report = _report()
    report["phase7_history_count"] = 2
    _reseal(report)

    with pytest.raises(ValueError, match="requires one history entry"):
        MODULE.validate_post_promotion_audit(report)


def test_resealed_audit_cannot_tamper_persisted_evidence():
    report = _report()
    report["current_record"]["evidence"]["closed_positions"] = 999
    report["history_record"]["evidence"]["closed_positions"] = 999
    _reseal(report)

    with pytest.raises(ValueError, match="evidence digest mismatch"):
        MODULE.validate_post_promotion_audit(report)


def test_resealed_audit_cannot_authorize_live_submit():
    report = _report()
    report["live_submit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_post_promotion_audit(report)


def test_post_promotion_audit_is_read_only():
    source = TOOL.read_text(encoding="utf-8")

    assert "PRAGMA query_only=ON" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
    assert '"production_pio_database_modified_by_audit": False' in source
