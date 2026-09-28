from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest

from meteora_learner.paper_account import create_paper_account
from meteora_learner.storage import Storage


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_manual_market_paper_phase5_evidence_status.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_phase5_evidence_status",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _result(*, ready: bool = False) -> dict:
    reasons = [] if ready else [
        "Phase 3 must be persistently promoted before Phase 5",
        "endurance: runtime below reviewed minimum",
    ]
    return {
        "account_id": "pio-proof-1",
        "phase3_promoted": ready,
        "endurance": {
            "passing": ready,
            "runtime_hours": 72.0 if ready else 0.1,
            "terminal_ticks": 500 if ready else 1,
        },
        "ledger_audit": {
            "account_id": "pio-proof-1",
            "passing": True,
            "position_failures": 0,
            "reasons": [],
        },
        "closed_positions": 3 if ready else 0,
        "distinct_valued_pools": 2 if ready else 0,
        "criteria": copy.deepcopy(MODULE.EXPECTED_PHASE5_CRITERIA),
        "promotion_ready": ready,
        "reasons": reasons,
    }


def _report(*, ready: bool = False) -> dict:
    result = _result(ready=ready)
    endurance = result["endurance"]
    ledger = result["ledger_audit"]
    criteria = result["criteria"]
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
        "post_cycle_audit_sha256": "1" * 64,
        "one_cycle_receipt_sha256": "2" * 64,
        "production_repository": "/opt/pio",
        "paper_database_path": "/opt/pio/data/pio.db",
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "database_sha256_before": "3" * 64,
        "database_sha256_after": "3" * 64,
        "wal_sha256_before": None,
        "wal_sha256_after": None,
        "database_snapshot_only": True,
        "source_database_unchanged": True,
        "source_wal_unchanged": True,
        "phase5_criteria": criteria,
        "phase5_criteria_sha256": hashlib.sha256(
            MODULE._canonical_bytes(criteria)
        ).hexdigest(),
        "phase3_promoted": result["phase3_promoted"],
        "endurance": endurance,
        "endurance_sha256": hashlib.sha256(
            MODULE._canonical_bytes(endurance)
        ).hexdigest(),
        "ledger_audit": ledger,
        "ledger_audit_sha256": hashlib.sha256(
            MODULE._canonical_bytes(ledger)
        ).hexdigest(),
        "closed_positions": result["closed_positions"],
        "distinct_valued_pools": result["distinct_valued_pools"],
        "phase5_promotion_ready": ready,
        "phase5_reasons": result["reasons"],
        "phase5_evidence_status_ready": True,
        "requires_additional_paper_evidence": not ready,
        "requires_separate_phase5_promotion_action": True,
        "requires_separate_timer_authorization": True,
        "phase5_promotion_persisted": False,
        "phase5_promotion_authorized": False,
        "recurring_paper_automation_authorized": False,
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_paper_database_modified": False,
    }
    return {
        **identity,
        "phase5_evidence_status_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["phase5_evidence_status_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def _audit(production: Path) -> dict:
    return {
        "post_cycle_audit_ready": True,
        "post_cycle_audit_sha256": "1" * 64,
        "one_cycle_receipt_sha256": "2" * 64,
        "production_repository": str(production),
        "paper_database_path": str(production / "data" / "pio.db"),
        "account": "pio-proof-1",
        "run_id": "manual-proof-20260928-1",
        "paper_timer_enable_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
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


def test_not_ready_status_reports_blockers_without_authorizing_anything():
    report = _report(ready=False)

    MODULE.validate_phase5_evidence_status(copy.deepcopy(report))

    assert report["phase5_evidence_status_ready"] is True
    assert report["phase5_promotion_ready"] is False
    assert report["requires_additional_paper_evidence"] is True
    assert report["phase5_reasons"]
    assert report["phase5_promotion_persisted"] is False
    assert report["phase5_promotion_authorized"] is False
    assert report["recurring_paper_automation_authorized"] is False
    assert report["paper_timer_enable_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_even_ready_phase5_evidence_does_not_persist_or_enable_timer():
    report = _report(ready=True)

    MODULE.validate_phase5_evidence_status(copy.deepcopy(report))

    assert report["phase5_promotion_ready"] is True
    assert report["requires_additional_paper_evidence"] is False
    assert report["phase5_promotion_persisted"] is False
    assert report["phase5_promotion_authorized"] is False
    assert report["recurring_paper_automation_authorized"] is False
    assert report["paper_timer_enable_authorized"] is False


def test_resealed_report_cannot_persist_promotion():
    report = _report(ready=True)
    report["phase5_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase5_promotion_persisted=false"):
        MODULE.validate_phase5_evidence_status(report)


def test_resealed_report_cannot_enable_recurring_automation():
    report = _report(ready=True)
    report["recurring_paper_automation_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="recurring_paper_automation_authorized=false",
    ):
        MODULE.validate_phase5_evidence_status(report)


def test_resealed_report_cannot_lower_phase5_criteria():
    report = _report(ready=False)
    report["phase5_criteria"]["min_runtime_hours"] = 1.0
    report["phase5_criteria_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(report["phase5_criteria"])
    ).hexdigest()
    _reseal(report)

    with pytest.raises(ValueError, match="criteria mismatch"):
        MODULE.validate_phase5_evidence_status(report)


def test_builder_uses_post_cycle_lineage_and_reports_current_blockers(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        database = production / "data" / "pio.db"
        database.write_bytes(b"paper-db")
        audit_path = _write(root, "post-cycle.json", _audit(production))

        class FakeAudit:
            @staticmethod
            def validate_post_cycle_audit(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_verify_reviewed_source",
            lambda source: FakeAudit,
        )
        monkeypatch.setattr(
            MODULE,
            "_evaluate_phase5",
            lambda **kwargs: _result(ready=False),
        )

        report = MODULE.build_phase5_evidence_status(
            repository=production,
            source_tree=ROOT,
            post_cycle_audit_path=audit_path,
        )

    MODULE.validate_phase5_evidence_status(report)
    assert report["post_cycle_audit_sha256"] == "1" * 64
    assert report["one_cycle_receipt_sha256"] == "2" * 64
    assert report["phase5_promotion_ready"] is False
    assert report["requires_additional_paper_evidence"] is True


def test_builder_fails_if_source_database_changes_during_evaluation(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        database = production / "data" / "pio.db"
        database.write_bytes(b"paper-db")
        audit_path = _write(root, "post-cycle.json", _audit(production))

        class FakeAudit:
            @staticmethod
            def validate_post_cycle_audit(value):
                assert isinstance(value, dict)

        def mutate_database(**kwargs):
            database.write_bytes(b"changed")
            return _result(ready=False)

        monkeypatch.setattr(
            MODULE,
            "_verify_reviewed_source",
            lambda source: FakeAudit,
        )
        monkeypatch.setattr(
            MODULE,
            "_evaluate_phase5",
            mutate_database,
        )

        with pytest.raises(
            ValueError,
            match="database changed during Phase 5 evaluation",
        ):
            MODULE.build_phase5_evidence_status(
                repository=production,
                source_tree=ROOT,
                post_cycle_audit_path=audit_path,
            )


def test_real_phase5_evaluator_runs_on_snapshot_without_changing_source_db():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        storage = Storage(database)
        create_paper_account(
            storage,
            account_id="pio-proof-1",
            starting_cash_quote=1000.0,
        )

        before_db = database.read_bytes()
        wal = Path(str(database) + "-wal")
        before_wal = wal.read_bytes() if wal.exists() else None

        result = MODULE._evaluate_phase5(
            source=ROOT,
            database=database,
            account="pio-proof-1",
        )

        after_db = database.read_bytes()
        after_wal = wal.read_bytes() if wal.exists() else None

    MODULE._validate_phase5_result(result, account="pio-proof-1")
    assert result["promotion_ready"] is False
    assert result["criteria"] == MODULE.EXPECTED_PHASE5_CRITERIA
    assert before_db == after_db
    assert before_wal == after_wal


def test_phase5_status_tool_has_no_promotion_or_activation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase5_promotion" not in source
    assert "--persist-ready" not in source
    assert "systemctl" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase5_promotion_persisted": False' in source
    assert '"recurring_paper_automation_authorized": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
