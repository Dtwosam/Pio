from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "check_phase7_controlled_live_exit_final_evidence.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_final_evidence",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


POSITION = "33333333333333333333333333333333"
DB_SHA = "d" * 64


class _PostLabel:
    @staticmethod
    def validate_exit_learning_label_post_audit(value):
        assert isinstance(value, dict)


class _Evidence:
    result = None

    @classmethod
    def build_phase7_evidence_status(cls, **kwargs):
        assert cls.result is not None
        return dict(cls.result)

    @staticmethod
    def validate_phase7_evidence_status(value):
        assert isinstance(value, dict)


def _post_label(database: Path) -> dict:
    return {
        "audit_sha256": "a" * 64,
        "position_address": POSITION,
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "settlement_decision_id": "PHASE7_EXIT_SETTLEMENT:" + "c" * 64,
        "pio_database_path": str(database),
        "pio_database_sha256_after": DB_SHA,
        "target_state_matches_apply": True,
        "valuation_matches_apply": True,
        "learning_label_matches_apply": True,
        "learning_label_post_audit_ready": True,
        "phase7_evidence_status_recheck_required": True,
        "requires_separate_phase7_promotion_action": True,
        "outcome_label_status": "VALUED",
        "production_pio_database_modified": False,
    }


def _evidence(
    database: Path,
    *,
    ready: bool,
) -> dict:
    reasons = [] if ready else ["closed_positions_below_minimum"]
    return {
        "phase7_evidence_status_sha256": "b" * 64,
        "pio_database_path": str(database),
        "pio_database_sha256_before": DB_SHA,
        "pio_database_sha256_after": DB_SHA,
        "phase7_evidence_status_ready": True,
        "requires_separate_phase7_promotion_action": True,
        "phase6_promoted": True,
        "ledger_audit": {"clean": True},
        "confirmed_receipts": 3,
        "failed_receipts": 0,
        "closed_positions": 1,
        "open_positions": 0,
        "distinct_closed_pools": 1,
        "valued_closed_positions": 1,
        "labeled_closed_positions": 1,
        "phase7_promotion_ready": ready,
        "phase7_reasons": reasons,
        "requires_additional_controlled_live_evidence": not ready,
        "phase7_promotion_persisted": False,
        "phase7_promotion_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, ready: bool):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        database = repo / "data" / "pio.db"
        database.parent.mkdir()
        database.write_bytes(b"production")

        post = _post_label(database)
        post_path = _write(root / "post-label.json", post)
        phase6 = _write(root / "phase6.json", {"dummy": True})
        _Evidence.result = _evidence(database, ready=ready)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_PostLabel, _Evidence),
        )

        return MODULE.build_exit_final_evidence_recheck(
            repository=repo,
            source_tree=ROOT,
            saved_post_label_audit_path=post_path,
            expected_post_label_audit_sha256=post["audit_sha256"],
            phase6_post_promotion_audit_path=phase6,
        )


def _reseal(report):
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["recheck_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_evidence_recheck_can_report_more_evidence_required(monkeypatch):
    report = _build(monkeypatch, ready=False)

    assert report["exact_exit_position_valued_and_labeled"] is True
    assert report["phase7_evidence_recheck_ready"] is True
    assert report["phase7_promotion_ready"] is False
    assert report["requires_additional_controlled_live_evidence"] is True
    assert report["phase7_reasons"]
    assert report["requires_separate_phase7_promotion_action"] is True
    assert report["phase7_promotion_authorized"] is False
    assert report["phase7_promotion_persisted"] is False
    assert report["live_capital_authorized"] is False


def test_evidence_recheck_ready_still_does_not_promote(monkeypatch):
    report = _build(monkeypatch, ready=True)

    assert report["phase7_promotion_ready"] is True
    assert report["phase7_reasons"] == []
    assert report["requires_additional_controlled_live_evidence"] is False
    assert report["requires_separate_phase7_promotion_action"] is True
    assert report["phase7_promotion_authorized"] is False
    assert report["phase7_promotion_persisted"] is False
    assert report["controlled_live_authorized"] is False


def test_database_binding_to_post_label_audit_is_required(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        database = repo / "data" / "pio.db"
        database.parent.mkdir()
        database.write_bytes(b"production")
        post = _post_label(database)
        post_path = _write(root / "post-label.json", post)
        phase6 = _write(root / "phase6.json", {"dummy": True})
        result = _evidence(database, ready=False)
        result["pio_database_sha256_before"] = "f" * 64
        _Evidence.result = result

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_PostLabel, _Evidence),
        )
        with pytest.raises(ValueError, match="database differs"):
            MODULE.build_exit_final_evidence_recheck(
                repository=repo,
                source_tree=ROOT,
                saved_post_label_audit_path=post_path,
                expected_post_label_audit_sha256=post["audit_sha256"],
                phase6_post_promotion_audit_path=phase6,
            )


def test_resealed_recheck_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch, ready=True)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_final_evidence_recheck(report)


def test_resealed_recheck_cannot_authorize_live_capital(monkeypatch):
    report = _build(monkeypatch, ready=True)
    report["live_capital_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="live_capital_authorized=false",
    ):
        MODULE.validate_exit_final_evidence_recheck(report)


def test_final_evidence_recheck_has_no_promotion_or_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "build_phase7_evidence_status" in source
    assert "persist_phase7_promotion" not in source
    assert "send_transaction" not in source
    assert "load_executor_keypair" not in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"phase7_promotion_persisted": False' in source
    assert '"transaction_submission_authorized": False' in source
