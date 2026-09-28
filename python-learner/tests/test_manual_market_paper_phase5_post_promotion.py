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
    / "check_manual_market_paper_phase5_post_promotion.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_phase5_post_promotion",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


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


def _fresh_status() -> dict:
    evidence = _evidence()
    return {
        "phase5_evidence_status_sha256": "3" * 64,
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "phase3_promoted": True,
        "phase5_promotion_ready": True,
        "phase5_reasons": [],
        "phase5_criteria": evidence["criteria"],
        "endurance": evidence["endurance"],
        "ledger_audit": evidence["ledger_audit"],
        "closed_positions": evidence["closed_positions"],
        "distinct_valued_pools": evidence["distinct_valued_pools"],
    }


def _request() -> dict:
    status = _fresh_status()
    return {
        "request_sha256": "2" * 64,
        "phase5_criteria_sha256": hashlib.sha256(
            MODULE._canonical_bytes(status["phase5_criteria"])
        ).hexdigest(),
        "endurance_sha256": hashlib.sha256(
            MODULE._canonical_bytes(status["endurance"])
        ).hexdigest(),
        "ledger_audit_sha256": hashlib.sha256(
            MODULE._canonical_bytes(status["ledger_audit"])
        ).hexdigest(),
        "closed_positions": status["closed_positions"],
        "distinct_valued_pools": status["distinct_valued_pools"],
    }


def _receipt(production: Path) -> dict:
    evidence = _evidence()
    return {
        "receipt_sha256": "1" * 64,
        "promotion_request_sha256": "2" * 64,
        "production_repository": str(production),
        "paper_database_path": str(production / "data" / "pio.db"),
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "phase_name": MODULE.PHASE5,
        "evidence_type": MODULE.PHASE5_EVIDENCE_TYPE,
        "promoted_at": "2026-09-28T15:30:00+00:00",
        "history_id": 1,
        "evidence_record": evidence,
        "evidence_sha256": hashlib.sha256(
            MODULE._canonical_bytes(evidence)
        ).hexdigest(),
        "phase5_promotion_persisted": True,
        "requires_post_promotion_audit": True,
    }


def _unit(
    *,
    unit: str,
    active_state: str,
    unit_file_state: str,
    fragment_matches: bool = True,
    no_drop_ins: bool = True,
) -> dict:
    return {
        "unit": unit,
        "load_state": "loaded",
        "active_state": active_state,
        "unit_file_state": unit_file_state,
        "fragment_path": f"/etc/systemd/system/{unit}",
        "fragment_sha256": "4" * 64,
        "fragment_git_blob": "5" * 40,
        "drop_in_paths": "" if no_drop_ins else "/etc/systemd/system/drop.conf",
        "fragment_matches_reviewed": fragment_matches,
        "no_drop_ins": no_drop_ins,
    }


def _report() -> dict:
    evidence = _evidence()
    current = {
        "phase_name": MODULE.PHASE5,
        "promoted_at": "2026-09-28T15:30:00+00:00",
        "evidence_type": MODULE.PHASE5_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": evidence,
    }
    history = {
        "id": 1,
        **current,
    }
    phase3 = {
        "phase_name": MODULE.PHASE3,
        "promoted_at": "2026-09-27T12:00:00+00:00",
        "evidence_type": MODULE.PHASE3_EVIDENCE_TYPE,
        "qualified": True,
        "evidence": {},
    }
    service = _unit(
        unit="pio-paper@pio-proof-1.service",
        active_state="inactive",
        unit_file_state="static",
    )
    timer = _unit(
        unit="pio-paper@pio-proof-1.timer",
        active_state="inactive",
        unit_file_state="disabled",
    )
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
        "fresh_phase5_evidence_status_sha256": "3" * 64,
        "production_repository": "/opt/pio",
        "paper_database_path": "/opt/pio/data/pio.db",
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "phase_name": MODULE.PHASE5,
        "evidence_type": MODULE.PHASE5_EVIDENCE_TYPE,
        "promoted_at": current["promoted_at"],
        "history_id": 1,
        "evidence_sha256": hashlib.sha256(
            MODULE._canonical_bytes(evidence)
        ).hexdigest(),
        "current_record": current,
        "history_record": history,
        "phase3_current_record": phase3,
        "phase3_dependency_still_promoted": True,
        "current_record_matches_receipt": True,
        "history_record_matches_receipt": True,
        "phase5_history_count": 1,
        "exact_single_history_entry": True,
        "fresh_phase5_evidence_matches_receipt": True,
        "service_unit": service,
        "timer_unit": timer,
        "service_inactive": True,
        "timer_inactive": True,
        "timer_disabled": True,
        "units_match_reviewed": True,
        "no_unit_drop_ins": True,
        "database_sha256_before": "6" * 64,
        "database_sha256_after": "6" * 64,
        "wal_sha256_before": None,
        "wal_sha256_after": None,
        "shm_sha256_before": None,
        "shm_sha256_after": None,
        "promotion_snapshot_read_only": True,
        "post_promotion_audit_ready": True,
        "phase5_promotion_confirmed": True,
        "phase5_promotion_persisted": True,
        "requires_separate_phase6_workflow": True,
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
        "production_paper_database_modified_by_audit": False,
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


def _schema_with_promotion(
    database: Path,
    *,
    extra_history: bool = False,
) -> None:
    receipt = _receipt(database.parent.parent)
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
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence(
                phase_name, promoted_at, evidence_type, qualified, evidence_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                MODULE.PHASE3,
                "2026-09-27T12:00:00+00:00",
                MODULE.PHASE3_EVIDENCE_TYPE,
                1,
                "{}",
            ),
        )
        evidence_json = json.dumps(
            receipt["evidence_record"],
            separators=(",", ":"),
        )
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence_history(
                id, phase_name, promoted_at, evidence_type,
                qualified, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                receipt["history_id"],
                MODULE.PHASE5,
                receipt["promoted_at"],
                MODULE.PHASE5_EVIDENCE_TYPE,
                1,
                evidence_json,
            ),
        )
        if extra_history:
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence_history(
                    phase_name, promoted_at, evidence_type,
                    qualified, evidence_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    MODULE.PHASE5,
                    "2026-09-28T15:31:00+00:00",
                    MODULE.PHASE5_EVIDENCE_TYPE,
                    1,
                    evidence_json,
                ),
            )
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence(
                phase_name, promoted_at, evidence_type, qualified, evidence_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                MODULE.PHASE5,
                receipt["promoted_at"],
                MODULE.PHASE5_EVIDENCE_TYPE,
                1,
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
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_audit_confirms_only_phase5_persistence():
    report = _report()

    MODULE.validate_post_promotion_audit(copy.deepcopy(report))

    assert report["phase5_promotion_confirmed"] is True
    assert report["phase5_promotion_persisted"] is True
    assert report["requires_separate_phase6_workflow"] is True
    assert report["paper_timer_enable_authorized"] is False
    assert report["paper_collection_start_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_audit_cannot_tamper_nested_current_evidence():
    report = _report()
    report["current_record"]["evidence"]["closed_positions"] = 999
    report["history_record"]["evidence"]["closed_positions"] = 999
    _reseal(report)

    with pytest.raises(ValueError, match="evidence digest mismatch"):
        MODULE.validate_post_promotion_audit(report)


def test_resealed_audit_cannot_add_history_entry():
    report = _report()
    report["phase5_history_count"] = 2
    _reseal(report)

    with pytest.raises(ValueError, match="requires one history entry"):
        MODULE.validate_post_promotion_audit(report)


def test_resealed_audit_cannot_authorize_live_capital():
    report = _report()
    report["live_capital_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_capital_authorized=false"):
        MODULE.validate_post_promotion_audit(report)


def test_real_promotion_snapshot_is_read_only_and_exact():
    with tempfile.TemporaryDirectory() as tmp:
        production = Path(tmp) / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        _schema_with_promotion(database)
        before = database.read_bytes()

        snapshot = MODULE._promotion_snapshot(database)

        after = database.read_bytes()

    assert before == after
    assert snapshot["current_record"]["phase_name"] == MODULE.PHASE5
    assert snapshot["current_record"]["qualified"] is True
    assert len(snapshot["history"]) == 1
    assert snapshot["history"][0]["id"] == 1
    assert snapshot["phase3_current_record"]["qualified"] is True
    assert snapshot["database_sha256_before"] == snapshot["database_sha256_after"]


def test_builder_verifies_exact_persisted_rows_and_fresh_evidence(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        _schema_with_promotion(database)
        receipt = _receipt(production)
        request = _request()
        fresh = _fresh_status()

        class FakePersistence:
            @staticmethod
            def validate_persistence_receipt(value):
                assert isinstance(value, dict)

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                assert isinstance(value, dict)

        class FakeStatus:
            @staticmethod
            def build_phase5_evidence_status(**kwargs):
                return copy.deepcopy(fresh)

            @staticmethod
            def validate_phase5_evidence_status(value):
                assert isinstance(value, dict)

        class FakeRuntime:
            PAPER_SERVICE_UNIT = Path("deploy/systemd/pio-paper@.service")
            PAPER_TIMER_UNIT = Path("deploy/systemd/pio-paper@.timer")

            @staticmethod
            def _unit_snapshot(*, source, unit, reviewed_relative):
                if unit.endswith(".service"):
                    return _unit(
                        unit=unit,
                        active_state="inactive",
                        unit_file_state="static",
                    )
                return _unit(
                    unit=unit,
                    active_state="inactive",
                    unit_file_state="disabled",
                )

            @staticmethod
            def _validate_unit_snapshot(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (
                FakePersistence,
                FakeRequest,
                FakeStatus,
                FakeRuntime,
            ),
        )

        report = MODULE.build_post_promotion_audit(
            repository=production,
            source_tree=ROOT,
            post_cycle_audit_path=root / "post-cycle.json",
            promotion_request_path=_write(root, "request.json", request),
            persistence_receipt_path=_write(root, "receipt.json", receipt),
        )

    MODULE.validate_post_promotion_audit(report)
    assert report["current_record_matches_receipt"] is True
    assert report["history_record_matches_receipt"] is True
    assert report["exact_single_history_entry"] is True
    assert report["fresh_phase5_evidence_matches_receipt"] is True
    assert report["phase5_promotion_confirmed"] is True


def test_builder_rejects_extra_phase5_history(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        _schema_with_promotion(database, extra_history=True)

        class FakePersistence:
            @staticmethod
            def validate_persistence_receipt(value):
                pass

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                pass

        class FakeStatus:
            pass

        class FakeRuntime:
            pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (
                FakePersistence,
                FakeRequest,
                FakeStatus,
                FakeRuntime,
            ),
        )

        with pytest.raises(ValueError, match="history count differs"):
            MODULE.build_post_promotion_audit(
                repository=production,
                source_tree=ROOT,
                post_cycle_audit_path=root / "post-cycle.json",
                promotion_request_path=_write(root, "request.json", _request()),
                persistence_receipt_path=_write(
                    root, "receipt.json", _receipt(production)
                ),
            )


def test_builder_rejects_fresh_evidence_drift(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        _schema_with_promotion(database)
        fresh = _fresh_status()
        fresh["endurance"] = copy.deepcopy(fresh["endurance"])
        fresh["endurance"]["terminal_ticks"] = 501

        class FakePersistence:
            @staticmethod
            def validate_persistence_receipt(value):
                pass

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                pass

        class FakeStatus:
            @staticmethod
            def build_phase5_evidence_status(**kwargs):
                return copy.deepcopy(fresh)

            @staticmethod
            def validate_phase5_evidence_status(value):
                pass

        class FakeRuntime:
            pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (
                FakePersistence,
                FakeRequest,
                FakeStatus,
                FakeRuntime,
            ),
        )

        with pytest.raises(ValueError, match="endurance drifted"):
            MODULE.build_post_promotion_audit(
                repository=production,
                source_tree=ROOT,
                post_cycle_audit_path=root / "post-cycle.json",
                promotion_request_path=_write(root, "request.json", _request()),
                persistence_receipt_path=_write(
                    root, "receipt.json", _receipt(production)
                ),
            )


def test_builder_rejects_active_timer(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        database = production / "data" / "pio.db"
        database.parent.mkdir(parents=True)
        _schema_with_promotion(database)
        fresh = _fresh_status()

        class FakePersistence:
            @staticmethod
            def validate_persistence_receipt(value):
                pass

        class FakeRequest:
            @staticmethod
            def validate_phase5_promotion_request(value):
                pass

        class FakeStatus:
            @staticmethod
            def build_phase5_evidence_status(**kwargs):
                return copy.deepcopy(fresh)

            @staticmethod
            def validate_phase5_evidence_status(value):
                pass

        class FakeRuntime:
            PAPER_SERVICE_UNIT = Path("deploy/systemd/pio-paper@.service")
            PAPER_TIMER_UNIT = Path("deploy/systemd/pio-paper@.timer")

            @staticmethod
            def _unit_snapshot(*, source, unit, reviewed_relative):
                if unit.endswith(".service"):
                    return _unit(
                        unit=unit,
                        active_state="inactive",
                        unit_file_state="static",
                    )
                return _unit(
                    unit=unit,
                    active_state="active",
                    unit_file_state="disabled",
                )

            @staticmethod
            def _validate_unit_snapshot(value):
                pass

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed_modules",
            lambda source: (
                FakePersistence,
                FakeRequest,
                FakeStatus,
                FakeRuntime,
            ),
        )

        with pytest.raises(ValueError, match="PAPER timer is active"):
            MODULE.build_post_promotion_audit(
                repository=production,
                source_tree=ROOT,
                post_cycle_audit_path=root / "post-cycle.json",
                promotion_request_path=_write(root, "request.json", _request()),
                persistence_receipt_path=_write(
                    root, "receipt.json", _receipt(production)
                ),
            )


def test_post_promotion_audit_has_no_mutation_or_phase6_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "systemctl start" not in source
    assert "systemctl stop" not in source
    assert "systemctl enable" not in source
    assert "persist_phase6_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase5_promotion_persisted": True' in source
    assert '"requires_separate_phase6_workflow": True' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
