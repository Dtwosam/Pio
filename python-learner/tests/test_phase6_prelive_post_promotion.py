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
TOOL = ROOT / "deploy" / "tools" / "check_phase6_prelive_post_promotion.py"

SPEC = importlib.util.spec_from_file_location(
    "check_phase6_prelive_post_promotion",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


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


def _phase5_record() -> dict:
    return {
        "phase_name": MODULE.PHASE5,
        "promoted_at": "2026-09-28T17:00:00+00:00",
        "evidence_type": MODULE.PHASE5_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": {"promotion_ready": True},
    }


def _report() -> dict:
    evidence = _evidence()
    evidence_sha = hashlib.sha256(
        MODULE._canonical_bytes(evidence)
    ).hexdigest()
    current = {
        "phase_name": MODULE.PHASE6,
        "promoted_at": "2026-09-28T18:00:00+00:00",
        "evidence_type": MODULE.PHASE6_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": evidence,
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
        "fresh_phase6_evidence_status_sha256": "3" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "execution_database_path": "/var/lib/pio/execution.db",
        "phase_name": MODULE.PHASE6,
        "evidence_type": MODULE.PHASE6_EVIDENCE_TYPE,
        "promoted_at": current["promoted_at"],
        "history_id": 2,
        "evidence_sha256": evidence_sha,
        "current_record": current,
        "history_record": history,
        "phase5_current_record": _phase5_record(),
        "phase5_dependency_still_promoted": True,
        "current_record_matches_receipt": True,
        "history_record_matches_receipt": True,
        "phase6_history_count": 1,
        "exact_single_history_entry": True,
        "fresh_phase6_report_matches_receipt": True,
        "fresh_execution_database_matches_request": True,
        "pio_database_sha256_before": "4" * 64,
        "pio_database_sha256_after": "4" * 64,
        "pio_wal_sha256_before": None,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_before": None,
        "pio_shm_sha256_after": None,
        "execution_database_sha256_before": "5" * 64,
        "execution_database_sha256_after": "5" * 64,
        "execution_wal_sha256_before": None,
        "execution_wal_sha256_after": None,
        "execution_shm_sha256_before": None,
        "execution_shm_sha256_after": None,
        "promotion_snapshot_read_only": True,
        "source_databases_unchanged": True,
        "post_promotion_audit_ready": True,
        "phase6_promotion_confirmed": True,
        "phase6_promotion_persisted": True,
        "requires_separate_phase7_workflow": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified_by_audit": False,
        "production_execution_database_modified_by_audit": False,
    }
    return {
        **identity,
        "post_promotion_audit_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["post_promotion_audit_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _create_promotion_db(
    path: Path,
    *,
    evidence: dict | None = None,
    promoted_at: str = "2026-09-28T18:00:00+00:00",
    history_id: int = 2,
    extra_phase6_history: bool = False,
) -> None:
    record = evidence if evidence is not None else _evidence()
    evidence_json = json.dumps(record, separators=(",", ":"))
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
            ) VALUES (?, '2026-09-28T17:00:00+00:00', ?, 1, ?)
            """,
            (
                MODULE.PHASE5,
                MODULE.PHASE5_EVIDENCE_TYPE,
                json.dumps({"promotion_ready": True}),
            ),
        )
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence_history(
                id, phase_name, promoted_at, evidence_type, qualified, evidence_json
            ) VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                history_id,
                MODULE.PHASE6,
                promoted_at,
                MODULE.PHASE6_EVIDENCE_TYPE,
                evidence_json,
            ),
        )
        if extra_phase6_history:
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence_history(
                    phase_name, promoted_at, evidence_type, qualified, evidence_json
                ) VALUES (?, '2026-09-28T18:01:00+00:00', ?, 1, ?)
                """,
                (
                    MODULE.PHASE6,
                    MODULE.PHASE6_EVIDENCE_TYPE,
                    evidence_json,
                ),
            )
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence(
                phase_name, promoted_at, evidence_type, qualified, evidence_json
            ) VALUES (?, ?, ?, 1, ?)
            """,
            (
                MODULE.PHASE6,
                promoted_at,
                MODULE.PHASE6_EVIDENCE_TYPE,
                evidence_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _write(root: Path, name: str, value: dict) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_ready_audit_confirms_phase6_but_authorizes_no_live_work():
    report = _report()

    MODULE.validate_post_promotion_audit(copy.deepcopy(report))

    assert report["phase6_promotion_confirmed"] is True
    assert report["phase6_promotion_persisted"] is True
    assert report["requires_separate_phase7_workflow"] is True
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_audit_cannot_tamper_nested_persisted_evidence():
    report = _report()
    report["current_record"]["evidence"]["passed_enter_intents"] = 999
    report["history_record"]["evidence"]["passed_enter_intents"] = 999
    _reseal(report)

    with pytest.raises(ValueError, match="evidence digest mismatch"):
        MODULE.validate_post_promotion_audit(report)


def test_resealed_audit_cannot_hide_extra_history():
    report = _report()
    report["phase6_history_count"] = 2
    _reseal(report)

    with pytest.raises(ValueError, match="requires one history entry"):
        MODULE.validate_post_promotion_audit(report)


def test_resealed_audit_cannot_authorize_live_submit():
    report = _report()
    report["live_submit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_post_promotion_audit(report)


def test_real_promotion_snapshot_is_read_only_and_exact():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _create_promotion_db(database)
        before = database.read_bytes()

        snapshot = MODULE._promotion_snapshot(database)

        after = database.read_bytes()

    assert before == after
    assert snapshot["current_record"]["phase_name"] == MODULE.PHASE6
    assert snapshot["current_record"]["qualified"] is True
    assert len(snapshot["history"]) == 1
    assert snapshot["history"][0]["id"] == 2
    assert snapshot["phase5_current_record"]["qualified"] is True
    assert snapshot["state_before"] == snapshot["state_after"]


def test_real_promotion_snapshot_exposes_extra_phase6_history():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "pio.db"
        _create_promotion_db(database, extra_phase6_history=True)

        snapshot = MODULE._promotion_snapshot(database)

    assert len(snapshot["history"]) == 2


def test_builder_verifies_persisted_rows_and_fresh_phase6_evidence(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        pio = data / "pio.db"
        execution = root / "execution.db"
        execution.write_bytes(b"canonical-execution-db")
        evidence = _evidence()
        evidence["execution_db"] = str(execution)
        _create_promotion_db(pio, evidence=evidence)

        evidence_sha = hashlib.sha256(
            MODULE._canonical_bytes(evidence)
        ).hexdigest()
        execution_sha = hashlib.sha256(execution.read_bytes()).hexdigest()

        request = {
            "request_sha256": "2" * 64,
            "execution_database_path": str(execution),
            "execution_database_sha256": execution_sha,
            "phase6_report_sha256": evidence_sha,
            "phase6_criteria_sha256": "6" * 64,
        }
        receipt = {
            "receipt_sha256": "1" * 64,
            "promotion_request_sha256": request["request_sha256"],
            "production_repository": str(production),
            "pio_database_path": str(pio),
            "execution_database_path": str(execution),
            "phase6_promotion_persisted": True,
            "requires_post_promotion_audit": True,
            "promoted_at": "2026-09-28T18:00:00+00:00",
            "history_id": 2,
            "evidence_record": evidence,
            "evidence_sha256": evidence_sha,
        }
        fresh = {
            "phase6_evidence_status_sha256": "3" * 64,
            "phase6_promotion_ready": True,
            "phase6_reasons": [],
            "phase6_report": evidence,
            "phase6_report_sha256": evidence_sha,
            "phase6_criteria_sha256": request["phase6_criteria_sha256"],
            "execution_database_sha256_after": execution_sha,
        }

        class FakePersistence:
            @staticmethod
            def validate_persistence_receipt(value):
                assert isinstance(value, dict)

        class FakeStatus:
            @staticmethod
            def build_phase6_evidence_status(**kwargs):
                return copy.deepcopy(fresh)

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
            lambda source: (FakePersistence, FakeStatus, FakeRequest),
        )

        report = MODULE.build_post_promotion_audit(
            repository=production,
            source_tree=ROOT,
            phase5_post_promotion_audit_path=root / "phase5.json",
            promotion_request_path=_write(root, "request.json", request),
            persistence_receipt_path=_write(root, "receipt.json", receipt),
            execution_database_path=execution,
        )

    MODULE.validate_post_promotion_audit(report)
    assert report["current_record_matches_receipt"] is True
    assert report["history_record_matches_receipt"] is True
    assert report["fresh_phase6_report_matches_receipt"] is True
    assert report["fresh_execution_database_matches_request"] is True
    assert report["phase6_promotion_confirmed"] is True


def test_builder_rejects_extra_phase6_history(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        pio = data / "pio.db"
        execution = root / "execution.db"
        execution.write_bytes(b"canonical-execution-db")
        evidence = _evidence()
        evidence["execution_db"] = str(execution)
        _create_promotion_db(
            pio,
            evidence=evidence,
            extra_phase6_history=True,
        )
        evidence_sha = hashlib.sha256(
            MODULE._canonical_bytes(evidence)
        ).hexdigest()
        request = {
            "request_sha256": "2" * 64,
            "execution_database_path": str(execution),
            "execution_database_sha256": hashlib.sha256(
                execution.read_bytes()
            ).hexdigest(),
            "phase6_report_sha256": evidence_sha,
            "phase6_criteria_sha256": "6" * 64,
        }
        receipt = {
            "receipt_sha256": "1" * 64,
            "promotion_request_sha256": request["request_sha256"],
            "production_repository": str(production),
            "pio_database_path": str(pio),
            "execution_database_path": str(execution),
            "phase6_promotion_persisted": True,
            "requires_post_promotion_audit": True,
            "promoted_at": "2026-09-28T18:00:00+00:00",
            "history_id": 2,
            "evidence_record": evidence,
            "evidence_sha256": evidence_sha,
        }

        class FakePersistence:
            @staticmethod
            def validate_persistence_receipt(value):
                pass

        class FakeStatus:
            pass

        class FakeRequest:
            @staticmethod
            def validate_phase6_promotion_request(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakePersistence, FakeStatus, FakeRequest),
        )

        with pytest.raises(ValueError, match="history count differs"):
            MODULE.build_post_promotion_audit(
                repository=production,
                source_tree=ROOT,
                phase5_post_promotion_audit_path=root / "phase5.json",
                promotion_request_path=_write(root, "request.json", request),
                persistence_receipt_path=_write(root, "receipt.json", receipt),
                execution_database_path=execution,
            )


def test_builder_rejects_symlink_execution_database(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        data = production / "data"
        data.mkdir(parents=True)
        pio = data / "pio.db"
        target = root / "execution-real.db"
        link = root / "execution.db"
        target.write_bytes(b"execution")
        link.symlink_to(target)
        _create_promotion_db(pio)

        request = {
            "request_sha256": "2" * 64,
            "execution_database_path": str(link),
        }
        receipt = {
            "receipt_sha256": "1" * 64,
            "promotion_request_sha256": request["request_sha256"],
            "production_repository": str(production),
            "pio_database_path": str(pio),
            "execution_database_path": str(link),
            "phase6_promotion_persisted": True,
            "requires_post_promotion_audit": True,
        }

        class FakePersistence:
            @staticmethod
            def validate_persistence_receipt(value):
                pass

        class FakeStatus:
            pass

        class FakeRequest:
            @staticmethod
            def validate_phase6_promotion_request(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (FakePersistence, FakeStatus, FakeRequest),
        )

        with pytest.raises(ValueError, match="must not be a symlink"):
            MODULE.build_post_promotion_audit(
                repository=production,
                source_tree=ROOT,
                phase5_post_promotion_audit_path=root / "phase5.json",
                promotion_request_path=_write(root, "request.json", request),
                persistence_receipt_path=_write(root, "receipt.json", receipt),
                execution_database_path=link,
            )


def test_post_promotion_audit_has_no_mutation_or_phase7_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase6_promotion" not in source
    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert "systemctl" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase6_promotion_persisted": True' in source
    assert '"requires_separate_phase7_workflow": True' in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
