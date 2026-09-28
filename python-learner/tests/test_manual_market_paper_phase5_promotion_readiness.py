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
    / "check_manual_market_paper_phase5_promotion_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_phase5_promotion_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _report() -> dict:
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
        "saved_post_collection_audit_sha256": "1" * 64,
        "fresh_post_collection_audit_sha256": "2" * 64,
        "promotion_request_sha256": "3" * 64,
        "saved_signed_authorization_verification_sha256": "4" * 64,
        "fresh_signed_authorization_verification_sha256": "4" * 64,
        "saved_phase5_evidence_status_sha256": "5" * 64,
        "fresh_phase5_evidence_status_sha256": "6" * 64,
        "production_repository": "/opt/pio",
        "paper_database_path": "/opt/pio/data/pio.db",
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "approver_principal": "wyck@example.com",
        "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
        "material_post_collection_state_matches": True,
        "material_phase5_evidence_matches": True,
        "promotion_request_matches_evidence": True,
        "fresh_signature_matches_saved": True,
        "approval_not_expired": True,
        "phase5_current_record_present": False,
        "phase5_history_count": 0,
        "phase5_promotion_absent": True,
        "database_sha256_before": "7" * 64,
        "database_sha256_after": "7" * 64,
        "wal_sha256_before": None,
        "wal_sha256_after": None,
        "shm_sha256_before": None,
        "shm_sha256_after": None,
        "promotion_snapshot_read_only": True,
        "phase5_promotion_readiness_ready": True,
        "requires_separate_persistence_executor": True,
        "phase5_promotion_persisted": False,
        "paper_timer_enable_authorized": False,
        "paper_collection_start_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "new_market_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified_by_readiness": False,
    }
    return {
        **identity,
        "promotion_readiness_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["promotion_readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _minimal_promotion_schema(database: Path) -> None:
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
        conn.commit()
    finally:
        conn.close()


def _status(
    *,
    database: Path,
    digest: str,
    endurance_digest: str,
    checked_at: str,
) -> dict:
    return {
        "phase5_evidence_status_sha256": digest,
        "paper_database_path": str(database),
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "phase5_promotion_ready": True,
        "phase5_reasons": [],
        "requires_additional_paper_evidence": False,
        "phase3_promoted": True,
        "endurance_sha256": endurance_digest,
        "endurance": {
            "checked_at": checked_at,
            "runtime_hours": 72.0,
            "terminal_ticks": 500,
            "success_rate_pct": 100.0,
        },
        "ledger_audit": {
            "passing": True,
            "reasons": [],
        },
        "closed_positions": 3,
        "distinct_valued_pools": 2,
        "phase5_criteria": {
            "min_runtime_hours": 72.0,
        },
        "phase5_criteria_sha256": "a" * 64,
        "ledger_audit_sha256": "b" * 64,
    }


def _post(
    *,
    status: dict,
    audit_digest: str,
    checked_at: str,
) -> dict:
    return {
        "post_collection_audit_sha256": audit_digest,
        "audit_checked_at": checked_at,
        "post_collection_audit_ready": True,
        "phase5_promotion_ready": True,
        "phase5_reasons": [],
        "requires_new_collection_plan": False,
        "requires_additional_paper_evidence": False,
        "fresh_phase5_evidence_status_sha256": status[
            "phase5_evidence_status_sha256"
        ],
        "fresh_phase5_evidence_status": status,
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "service_inactive": True,
        "timer_inactive": True,
        "timer_disabled": True,
    }


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_artifact_still_authorizes_no_persistence_or_activation():
    report = _report()

    MODULE.validate_phase5_promotion_readiness(copy.deepcopy(report))

    assert report["phase5_promotion_readiness_ready"] is True
    assert report["requires_separate_persistence_executor"] is True
    assert report["phase5_promotion_persisted"] is False
    assert report["paper_timer_enable_authorized"] is False
    assert report["paper_collection_start_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_artifact_cannot_claim_promotion_persisted():
    report = _report()
    report["phase5_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase5_promotion_persisted=false"):
        MODULE.validate_phase5_promotion_readiness(report)


def test_resealed_artifact_cannot_enable_paper_timer():
    report = _report()
    report["paper_timer_enable_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_phase5_promotion_readiness(report)


def test_resealed_artifact_cannot_hide_existing_history():
    report = _report()
    report["phase5_history_count"] = 1
    _reseal(report)

    with pytest.raises(ValueError, match="empty history"):
        MODULE.validate_phase5_promotion_readiness(report)


def test_normalization_ignores_only_time_derived_phase5_fields():
    left = {
        "phase5_evidence_status_sha256": "1" * 64,
        "endurance_sha256": "2" * 64,
        "endurance": {
            "checked_at": "2026-09-28T12:00:00Z",
            "runtime_hours": 72.0,
            "terminal_ticks": 500,
        },
        "closed_positions": 3,
    }
    right = copy.deepcopy(left)
    right["phase5_evidence_status_sha256"] = "3" * 64
    right["endurance_sha256"] = "4" * 64
    right["endurance"]["checked_at"] = "2026-09-28T12:05:00Z"

    assert MODULE._normalized_phase5_status(left) == (
        MODULE._normalized_phase5_status(right)
    )

    right["endurance"]["terminal_ticks"] = 501
    assert MODULE._normalized_phase5_status(left) != (
        MODULE._normalized_phase5_status(right)
    )


def test_promotion_snapshot_is_read_only_when_no_phase5_record_exists():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _minimal_promotion_schema(database)
        before = database.read_bytes()

        snapshot = MODULE._promotion_snapshot(database)

        after = database.read_bytes()

    assert before == after
    assert snapshot["current_record"] is None
    assert snapshot["history_count"] == 0
    assert snapshot["database_sha256_before"] == snapshot["database_sha256_after"]
    assert snapshot["wal_sha256_before"] == snapshot["wal_sha256_after"]
    assert snapshot["shm_sha256_before"] == snapshot["shm_sha256_after"]


def test_promotion_snapshot_exposes_replay_state():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _minimal_promotion_schema(database)
        conn = sqlite3.connect(database)
        try:
            evidence = json.dumps({"promotion_ready": True})
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence(
                    phase_name, promoted_at, evidence_type,
                    qualified, evidence_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MODULE.PHASE_NAME,
                    "2026-09-28T15:00:00Z",
                    MODULE.EVIDENCE_TYPE,
                    1,
                    evidence,
                ),
            )
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence_history(
                    phase_name, promoted_at, evidence_type,
                    qualified, evidence_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MODULE.PHASE_NAME,
                    "2026-09-28T15:00:00Z",
                    MODULE.EVIDENCE_TYPE,
                    1,
                    evidence,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        snapshot = MODULE._promotion_snapshot(database)

    assert snapshot["current_record"] is not None
    assert snapshot["current_record"]["qualified"] is True
    assert snapshot["current_record"]["evidence_type"] == MODULE.EVIDENCE_TYPE
    assert snapshot["history_count"] == 1


def test_builder_accepts_only_time_drift_and_reverifies_signature(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"not-used-because-snapshot-is-mocked")

        saved_status = _status(
            database=database,
            digest="1" * 64,
            endurance_digest="2" * 64,
            checked_at="2026-09-28T14:00:00Z",
        )
        fresh_status = _status(
            database=database,
            digest="3" * 64,
            endurance_digest="4" * 64,
            checked_at="2026-09-28T14:05:00Z",
        )
        saved_post = _post(
            status=saved_status,
            audit_digest="5" * 64,
            checked_at="2026-09-28T14:00:01Z",
        )
        fresh_post = _post(
            status=fresh_status,
            audit_digest="6" * 64,
            checked_at="2026-09-28T14:05:01Z",
        )
        request = {
            "post_collection_audit_sha256": saved_post[
                "post_collection_audit_sha256"
            ],
            "phase5_evidence_status_sha256": saved_status[
                "phase5_evidence_status_sha256"
            ],
            "request_sha256": "7" * 64,
            "production_repository": str(production),
            "account": "pio-proof-1",
            "run_id": "phase5-collection-1",
            "phase5_criteria_sha256": saved_status["phase5_criteria_sha256"],
            "endurance_sha256": saved_status["endurance_sha256"],
            "ledger_audit_sha256": saved_status["ledger_audit_sha256"],
            "closed_positions": saved_status["closed_positions"],
            "distinct_valued_pools": saved_status["distinct_valued_pools"],
        }
        verification = {
            "request_sha256": request["request_sha256"],
            "verification_sha256": "8" * 64,
            "approver_principal": "wyck@example.com",
            "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
        }

        saved_post_path = _write(root, "saved-post.json", saved_post)
        request_path = _write(root, "request.json", request)
        verification_path = _write(root, "verification.json", verification)

        class FakePost:
            @staticmethod
            def validate_post_collection_audit(value):
                assert isinstance(value, dict)

            @staticmethod
            def build_post_collection_audit(**kwargs):
                return copy.deepcopy(fresh_post)

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                assert isinstance(value, dict)

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                assert isinstance(value, dict)

            @staticmethod
            def verify_authorization(**kwargs):
                return copy.deepcopy(verification)

        class FakeStatus:
            @staticmethod
            def validate_phase5_evidence_status(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakePost, FakeRequest, FakeSigner, FakeStatus),
        )
        monkeypatch.setattr(
            MODULE,
            "_promotion_snapshot",
            lambda db: {
                "current_record": None,
                "history_count": 0,
                "database_sha256_before": "9" * 64,
                "database_sha256_after": "9" * 64,
                "wal_sha256_before": None,
                "wal_sha256_after": None,
                "shm_sha256_before": None,
                "shm_sha256_after": None,
            },
        )

        report = MODULE.build_phase5_promotion_readiness(
            repository=production,
            source_tree=ROOT,
            post_cycle_audit_path=root / "post-cycle.json",
            pre_collection_phase5_evidence_status_path=root / "pre-status.json",
            activation_receipt_path=root / "activation.json",
            saved_post_collection_audit_path=saved_post_path,
            promotion_request_path=request_path,
            saved_signed_verification_path=verification_path,
            signed_payload_path=root / "payload.json",
            signature_path=root / "payload.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="a" * 64,
            now="2026-09-28T14:05:01Z",
        )

    MODULE.validate_phase5_promotion_readiness(report)
    assert report["material_post_collection_state_matches"] is True
    assert report["material_phase5_evidence_matches"] is True
    assert report["fresh_signature_matches_saved"] is True
    assert report["phase5_promotion_absent"] is True
    assert report["phase5_promotion_persisted"] is False


def test_builder_rejects_material_evidence_drift(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"x")

        saved_status = _status(
            database=database,
            digest="1" * 64,
            endurance_digest="2" * 64,
            checked_at="2026-09-28T14:00:00Z",
        )
        fresh_status = copy.deepcopy(saved_status)
        fresh_status["phase5_evidence_status_sha256"] = "3" * 64
        fresh_status["endurance_sha256"] = "4" * 64
        fresh_status["endurance"]["checked_at"] = "2026-09-28T14:05:00Z"
        fresh_status["endurance"]["terminal_ticks"] = 501

        saved_post = _post(
            status=saved_status,
            audit_digest="5" * 64,
            checked_at="2026-09-28T14:00:01Z",
        )
        fresh_post = _post(
            status=fresh_status,
            audit_digest="6" * 64,
            checked_at="2026-09-28T14:05:01Z",
        )
        request = {
            "post_collection_audit_sha256": "5" * 64,
            "phase5_evidence_status_sha256": "1" * 64,
            "request_sha256": "7" * 64,
            "production_repository": str(production),
            "account": "pio-proof-1",
            "run_id": "phase5-collection-1",
            "phase5_criteria_sha256": saved_status["phase5_criteria_sha256"],
            "endurance_sha256": saved_status["endurance_sha256"],
            "ledger_audit_sha256": saved_status["ledger_audit_sha256"],
            "closed_positions": saved_status["closed_positions"],
            "distinct_valued_pools": saved_status["distinct_valued_pools"],
        }
        verification = {
            "request_sha256": "7" * 64,
            "verification_sha256": "8" * 64,
        }

        class FakePost:
            @staticmethod
            def validate_post_collection_audit(value):
                pass

            @staticmethod
            def build_post_collection_audit(**kwargs):
                return copy.deepcopy(fresh_post)

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                pass

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                pass

        class FakeStatus:
            @staticmethod
            def validate_phase5_evidence_status(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakePost, FakeRequest, FakeSigner, FakeStatus),
        )

        with pytest.raises(ValueError, match="material state drifted"):
            MODULE.build_phase5_promotion_readiness(
                repository=production,
                source_tree=ROOT,
                post_cycle_audit_path=root / "post-cycle.json",
                pre_collection_phase5_evidence_status_path=root / "pre-status.json",
                activation_receipt_path=root / "activation.json",
                saved_post_collection_audit_path=_write(
                    root, "saved-post.json", saved_post
                ),
                promotion_request_path=_write(root, "request.json", request),
                saved_signed_verification_path=_write(
                    root, "verification.json", verification
                ),
                signed_payload_path=root / "payload.json",
                signature_path=root / "payload.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="a" * 64,
                now="2026-09-28T14:05:01Z",
            )


def test_builder_rejects_signed_request_account_mismatch(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"x")

        status = _status(
            database=database,
            digest="1" * 64,
            endurance_digest="2" * 64,
            checked_at="2026-09-28T14:00:00Z",
        )
        post = _post(
            status=status,
            audit_digest="5" * 64,
            checked_at="2026-09-28T14:00:01Z",
        )
        request = {
            "post_collection_audit_sha256": "5" * 64,
            "phase5_evidence_status_sha256": "1" * 64,
            "request_sha256": "7" * 64,
            "production_repository": str(production),
            "account": "different-account",
            "run_id": "phase5-collection-1",
            "phase5_criteria_sha256": status["phase5_criteria_sha256"],
            "endurance_sha256": status["endurance_sha256"],
            "ledger_audit_sha256": status["ledger_audit_sha256"],
            "closed_positions": status["closed_positions"],
            "distinct_valued_pools": status["distinct_valued_pools"],
        }
        verification = {
            "request_sha256": "7" * 64,
            "verification_sha256": "8" * 64,
        }

        class FakePost:
            @staticmethod
            def validate_post_collection_audit(value):
                pass

            @staticmethod
            def build_post_collection_audit(**kwargs):
                return copy.deepcopy(post)

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                pass

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                pass

        class FakeStatus:
            @staticmethod
            def validate_phase5_evidence_status(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakePost, FakeRequest, FakeSigner, FakeStatus),
        )

        with pytest.raises(ValueError, match="account binding mismatch"):
            MODULE.build_phase5_promotion_readiness(
                repository=production,
                source_tree=ROOT,
                post_cycle_audit_path=root / "post-cycle.json",
                pre_collection_phase5_evidence_status_path=root / "pre-status.json",
                activation_receipt_path=root / "activation.json",
                saved_post_collection_audit_path=_write(
                    root, "saved-post.json", post
                ),
                promotion_request_path=_write(root, "request.json", request),
                saved_signed_verification_path=_write(
                    root, "verification.json", verification
                ),
                signed_payload_path=root / "payload.json",
                signature_path=root / "payload.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="a" * 64,
                now="2026-09-28T14:00:01Z",
            )


def test_builder_rejects_existing_phase5_history(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"x")

        status = _status(
            database=database,
            digest="1" * 64,
            endurance_digest="2" * 64,
            checked_at="2026-09-28T14:00:00Z",
        )
        post = _post(
            status=status,
            audit_digest="5" * 64,
            checked_at="2026-09-28T14:00:01Z",
        )
        request = {
            "post_collection_audit_sha256": "5" * 64,
            "phase5_evidence_status_sha256": "1" * 64,
            "request_sha256": "7" * 64,
            "production_repository": str(production),
            "account": "pio-proof-1",
            "run_id": "phase5-collection-1",
            "phase5_criteria_sha256": status["phase5_criteria_sha256"],
            "endurance_sha256": status["endurance_sha256"],
            "ledger_audit_sha256": status["ledger_audit_sha256"],
            "closed_positions": status["closed_positions"],
            "distinct_valued_pools": status["distinct_valued_pools"],
        }
        verification = {
            "request_sha256": "7" * 64,
            "verification_sha256": "8" * 64,
            "approver_principal": "wyck@example.com",
            "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
        }

        class FakePost:
            @staticmethod
            def validate_post_collection_audit(value):
                pass

            @staticmethod
            def build_post_collection_audit(**kwargs):
                return copy.deepcopy(post)

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                pass

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                pass

            @staticmethod
            def verify_authorization(**kwargs):
                return copy.deepcopy(verification)

        class FakeStatus:
            @staticmethod
            def validate_phase5_evidence_status(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakePost, FakeRequest, FakeSigner, FakeStatus),
        )
        monkeypatch.setattr(
            MODULE,
            "_promotion_snapshot",
            lambda db: {
                "current_record": None,
                "history_count": 1,
                "database_sha256_before": "9" * 64,
                "database_sha256_after": "9" * 64,
                "wal_sha256_before": None,
                "wal_sha256_after": None,
                "shm_sha256_before": None,
                "shm_sha256_after": None,
            },
        )

        with pytest.raises(ValueError, match="history is already non-empty"):
            MODULE.build_phase5_promotion_readiness(
                repository=production,
                source_tree=ROOT,
                post_cycle_audit_path=root / "post-cycle.json",
                pre_collection_phase5_evidence_status_path=root / "pre-status.json",
                activation_receipt_path=root / "activation.json",
                saved_post_collection_audit_path=_write(
                    root, "saved-post.json", post
                ),
                promotion_request_path=_write(root, "request.json", request),
                saved_signed_verification_path=_write(
                    root, "verification.json", verification
                ),
                signed_payload_path=root / "payload.json",
                signature_path=root / "payload.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="a" * 64,
                now="2026-09-28T14:00:01Z",
            )


def test_readiness_tool_has_no_persistence_or_activation_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase5_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert "systemctl start" not in source
    assert "systemctl stop" not in source
    assert "systemctl enable" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase5_promotion_persisted": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
