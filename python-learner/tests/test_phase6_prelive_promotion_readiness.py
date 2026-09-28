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
    / "check_phase6_prelive_promotion_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase6_prelive_promotion_readiness",
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
        "saved_phase6_evidence_status_sha256": "1" * 64,
        "fresh_phase6_evidence_status_sha256": "1" * 64,
        "promotion_request_sha256": "2" * 64,
        "saved_signed_authorization_verification_sha256": "3" * 64,
        "fresh_signed_authorization_verification_sha256": "3" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "execution_database_path": "/var/lib/pio/execution.db",
        "pio_database_sha256": "4" * 64,
        "execution_database_sha256": "5" * 64,
        "phase6_criteria_sha256": "6" * 64,
        "phase6_report_sha256": "7" * 64,
        "approval_payload_sha256": "8" * 64,
        "approval_signature_sha256": "9" * 64,
        "allowed_signers_sha256": "a" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
        "fresh_status_matches_saved": True,
        "fresh_authorization_matches_saved": True,
        "human_phase6_promotion_authorization_verified": True,
        "approval_not_expired": True,
        "phase5_dependency_current": True,
        "phase6_current_record_absent": True,
        "phase6_history_count": 0,
        "phase6_history_empty": True,
        "anti_replay_ready": True,
        "passed_enter_intents": 10,
        "distinct_pools": 2,
        "blocked_intents": 2,
        "distinct_authorized_wallets": ["wallet-1"],
        "phase6_promotion_ready": True,
        "phase6_reasons": [],
        "promotion_readiness_ready": True,
        "requires_atomic_phase6_persistence": True,
        "phase6_promotion_persisted": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "production_execution_database_modified": False,
    }
    return {
        **identity,
        "readiness_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _promotion_db(
    path: Path,
    *,
    phase6_current: bool = False,
    phase6_history: bool = False,
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
            ) VALUES (?, '2026-09-28T17:00:00Z', ?, 1, '{}')
            """,
            (MODULE.PHASE5, MODULE.PHASE5_EVIDENCE_TYPE),
        )
        if phase6_current:
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence(
                    phase_name, promoted_at, evidence_type, qualified, evidence_json
                ) VALUES (?, '2026-09-28T18:00:00Z', ?, 1, '{}')
                """,
                (MODULE.PHASE6, MODULE.PHASE6_EVIDENCE_TYPE),
            )
        if phase6_history:
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence_history(
                    phase_name, promoted_at, evidence_type, qualified, evidence_json
                ) VALUES (?, '2026-09-28T18:00:00Z', ?, 1, '{}')
                """,
                (MODULE.PHASE6, MODULE.PHASE6_EVIDENCE_TYPE),
            )
        conn.commit()
    finally:
        conn.close()


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _builder_inputs(production: Path) -> tuple[dict, dict, dict]:
    pio = production / "data" / "pio.db"
    status = {
        "phase6_evidence_status_sha256": "1" * 64,
        "production_repository": str(production),
        "pio_database_path": str(pio),
        "execution_database_path": str(production / "execution.db"),
        "pio_database_sha256_after": "4" * 64,
        "execution_database_sha256_after": "5" * 64,
        "phase6_criteria_sha256": "6" * 64,
        "phase6_report_sha256": "7" * 64,
        "passed_enter_intents": 10,
        "distinct_pools": 2,
        "blocked_intents": 2,
        "distinct_authorized_wallets": ["wallet-1"],
        "phase6_promotion_ready": True,
        "phase6_reasons": [],
    }
    request = {
        "request_sha256": "2" * 64,
        "phase6_evidence_status_sha256": "1" * 64,
        "production_repository": str(production),
        "execution_database_path": str(production / "execution.db"),
    }
    verification = {
        "verification_sha256": "3" * 64,
        "request_sha256": "2" * 64,
        "human_phase6_promotion_authorization_verified": True,
        "approval_payload_sha256": "8" * 64,
        "approval_signature_sha256": "9" * 64,
        "allowed_signers_sha256": "a" * 64,
        "approver_principal": "wyck@example.com",
        "approval_id": "fedcba98-7654-4cba-8123-456789abcdef",
    }
    return status, request, verification


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_ready_report_remains_non_persisting_and_non_live():
    report = _report()

    MODULE.validate_phase6_promotion_readiness(copy.deepcopy(report))

    assert report["promotion_readiness_ready"] is True
    assert report["anti_replay_ready"] is True
    assert report["phase6_promotion_persisted"] is False
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_report_cannot_claim_phase6_persisted():
    report = _report()
    report["phase6_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase6_promotion_persisted=false"):
        MODULE.validate_phase6_promotion_readiness(report)


def test_resealed_report_cannot_authorize_live_submit():
    report = _report()
    report["live_submit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase6_promotion_readiness(report)


def test_resealed_report_cannot_hide_history_replay():
    report = _report()
    report["phase6_history_count"] = 1
    report["phase6_history_empty"] = False
    report["anti_replay_ready"] = False
    _reseal(report)

    with pytest.raises(ValueError):
        MODULE.validate_phase6_promotion_readiness(report)


def test_real_promotion_snapshot_requires_phase5_and_empty_phase6():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _promotion_db(database)
        before = database.read_bytes()

        snapshot = MODULE._promotion_snapshot(database)

        after = database.read_bytes()

    assert before == after
    assert snapshot["phase5_dependency_current"] is True
    assert snapshot["phase6_current_record_absent"] is True
    assert snapshot["phase6_history_count"] == 0
    assert snapshot["phase6_history_empty"] is True


def test_real_promotion_snapshot_detects_existing_phase6_current():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _promotion_db(database, phase6_current=True)

        snapshot = MODULE._promotion_snapshot(database)

    assert snapshot["phase6_current_record_absent"] is False


def test_real_promotion_snapshot_detects_phase6_history_replay():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _promotion_db(database, phase6_history=True)

        snapshot = MODULE._promotion_snapshot(database)

    assert snapshot["phase6_history_count"] == 1
    assert snapshot["phase6_history_empty"] is False


def test_builder_requires_exact_fresh_status_and_authorization(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        _promotion_db(production / "data" / "pio.db")
        (production / "execution.db").write_bytes(b"unused")

        status, request, verification = _builder_inputs(production)

        class FakeStatus:
            @staticmethod
            def validate_phase6_evidence_status(value):
                pass

            @staticmethod
            def build_phase6_evidence_status(**kwargs):
                return copy.deepcopy(status)

        class FakeRequest:
            @staticmethod
            def validate_phase6_promotion_request(value):
                pass

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                pass

            @staticmethod
            def verify_authorization(**kwargs):
                return copy.deepcopy(verification)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakeStatus, FakeRequest, FakeSigner),
        )

        report = MODULE.build_phase6_promotion_readiness(
            repository=production,
            source_tree=ROOT,
            phase5_post_promotion_audit_path=root / "phase5.json",
            saved_phase6_evidence_status_path=_write(
                root, "status.json", status
            ),
            promotion_request_path=_write(root, "request.json", request),
            saved_signed_authorization_verification_path=_write(
                root, "verification.json", verification
            ),
            signed_payload_path=root / "payload.json",
            signature_path=root / "signature.sig",
            allowed_signers_path=root / "allowed_signers",
            expected_allowed_signers_sha256="a" * 64,
            execution_database_path=production / "execution.db",
            now="2026-09-28T18:02:00Z",
        )

    MODULE.validate_phase6_promotion_readiness(report)
    assert report["fresh_status_matches_saved"] is True
    assert report["fresh_authorization_matches_saved"] is True
    assert report["anti_replay_ready"] is True
    assert report["phase6_promotion_persisted"] is False


def test_builder_rejects_fresh_status_drift(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        _promotion_db(production / "data" / "pio.db")
        (production / "execution.db").write_bytes(b"unused")

        status, request, verification = _builder_inputs(production)
        fresh = copy.deepcopy(status)
        fresh["passed_enter_intents"] = 11

        class FakeStatus:
            @staticmethod
            def validate_phase6_evidence_status(value):
                pass

            @staticmethod
            def build_phase6_evidence_status(**kwargs):
                return copy.deepcopy(fresh)

        class FakeRequest:
            @staticmethod
            def validate_phase6_promotion_request(value):
                pass

        class FakeSigner:
            @staticmethod
            def validate_verification(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakeStatus, FakeRequest, FakeSigner),
        )

        with pytest.raises(ValueError, match="differs from saved status"):
            MODULE.build_phase6_promotion_readiness(
                repository=production,
                source_tree=ROOT,
                phase5_post_promotion_audit_path=root / "phase5.json",
                saved_phase6_evidence_status_path=_write(
                    root, "status.json", status
                ),
                promotion_request_path=_write(root, "request.json", request),
                saved_signed_authorization_verification_path=_write(
                    root, "verification.json", verification
                ),
                signed_payload_path=root / "payload.json",
                signature_path=root / "signature.sig",
                allowed_signers_path=root / "allowed_signers",
                expected_allowed_signers_sha256="a" * 64,
                execution_database_path=production / "execution.db",
            )


def test_readiness_tool_has_no_promotion_or_live_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase6_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert "systemctl" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase6_promotion_persisted": False' in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
