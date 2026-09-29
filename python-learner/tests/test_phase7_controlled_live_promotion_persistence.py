from __future__ import annotations

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
    / "persist_phase7_controlled_live_promotion.py"
)

SPEC = importlib.util.spec_from_file_location(
    "persist_phase7_controlled_live_promotion",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


EVIDENCE = {
    "promotion_ready": True,
    "criteria": {"min_closed_positions": 3},
    "closed_positions": 3,
    "open_positions": 0,
    "reasons": [],
}


def _create_db(path: Path, *, phase7_existing: bool = False) -> None:
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
            CREATE TRIGGER phase_promotion_history_no_update
            BEFORE UPDATE ON phase_promotion_evidence_history
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'phase_promotion_evidence_history is immutable'
                );
            END;
            CREATE TRIGGER phase_promotion_history_no_delete
            BEFORE DELETE ON phase_promotion_evidence_history
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'phase_promotion_evidence_history is immutable'
                );
            END;
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
        if phase7_existing:
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence(
                    phase_name, promoted_at, evidence_type, qualified, evidence_json
                ) VALUES (?, '2026-09-29T18:00:00+00:00', ?, 1, ?)
                """,
                (
                    MODULE.PHASE7,
                    MODULE.PHASE7_EVIDENCE_TYPE,
                    json.dumps(EVIDENCE),
                ),
            )
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence_history(
                    phase_name, promoted_at, evidence_type, qualified, evidence_json
                ) VALUES (?, '2026-09-29T18:00:00+00:00', ?, 1, ?)
                """,
                (
                    MODULE.PHASE7,
                    MODULE.PHASE7_EVIDENCE_TYPE,
                    json.dumps(EVIDENCE),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _receipt() -> dict:
    evidence_sha = hashlib.sha256(
        MODULE._canonical_bytes(EVIDENCE)
    ).hexdigest()
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
        "saved_promotion_readiness_sha256": "1" * 64,
        "fresh_promotion_readiness_sha256": "1" * 64,
        "phase7_evidence_status_sha256": "2" * 64,
        "promotion_request_sha256": "3" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "approver_principal": "ops@example.com",
        "approval_id": "11111111-2222-4333-8444-555555555555",
        "phase_name": MODULE.PHASE7,
        "evidence_type": MODULE.PHASE7_EVIDENCE_TYPE,
        "promoted_at": "2026-09-29T18:00:00+00:00",
        "evidence_record": dict(EVIDENCE),
        "evidence_sha256": evidence_sha,
        "phase7_report_sha256": evidence_sha,
        "runtime_lock_used": True,
        "schema_contract_verified": True,
        "phase6_dependency_rechecked": True,
        "in_transaction_replay_check_passed": True,
        "history_id": 1,
        "history_inserted": True,
        "current_record_inserted": True,
        "rows_changed": 2,
        "history_record_verified": True,
        "current_record_verified": True,
        "database_transaction_committed": True,
        "phase7_promotion_persistence_authorized": True,
        "phase7_promotion_persisted": True,
        "requires_post_promotion_audit": True,
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
        "production_pio_database_modified_by_executor": True,
    }
    return {
        **identity,
        "receipt_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(receipt: dict) -> None:
    identity = {field: receipt[field] for field in MODULE.RECEIPT_FIELDS}
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_atomic_transaction_inserts_exact_phase7_current_and_history():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _create_db(database)

        conn = sqlite3.connect(database)
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = MODULE._persist_exact_phase7_in_transaction(
                conn=conn,
                evidence_record=dict(EVIDENCE),
            )
            conn.commit()

            current = conn.execute(
                """
                SELECT phase_name, evidence_type, qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                """,
                (MODULE.PHASE7,),
            ).fetchone()
            history = conn.execute(
                """
                SELECT phase_name, evidence_type, qualified, evidence_json
                FROM phase_promotion_evidence_history
                WHERE phase_name = ?
                """,
                (MODULE.PHASE7,),
            ).fetchall()
        finally:
            conn.close()

        assert result["rows_changed"] == 2
        assert result["phase6_dependency_rechecked"] is True
        assert result["in_transaction_replay_check_passed"] is True
        assert current[0] == MODULE.PHASE7
        assert current[1] == MODULE.PHASE7_EVIDENCE_TYPE
        assert bool(current[2]) is True
        assert json.loads(current[3]) == EVIDENCE
        assert len(history) == 1
        assert json.loads(history[0][3]) == EVIDENCE


def test_transaction_rejects_phase7_replay():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _create_db(database, phase7_existing=True)

        conn = sqlite3.connect(database)
        try:
            conn.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="replay detected"):
                MODULE._persist_exact_phase7_in_transaction(
                    conn=conn,
                    evidence_record=dict(EVIDENCE),
                )
            conn.rollback()
        finally:
            conn.close()


def test_transaction_requires_phase6_dependency():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _create_db(database)
        conn = sqlite3.connect(database)
        try:
            conn.execute(
                "DELETE FROM phase_promotion_evidence WHERE phase_name = ?",
                (MODULE.PHASE6,),
            )
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="Phase 6 is no longer promoted"):
                MODULE._persist_exact_phase7_in_transaction(
                    conn=conn,
                    evidence_record=dict(EVIDENCE),
                )
            conn.rollback()
        finally:
            conn.close()


def test_history_is_immutable_after_insert():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _create_db(database)
        conn = sqlite3.connect(database)
        try:
            conn.execute("BEGIN IMMEDIATE")
            MODULE._persist_exact_phase7_in_transaction(
                conn=conn,
                evidence_record=dict(EVIDENCE),
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    UPDATE phase_promotion_evidence_history
                    SET qualified = 0
                    WHERE phase_name = ?
                    """,
                    (MODULE.PHASE7,),
                )
        finally:
            conn.close()


def test_valid_receipt_persists_promotion_but_authorizes_no_live_work():
    receipt = _receipt()
    MODULE.validate_persistence_receipt(receipt)

    assert receipt["phase7_promotion_persisted"] is True
    assert receipt["phase7_promotion_persistence_authorized"] is True
    assert receipt["live_submit_authorized"] is False
    assert receipt["transaction_submission_authorized"] is False
    assert receipt["new_live_capital_authorized"] is False


def test_resealed_receipt_cannot_authorize_live_submit():
    receipt = _receipt()
    receipt["live_submit_authorized"] = True
    _reseal(receipt)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_persistence_receipt(receipt)


def test_persistence_tool_has_no_transaction_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "BEGIN IMMEDIATE" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
