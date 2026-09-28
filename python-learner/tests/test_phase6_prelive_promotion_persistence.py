from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "persist_phase6_prelive_promotion.py"

SPEC = importlib.util.spec_from_file_location(
    "persist_phase6_prelive_promotion",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _schema(database: Path, *, include_triggers: bool = True) -> None:
    conn = sqlite3.connect(database)
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
        if include_triggers:
            conn.executescript(
                """
                CREATE TRIGGER phase_promotion_history_no_update
                BEFORE UPDATE ON phase_promotion_evidence_history
                BEGIN
                    SELECT RAISE(ABORT, 'phase_promotion_evidence_history is immutable');
                END;

                CREATE TRIGGER phase_promotion_history_no_delete
                BEFORE DELETE ON phase_promotion_evidence_history
                BEGIN
                    SELECT RAISE(ABORT, 'phase_promotion_evidence_history is immutable');
                END;
                """
            )
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence(
                phase_name, promoted_at, evidence_type, qualified, evidence_json
            ) VALUES (?, '2026-09-28T17:00:00Z', ?, 1, '{}')
            """,
            (MODULE.PHASE5, MODULE.PHASE5_EVIDENCE_TYPE),
        )
        conn.commit()
    finally:
        conn.close()


def _evidence() -> dict:
    return {
        "phase5_promoted": True,
        "execution_db": "/var/lib/pio/execution.db",
        "passed_enter_intents": 10,
        "distinct_pools": 2,
        "blocked_intents": 2,
        "postsimulation_intents": 0,
        "invalid_passed_intents": 0,
        "distinct_authorized_wallets": ["wallet-1"],
        "criteria": {
            "min_passed_enter_intents": 10,
            "min_distinct_pools": 2,
            "min_blocked_intents": 2,
            "max_postsimulation_intents": 0,
        },
        "promotion_ready": True,
        "reasons": [],
    }


def _status(production: Path) -> dict:
    evidence = _evidence()
    return {
        "phase6_evidence_status_sha256": "1" * 64,
        "phase6_report": evidence,
        "phase6_report_sha256": hashlib.sha256(
            MODULE._canonical_bytes(evidence)
        ).hexdigest(),
        "phase6_criteria_sha256": "2" * 64,
        "pio_database_sha256_after": "3" * 64,
        "execution_database_sha256_after": "4" * 64,
        "distinct_authorized_wallets": ["wallet-1"],
        "phase6_promotion_ready": True,
        "phase6_reasons": [],
        "phase5_promoted": True,
        "execution_database_path": "/var/lib/pio/execution.db",
    }


def _request(status: dict) -> dict:
    return {
        "request_sha256": "5" * 64,
        "phase6_report_sha256": status["phase6_report_sha256"],
        "phase6_criteria_sha256": status["phase6_criteria_sha256"],
        "pio_database_sha256": status["pio_database_sha256_after"],
        "execution_database_sha256": status["execution_database_sha256_after"],
        "distinct_authorized_wallets": ["wallet-1"],
    }


def _readiness(production: Path, status: dict, request: dict) -> dict:
    return {
        "readiness_sha256": "6" * 64,
        "promotion_readiness_ready": True,
        "requires_atomic_phase6_persistence": True,
        "production_repository": str(production),
        "pio_database_path": str(production / "data" / "pio.db"),
        "saved_phase6_evidence_status_sha256": status[
            "phase6_evidence_status_sha256"
        ],
        "promotion_request_sha256": request["request_sha256"],
        "approver_principal": "wyck@example.com",
        "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
    }


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _reseal(receipt: dict) -> None:
    identity = {field: receipt[field] for field in MODULE.RECEIPT_FIELDS}
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_transaction_inserts_exact_phase6_history_and_current_rows():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _schema(database)
        conn = sqlite3.connect(database, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = MODULE._persist_exact_phase6_in_transaction(
                conn=conn,
                evidence_record=_evidence(),
            )
            conn.commit()

            current = conn.execute(
                """
                SELECT evidence_type, qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                """,
                (MODULE.PHASE6,),
            ).fetchone()
            history = conn.execute(
                """
                SELECT COUNT(*)
                FROM phase_promotion_evidence_history
                WHERE phase_name = ?
                """,
                (MODULE.PHASE6,),
            ).fetchone()[0]
        finally:
            conn.close()

    assert result["rows_changed"] == 2
    assert result["history_inserted"] is True
    assert result["current_record_inserted"] is True
    assert result["history_record_verified"] is True
    assert result["current_record_verified"] is True
    assert current[0] == MODULE.PHASE6_EVIDENCE_TYPE
    assert bool(current[1]) is True
    assert json.loads(current[2]) == _evidence()
    assert history == 1


def test_transaction_rejects_phase6_replay():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _schema(database)

        conn = sqlite3.connect(database, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            MODULE._persist_exact_phase6_in_transaction(
                conn=conn,
                evidence_record=_evidence(),
            )
            conn.commit()

            conn.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="replay detected"):
                MODULE._persist_exact_phase6_in_transaction(
                    conn=conn,
                    evidence_record=_evidence(),
                )
            conn.rollback()
        finally:
            conn.close()


def test_transaction_rejects_missing_phase5_dependency():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _schema(database)
        conn = sqlite3.connect(database)
        try:
            conn.execute(
                "DELETE FROM phase_promotion_evidence WHERE phase_name = ?",
                (MODULE.PHASE5,),
            )
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(database, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="Phase 5 is no longer promoted"):
                MODULE._persist_exact_phase6_in_transaction(
                    conn=conn,
                    evidence_record=_evidence(),
                )
            conn.rollback()
        finally:
            conn.close()


def test_transaction_rejects_missing_immutable_history_triggers():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _schema(database, include_triggers=False)
        conn = sqlite3.connect(database, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="immutable-history triggers"):
                MODULE._persist_exact_phase6_in_transaction(
                    conn=conn,
                    evidence_record=_evidence(),
                )
            conn.rollback()
        finally:
            conn.close()


def test_evidence_record_requires_exact_signed_hash_bindings():
    with tempfile.TemporaryDirectory() as tmp:
        production = Path(tmp)
        status = _status(production)
        request = _request(status)

        record = MODULE._evidence_record(status=status, request=request)
        assert record == status["phase6_report"]

        request["phase6_report_sha256"] = "f" * 64
        with pytest.raises(ValueError, match="report digest binding mismatch"):
            MODULE._evidence_record(status=status, request=request)


def test_full_persistence_uses_fresh_locked_readiness_and_writes_only_phase6(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        database = production / "data" / "pio.db"
        _schema(database)

        status = _status(production)
        request = _request(status)
        readiness = _readiness(production, status, request)

        class FakeReadiness:
            @staticmethod
            def validate_phase6_promotion_readiness(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_phase6_promotion_readiness(**kwargs):
                return copy.deepcopy(readiness)

        class FakeStatus:
            @staticmethod
            def validate_phase6_evidence_status(value):
                assert isinstance(value, dict)

        class FakeRequest:
            @staticmethod
            def validate_phase6_promotion_request(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakeReadiness, FakeStatus, FakeRequest),
        )

        receipt = MODULE.persist_phase6_promotion(
            repository=production,
            source_tree=ROOT,
            phase5_post_promotion_audit_path=root / "phase5.json",
            saved_phase6_evidence_status_path=_write(
                root, "status.json", status
            ),
            promotion_request_path=_write(root, "request.json", request),
            saved_signed_authorization_verification_path=root / "verify.json",
            signed_payload_path=root / "payload.json",
            signature_path=root / "signature.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="a" * 64,
            execution_database_path="/var/lib/pio/execution.db",
            promotion_readiness_path=_write(
                root, "readiness.json", readiness
            ),
            expected_promotion_readiness_sha256=readiness[
                "readiness_sha256"
            ],
            lock_path=root / "phase6.lock",
        )

        conn = sqlite3.connect(database)
        try:
            phase6_current = conn.execute(
                """
                SELECT evidence_type, qualified
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                """,
                (MODULE.PHASE6,),
            ).fetchone()
            phase6_history = conn.execute(
                """
                SELECT COUNT(*)
                FROM phase_promotion_evidence_history
                WHERE phase_name = ?
                """,
                (MODULE.PHASE6,),
            ).fetchone()[0]
            phase5_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                """,
                (MODULE.PHASE5,),
            ).fetchone()[0]
        finally:
            conn.close()

    MODULE.validate_persistence_receipt(receipt)
    assert receipt["phase6_promotion_persisted"] is True
    assert receipt["phase6_promotion_persistence_authorized"] is True
    assert receipt["requires_post_promotion_audit"] is True
    assert receipt["controlled_live_authorized"] is False
    assert receipt["live_submit_authorized"] is False
    assert receipt["live_capital_authorized"] is False
    assert phase6_current == (MODULE.PHASE6_EVIDENCE_TYPE, 1)
    assert phase6_history == 1
    assert phase5_count == 1


def test_full_persistence_rolls_back_if_fresh_readiness_drifts(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        database = production / "data" / "pio.db"
        _schema(database)

        status = _status(production)
        request = _request(status)
        readiness = _readiness(production, status, request)
        fresh = copy.deepcopy(readiness)
        fresh["readiness_sha256"] = "7" * 64

        class FakeReadiness:
            @staticmethod
            def validate_phase6_promotion_readiness(value):
                pass

            @staticmethod
            def build_phase6_promotion_readiness(**kwargs):
                return copy.deepcopy(fresh)

        class FakeStatus:
            @staticmethod
            def validate_phase6_evidence_status(value):
                pass

        class FakeRequest:
            @staticmethod
            def validate_phase6_promotion_request(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakeReadiness, FakeStatus, FakeRequest),
        )

        with pytest.raises(ValueError, match="readiness drifted"):
            MODULE.persist_phase6_promotion(
                repository=production,
                source_tree=ROOT,
                phase5_post_promotion_audit_path=root / "phase5.json",
                saved_phase6_evidence_status_path=_write(
                    root, "status.json", status
                ),
                promotion_request_path=_write(root, "request.json", request),
                saved_signed_authorization_verification_path=root / "verify.json",
                signed_payload_path=root / "payload.json",
                signature_path=root / "signature.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="a" * 64,
                execution_database_path="/var/lib/pio/execution.db",
                promotion_readiness_path=_write(
                    root, "readiness.json", readiness
                ),
                expected_promotion_readiness_sha256=readiness[
                    "readiness_sha256"
                ],
                lock_path=root / "phase6.lock",
            )

        conn = sqlite3.connect(database)
        try:
            count = conn.execute(
                """
                SELECT COUNT(*)
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                """,
                (MODULE.PHASE6,),
            ).fetchone()[0]
        finally:
            conn.close()

    assert count == 0


def test_resealed_receipt_cannot_authorize_live_submit():
    receipt = {
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
        "phase6_evidence_status_sha256": "2" * 64,
        "promotion_request_sha256": "3" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "execution_database_path": "/var/lib/pio/execution.db",
        "approver_principal": "wyck@example.com",
        "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
        "phase_name": MODULE.PHASE6,
        "evidence_type": MODULE.PHASE6_EVIDENCE_TYPE,
        "promoted_at": "2026-09-28T18:30:00+00:00",
        "evidence_record": _evidence(),
        "evidence_sha256": hashlib.sha256(
            MODULE._canonical_bytes(_evidence())
        ).hexdigest(),
        "runtime_lock_used": True,
        "schema_contract_verified": True,
        "phase5_dependency_rechecked": True,
        "in_transaction_replay_check_passed": True,
        "history_id": 1,
        "history_inserted": True,
        "current_record_inserted": True,
        "rows_changed": 2,
        "history_record_verified": True,
        "current_record_verified": True,
        "database_transaction_committed": True,
        "phase6_promotion_persistence_authorized": True,
        "phase6_promotion_persisted": True,
        "requires_post_promotion_audit": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
    }
    identity = {field: receipt[field] for field in MODULE.RECEIPT_FIELDS}
    receipt["receipt_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    MODULE.validate_persistence_receipt(copy.deepcopy(receipt))

    receipt["live_submit_authorized"] = True
    _reseal(receipt)
    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_persistence_receipt(receipt)


def test_persistence_tool_has_no_controlled_live_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert "systemctl" not in source
    assert "meteora-executor live-submit" not in source
    assert "transaction-sign" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
