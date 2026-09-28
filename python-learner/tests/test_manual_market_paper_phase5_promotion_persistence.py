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
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "persist_manual_market_paper_phase5_promotion.py"
)

SPEC = importlib.util.spec_from_file_location(
    "persist_manual_market_paper_phase5_promotion",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _schema(database: Path, *, include_phase3: bool = True) -> None:
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

            CREATE INDEX idx_phase_promotion_history_phase_id
            ON phase_promotion_evidence_history(phase_name, id);

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
        if include_phase3:
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence(
                    phase_name, promoted_at, evidence_type,
                    qualified, evidence_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MODULE.PHASE3,
                    "2026-09-28T10:00:00+00:00",
                    MODULE.PHASE3_EVIDENCE_TYPE,
                    1,
                    "{}",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _evidence() -> dict:
    return {
        "account_id": "pio-proof-1",
        "phase3_promoted": True,
        "endurance": {
            "passing": True,
            "runtime_hours": 72.0,
            "terminal_ticks": 500,
        },
        "ledger_audit": {
            "passing": True,
            "reasons": [],
        },
        "closed_positions": 3,
        "distinct_valued_pools": 2,
        "criteria": {
            "min_runtime_hours": 72.0,
            "min_terminal_ticks": 500,
        },
        "promotion_ready": True,
        "reasons": [],
    }


def _status() -> dict:
    criteria = {
        "min_runtime_hours": 72.0,
        "min_terminal_ticks": 500,
    }
    endurance = {
        "passing": True,
        "runtime_hours": 72.0,
        "terminal_ticks": 500,
    }
    ledger = {
        "passing": True,
        "reasons": [],
    }
    return {
        "phase5_evidence_status_sha256": "4" * 64,
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "phase3_promoted": True,
        "phase5_promotion_ready": True,
        "phase5_reasons": [],
        "phase5_criteria": criteria,
        "phase5_criteria_sha256": hashlib.sha256(
            MODULE._canonical_bytes(criteria)
        ).hexdigest(),
        "endurance": endurance,
        "endurance_sha256": hashlib.sha256(
            MODULE._canonical_bytes(endurance)
        ).hexdigest(),
        "ledger_audit": ledger,
        "ledger_audit_sha256": hashlib.sha256(
            MODULE._canonical_bytes(ledger)
        ).hexdigest(),
        "closed_positions": 3,
        "distinct_valued_pools": 2,
    }


def _request(status: dict) -> dict:
    return {
        "request_sha256": "2" * 64,
        "phase5_evidence_status_sha256": status[
            "phase5_evidence_status_sha256"
        ],
        "account": status["account"],
        "run_id": status["run_id"],
        "phase5_criteria_sha256": status["phase5_criteria_sha256"],
        "endurance_sha256": status["endurance_sha256"],
        "ledger_audit_sha256": status["ledger_audit_sha256"],
        "closed_positions": status["closed_positions"],
        "distinct_valued_pools": status["distinct_valued_pools"],
    }


def _post(status: dict) -> dict:
    return {
        "post_collection_audit_sha256": "3" * 64,
        "fresh_phase5_evidence_status": status,
    }


def _readiness(production: Path) -> dict:
    return {
        "promotion_readiness_sha256": "1" * 64,
        "fresh_post_collection_audit_sha256": "5" * 64,
        "fresh_phase5_evidence_status_sha256": "6" * 64,
        "phase5_promotion_readiness_ready": True,
        "requires_separate_persistence_executor": True,
        "production_repository": str(production),
        "paper_database_path": str(production / "data" / "pio.db"),
        "promotion_request_sha256": "2" * 64,
        "saved_post_collection_audit_sha256": "3" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
    }


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _receipt() -> dict:
    evidence = _evidence()
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
        "fresh_promotion_readiness_sha256": "2" * 64,
        "promotion_request_sha256": "3" * 64,
        "post_collection_audit_sha256": "4" * 64,
        "phase5_evidence_status_sha256": "5" * 64,
        "production_repository": "/opt/pio",
        "paper_database_path": "/opt/pio/data/pio.db",
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "approver_principal": "wyck@example.com",
        "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
        "phase_name": MODULE.PHASE5,
        "evidence_type": MODULE.PHASE5_EVIDENCE_TYPE,
        "promoted_at": "2026-09-28T15:30:00+00:00",
        "evidence_record": evidence,
        "evidence_sha256": hashlib.sha256(
            MODULE._canonical_bytes(evidence)
        ).hexdigest(),
        "material_readiness_matches": True,
        "runtime_lock_used": True,
        "schema_contract_verified": True,
        "phase3_dependency_rechecked": True,
        "in_transaction_replay_check_passed": True,
        "history_id": 1,
        "history_inserted": True,
        "current_record_inserted": True,
        "rows_changed": 2,
        "history_record_verified": True,
        "current_record_verified": True,
        "database_transaction_committed": True,
        "phase5_promotion_persistence_authorized": True,
        "phase5_promotion_persisted": True,
        "requires_post_promotion_audit": True,
        "paper_timer_enable_authorized": False,
        "paper_collection_start_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "new_market_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified_by_executor": True,
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


def _phase5_counts(database: Path) -> tuple[int, int]:
    conn = sqlite3.connect(database)
    try:
        current = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                """,
                (MODULE.PHASE5,),
            ).fetchone()[0]
        )
        history = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM phase_promotion_evidence_history
                WHERE phase_name = ?
                """,
                (MODULE.PHASE5,),
            ).fetchone()[0]
        )
        return current, history
    finally:
        conn.close()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_exact_persistence_inserts_one_current_and_one_history_row():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _schema(database)

        result = MODULE._persist_exact_phase5(
            database=database,
            evidence_record=_evidence(),
        )

        conn = sqlite3.connect(database)
        try:
            current = conn.execute(
                """
                SELECT phase_name, evidence_type, qualified, evidence_json
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                """,
                (MODULE.PHASE5,),
            ).fetchone()
            history = conn.execute(
                """
                SELECT phase_name, evidence_type, qualified, evidence_json
                FROM phase_promotion_evidence_history
                WHERE id = ?
                """,
                (result["history_id"],),
            ).fetchone()
        finally:
            conn.close()

    assert result["rows_changed"] == 2
    assert result["database_transaction_committed"] is True
    assert current is not None
    assert history is not None
    assert current[:3] == (
        MODULE.PHASE5,
        MODULE.PHASE5_EVIDENCE_TYPE,
        1,
    )
    assert history[:3] == current[:3]
    assert json.loads(current[3]) == _evidence()
    assert json.loads(history[3]) == _evidence()


def test_second_persistence_attempt_is_rejected_without_new_history():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _schema(database)

        MODULE._persist_exact_phase5(
            database=database,
            evidence_record=_evidence(),
        )
        before = _phase5_counts(database)

        with pytest.raises(ValueError, match="replay detected"):
            MODULE._persist_exact_phase5(
                database=database,
                evidence_record=_evidence(),
            )

        after = _phase5_counts(database)

    assert before == (1, 1)
    assert after == before


def test_missing_phase3_dependency_rolls_back_before_phase5_write():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _schema(database, include_phase3=False)

        with pytest.raises(ValueError, match="Phase 3 is no longer"):
            MODULE._persist_exact_phase5(
                database=database,
                evidence_record=_evidence(),
            )

        counts = _phase5_counts(database)

    assert counts == (0, 0)


def test_second_insert_failure_rolls_back_history_insert_atomically():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _schema(database)
        conn = sqlite3.connect(database)
        try:
            conn.executescript(
                """
                CREATE TRIGGER fail_phase5_current_insert
                BEFORE INSERT ON phase_promotion_evidence
                WHEN NEW.phase_name = 'PHASE5'
                BEGIN
                    SELECT RAISE(ABORT, 'forced current insert failure');
                END;
                """
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(sqlite3.IntegrityError):
            MODULE._persist_exact_phase5(
                database=database,
                evidence_record=_evidence(),
            )

        counts = _phase5_counts(database)

    assert counts == (0, 0)


def test_drifted_immutable_history_trigger_is_rejected_before_write():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _schema(database)
        conn = sqlite3.connect(database)
        try:
            conn.executescript(
                """
                DROP TRIGGER phase_promotion_history_no_update;
                CREATE TRIGGER phase_promotion_history_no_update
                BEFORE UPDATE ON phase_promotion_evidence_history
                BEGIN
                    SELECT 1;
                END;
                """
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="history-update trigger drifted"):
            MODULE._persist_exact_phase5(
                database=database,
                evidence_record=_evidence(),
            )

        counts = _phase5_counts(database)

    assert counts == (0, 0)


def test_evidence_record_binds_exact_signed_components():
    status = _status()
    request = _request(status)
    record = MODULE._evidence_record(
        post_collection=_post(status),
        request=request,
    )

    assert record == _evidence()


def test_evidence_record_rejects_component_digest_drift():
    status = _status()
    request = _request(status)
    request["endurance_sha256"] = "f" * 64

    with pytest.raises(ValueError, match="endurance does not match"):
        MODULE._evidence_record(
            post_collection=_post(status),
            request=request,
        )


def test_receipt_is_narrow_even_after_success():
    receipt = _receipt()

    MODULE.validate_persistence_receipt(copy.deepcopy(receipt))

    assert receipt["phase5_promotion_persistence_authorized"] is True
    assert receipt["phase5_promotion_persisted"] is True
    assert receipt["requires_post_promotion_audit"] is True
    assert receipt["paper_timer_enable_authorized"] is False
    assert receipt["paper_collection_start_authorized"] is False
    assert receipt["transaction_signing_authorized"] is False
    assert receipt["transaction_submission_authorized"] is False
    assert receipt["live_capital_authorized"] is False


def test_resealed_receipt_cannot_expand_to_live_capital():
    receipt = _receipt()
    receipt["live_capital_authorized"] = True
    _reseal(receipt)

    with pytest.raises(ValueError, match="live_capital_authorized=false"):
        MODULE.validate_persistence_receipt(receipt)


def test_resealed_receipt_cannot_hide_extra_row_changes():
    receipt = _receipt()
    receipt["rows_changed"] = 3
    _reseal(receipt)

    with pytest.raises(ValueError, match="exactly two logical rows"):
        MODULE.validate_persistence_receipt(receipt)


def test_full_executor_fresh_rechecks_then_commits_exact_promotion(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        _schema(database)

        status = _status()
        request = _request(status)
        post = _post(status)
        saved = _readiness(production)
        fresh = copy.deepcopy(saved)
        fresh["promotion_readiness_sha256"] = "9" * 64
        fresh["fresh_post_collection_audit_sha256"] = "a" * 64
        fresh["fresh_phase5_evidence_status_sha256"] = "b" * 64

        class FakeReadiness:
            @staticmethod
            def validate_phase5_promotion_readiness(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_phase5_promotion_readiness(**kwargs):
                return copy.deepcopy(fresh)

        class FakePost:
            @staticmethod
            def validate_post_collection_audit(value):
                assert isinstance(value, dict)

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakeReadiness, FakePost, FakeRequest),
        )

        receipt = MODULE.persist_phase5_promotion(
            repository=production,
            source_tree=ROOT,
            post_cycle_audit_path=root / "post-cycle.json",
            pre_collection_phase5_evidence_status_path=root / "pre-status.json",
            activation_receipt_path=root / "activation.json",
            saved_post_collection_audit_path=_write(
                root, "post-collection.json", post
            ),
            promotion_request_path=_write(root, "request.json", request),
            saved_signed_verification_path=root / "verification.json",
            signed_payload_path=root / "payload.json",
            signature_path=root / "payload.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="c" * 64,
            promotion_readiness_path=_write(root, "readiness.json", saved),
            expected_promotion_readiness_sha256="1" * 64,
            lock_path=root / "promotion.lock",
            now="2026-09-28T15:30:00Z",
        )

        counts = _phase5_counts(database)

    MODULE.validate_persistence_receipt(receipt)
    assert counts == (1, 1)
    assert receipt["material_readiness_matches"] is True
    assert receipt["rows_changed"] == 2
    assert receipt["phase5_promotion_persisted"] is True


def test_full_executor_rejects_fresh_readiness_drift_before_write(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        _schema(database)

        status = _status()
        request = _request(status)
        post = _post(status)
        saved = _readiness(production)
        fresh = copy.deepcopy(saved)
        fresh["account"] = "drifted-account"

        class FakeReadiness:
            @staticmethod
            def validate_phase5_promotion_readiness(value):
                pass

            @staticmethod
            def build_phase5_promotion_readiness(**kwargs):
                return copy.deepcopy(fresh)

        class FakePost:
            @staticmethod
            def validate_post_collection_audit(value):
                pass

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakeReadiness, FakePost, FakeRequest),
        )

        with pytest.raises(ValueError, match="materially drifted"):
            MODULE.persist_phase5_promotion(
                repository=production,
                source_tree=ROOT,
                post_cycle_audit_path=root / "post-cycle.json",
                pre_collection_phase5_evidence_status_path=root / "pre-status.json",
                activation_receipt_path=root / "activation.json",
                saved_post_collection_audit_path=_write(
                    root, "post-collection.json", post
                ),
                promotion_request_path=_write(root, "request.json", request),
                saved_signed_verification_path=root / "verification.json",
                signed_payload_path=root / "payload.json",
                signature_path=root / "payload.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="c" * 64,
                promotion_readiness_path=_write(root, "readiness.json", saved),
                expected_promotion_readiness_sha256="1" * 64,
                lock_path=root / "promotion.lock",
                now="2026-09-28T15:30:00Z",
            )

        counts = _phase5_counts(database)

    assert counts == (0, 0)


def test_persistence_tool_has_no_systemd_git_or_storage_initializer():
    source = TOOL.read_text(encoding="utf-8")

    assert "systemctl" not in source
    assert "Storage(" not in source
    assert "from meteora_learner.phase_promotion import persist_phase5_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert "shell=True" not in source
    assert '"phase5_promotion_persisted": True' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
