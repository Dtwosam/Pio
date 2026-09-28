from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest

from meteora_learner.storage import Storage


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_evidence_status.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_evidence_status",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _phase7_result(*, ready: bool = False) -> dict:
    if ready:
        return {
            "phase6_promoted": True,
            "ledger_audit": {
                "clean": True,
                "reasons": [],
            },
            "confirmed_receipts": 6,
            "failed_receipts": 0,
            "closed_positions": 3,
            "open_positions": 0,
            "distinct_closed_pools": 2,
            "valued_closed_positions": 3,
            "labeled_closed_positions": 3,
            "criteria": copy.deepcopy(MODULE.EXPECTED_CRITERIA),
            "promotion_ready": True,
            "reasons": [],
        }
    return {
        "phase6_promoted": True,
        "ledger_audit": {
            "clean": True,
            "reasons": [],
        },
        "confirmed_receipts": 0,
        "failed_receipts": 0,
        "closed_positions": 0,
        "open_positions": 0,
        "distinct_closed_pools": 0,
        "valued_closed_positions": 0,
        "labeled_closed_positions": 0,
        "criteria": copy.deepcopy(MODULE.EXPECTED_CRITERIA),
        "promotion_ready": False,
        "reasons": [
            "closed live positions 0 are below 3",
            "distinct closed pools 0 are below 2",
            "confirmed successful receipts 0 are below 6",
        ],
    }


def _report(*, ready: bool = False) -> dict:
    nested = _phase7_result(ready=ready)
    ledger = nested["ledger_audit"]
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
        "phase6_post_promotion_audit_sha256": "1" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256_before": "2" * 64,
        "pio_database_sha256_after": "2" * 64,
        "pio_wal_sha256_before": None,
        "pio_wal_sha256_after": None,
        "pio_shm_sha256_before": None,
        "pio_shm_sha256_after": None,
        "snapshot_only": True,
        "source_database_unchanged": True,
        "phase6_promotion_confirmed": True,
        "phase7_criteria": copy.deepcopy(MODULE.EXPECTED_CRITERIA),
        "phase7_criteria_sha256": hashlib.sha256(
            MODULE._canonical_bytes(MODULE.EXPECTED_CRITERIA)
        ).hexdigest(),
        "phase7_report": nested,
        "phase7_report_sha256": hashlib.sha256(
            MODULE._canonical_bytes(nested)
        ).hexdigest(),
        "phase6_promoted": nested["phase6_promoted"],
        "ledger_audit": ledger,
        "ledger_audit_sha256": hashlib.sha256(
            MODULE._canonical_bytes(ledger)
        ).hexdigest(),
        "confirmed_receipts": nested["confirmed_receipts"],
        "failed_receipts": nested["failed_receipts"],
        "closed_positions": nested["closed_positions"],
        "open_positions": nested["open_positions"],
        "distinct_closed_pools": nested["distinct_closed_pools"],
        "valued_closed_positions": nested["valued_closed_positions"],
        "labeled_closed_positions": nested["labeled_closed_positions"],
        "phase7_promotion_ready": nested["promotion_ready"],
        "phase7_reasons": nested["reasons"],
        "phase7_evidence_status_ready": True,
        "requires_additional_controlled_live_evidence": not ready,
        "requires_separate_controlled_live_authorization": True,
        "requires_separate_phase7_promotion_action": True,
        "phase7_promotion_persisted": False,
        "phase7_promotion_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    return {
        **identity,
        "phase7_evidence_status_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["phase7_evidence_status_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_not_ready_status_reports_missing_live_evidence_without_authorizing():
    report = _report(ready=False)

    MODULE.validate_phase7_evidence_status(copy.deepcopy(report))

    assert report["phase7_promotion_ready"] is False
    assert report["requires_additional_controlled_live_evidence"] is True
    assert report["phase7_reasons"]
    assert report["phase7_promotion_persisted"] is False
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_ready_status_still_does_not_authorize_live_or_persist_promotion():
    report = _report(ready=True)

    MODULE.validate_phase7_evidence_status(copy.deepcopy(report))

    assert report["phase7_promotion_ready"] is True
    assert report["requires_additional_controlled_live_evidence"] is False
    assert report["requires_separate_phase7_promotion_action"] is True
    assert report["phase7_promotion_persisted"] is False
    assert report["phase7_promotion_authorized"] is False
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False
    assert report["live_capital_authorized"] is False


def test_resealed_status_cannot_authorize_controlled_live():
    report = _report(ready=True)
    report["controlled_live_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="controlled_live_authorized=false"):
        MODULE.validate_phase7_evidence_status(report)


def test_resealed_status_cannot_authorize_live_submit():
    report = _report(ready=True)
    report["live_submit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase7_evidence_status(report)


def test_resealed_status_cannot_claim_phase7_persisted():
    report = _report(ready=True)
    report["phase7_promotion_persisted"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase7_promotion_persisted=false"):
        MODULE.validate_phase7_evidence_status(report)


def test_resealed_status_cannot_lower_phase7_criteria():
    report = _report(ready=False)
    report["phase7_criteria"]["min_closed_positions"] = 1
    report["phase7_criteria_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(report["phase7_criteria"])
    ).hexdigest()
    _reseal(report)

    with pytest.raises(ValueError, match="criteria mismatch"):
        MODULE.validate_phase7_evidence_status(report)


def test_real_phase7_evaluator_uses_private_snapshot_without_source_mutation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / "pio.db"
        storage = Storage(database)
        storage.save_phase_promotion_evidence(
            phase_name="PHASE6",
            evidence_type="PHASE6_PROMOTION_V1",
            qualified=True,
            evidence={"promotion_ready": True},
        )

        before_db = database.read_bytes()
        wal = Path(str(database) + "-wal")
        shm = Path(str(database) + "-shm")
        before_wal = wal.read_bytes() if wal.exists() else None
        before_shm = shm.read_bytes() if shm.exists() else None

        snapshot = root / "snapshot.db"
        MODULE._snapshot_sqlite(database, snapshot)
        result = MODULE._evaluate_phase7(
            source=ROOT,
            pio_database=snapshot,
        )

        after_db = database.read_bytes()
        after_wal = wal.read_bytes() if wal.exists() else None
        after_shm = shm.read_bytes() if shm.exists() else None

    MODULE._validate_phase7_result(result)
    assert result["phase6_promoted"] is True
    assert result["promotion_ready"] is False
    assert result["closed_positions"] == 0
    assert result["confirmed_receipts"] == 0
    assert before_db == after_db
    assert before_wal == after_wal
    assert before_shm == after_shm


def test_builder_binds_phase6_terminal_audit_and_source_hashes(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        (production / "data").mkdir(parents=True)
        database = production / "data" / "pio.db"
        Storage(database)

        audit = {
            "post_promotion_audit_sha256": "1" * 64,
            "post_promotion_audit_ready": True,
            "phase6_promotion_confirmed": True,
            "phase6_promotion_persisted": True,
            "requires_separate_phase7_workflow": True,
            "production_repository": str(production),
            "pio_database_path": str(database),
            "controlled_live_authorized": False,
            "live_submit_authorized": False,
            "transaction_signing_authorized": False,
            "transaction_submission_authorized": False,
            "live_capital_authorized": False,
        }
        audit_path = root / "phase6-audit.json"
        audit_path.write_text(json.dumps(audit), encoding="utf-8")

        class FakeAudit:
            @staticmethod
            def validate_post_promotion_audit(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_verify_reviewed_source",
            lambda source: FakeAudit,
        )
        monkeypatch.setattr(
            MODULE,
            "_evaluate_phase7",
            lambda **kwargs: _phase7_result(ready=False),
        )

        report = MODULE.build_phase7_evidence_status(
            repository=production,
            source_tree=ROOT,
            phase6_post_promotion_audit_path=audit_path,
        )

    MODULE.validate_phase7_evidence_status(report)
    assert report["phase6_promotion_confirmed"] is True
    assert report["source_database_unchanged"] is True
    assert report["phase7_promotion_ready"] is False
    assert report["phase7_promotion_persisted"] is False


def test_phase7_status_tool_has_no_live_or_promotion_executor():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert "controlled-live-check" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source
    assert "systemctl" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase7_promotion_persisted": False' in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
