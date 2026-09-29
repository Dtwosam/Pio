from __future__ import annotations

import copy
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
    / "build_phase7_controlled_live_exit_completion_evidence_handoff.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_completion_evidence_handoff",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


OPENED = "11111111-2222-4333-8444-555555555555"
EXIT = "01234567-89ab-4def-8123-456789abcdef"
SETTLEMENT = "PHASE7_EXIT_SETTLEMENT:" + "c" * 64
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
DB_SHA = "d" * 64


def _post(database: Path) -> dict:
    return {
        "audit_sha256": "a" * 64,
        "opened_decision_id": OPENED,
        "principal_exit_decision_id": EXIT,
        "settlement_decision_id": SETTLEMENT,
        "pool_address": POOL,
        "position_address": POSITION,
        "position_status": "CLOSED",
        "pio_database_path": str(database),
        "pio_database_sha256_after": DB_SHA,
        "valuation_present": True,
        "learning_label_present": True,
        "persisted_learning_label_matches_apply": True,
        "learning_label_reconciliation_complete": True,
        "phase7_evidence_status_recheck_required": True,
        "phase7_evidence_status_recheck_ready": True,
        "requires_separate_phase7_promotion_action": True,
        "post_label_audit_ready": True,
        "new_live_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_pio_database_modified": False,
    }


def _status(database: Path, *, ready: bool, db_sha: str = DB_SHA) -> dict:
    return {
        "phase7_evidence_status_sha256": "b" * 64,
        "phase7_evidence_status_ready": True,
        "pio_database_path": str(database),
        "pio_database_sha256_before": db_sha,
        "pio_database_sha256_after": db_sha,
        "confirmed_receipts": 6 if ready else 2,
        "failed_receipts": 0,
        "closed_positions": 3 if ready else 1,
        "open_positions": 0,
        "distinct_closed_pools": 2 if ready else 1,
        "valued_closed_positions": 3 if ready else 1,
        "labeled_closed_positions": 3 if ready else 1,
        "phase7_promotion_ready": ready,
        "phase7_reasons": [] if ready else [
            "closed live positions 1 below 3",
            "distinct closed pools 1 below 2",
        ],
    }


def _plan(*, ready: bool) -> dict:
    return {
        "plan_sha256": "c" * 64,
        "phase7_evidence_status_sha256": "b" * 64,
        "collection_plan_ready": True,
        "ledger_clean": True,
        "historical_failed_receipts_blocked": False,
        "open_positions_require_exit_reconciliation": False,
        "ledger_reconciliation_required": False,
        "valuation_completion_required": False,
        "label_completion_required": False,
        "phase7_promotion_ready": ready,
        "new_entry_evidence_candidate": not ready,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    ready: bool,
    fresh_db_sha: str = DB_SHA,
    plan_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        production.mkdir()
        data = production / "data"
        data.mkdir()
        database = data / "pio.db"
        database.write_bytes(b"production")

        post = _post(database)
        status = _status(
            database,
            ready=ready,
            db_sha=fresh_db_sha,
        )
        plan = _plan(ready=ready)
        if plan_override:
            plan.update(plan_override)

        post_path = _write(root / "post.json", post)
        phase6_path = _write(root / "phase6.json", {"unused": True})

        class FakePost:
            @staticmethod
            def validate_exit_post_learning_label_audit_v2(value):
                assert isinstance(value, dict)

        class FakeStatus:
            @staticmethod
            def build_phase7_evidence_status(**kwargs):
                return copy.deepcopy(status)

            @staticmethod
            def validate_phase7_evidence_status(value):
                assert isinstance(value, dict)

        class FakePlan:
            @staticmethod
            def build_phase7_evidence_plan(**kwargs):
                return copy.deepcopy(plan)

            @staticmethod
            def validate_phase7_evidence_plan(value):
                assert isinstance(value, dict)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (FakePost, FakeStatus, FakePlan),
        )

        return MODULE.build_exit_completion_evidence_handoff(
            repository=production,
            source_tree=ROOT,
            saved_post_label_audit_path=post_path,
            expected_post_label_audit_sha256=post["audit_sha256"],
            phase6_post_promotion_audit_path=phase6_path,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["handoff_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_complete_exit_cycle_routes_to_promotion_review(monkeypatch):
    report = _build(monkeypatch, ready=True)

    assert report["closed_position_cycle_complete"] is True
    assert report["phase7_promotion_ready"] is True
    assert report["continuation_route"] == MODULE.ROUTE_PROMOTION
    assert report["closed_positions"] == 3
    assert report["valued_closed_positions"] == 3
    assert report["labeled_closed_positions"] == 3
    assert report["new_entry_evidence_candidate"] is False
    assert report["phase7_promotion_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False


def test_incomplete_clean_evidence_routes_to_more_evidence(monkeypatch):
    report = _build(monkeypatch, ready=False)

    assert report["phase7_promotion_ready"] is False
    assert report["continuation_route"] == MODULE.ROUTE_MORE_EVIDENCE
    assert report["new_entry_evidence_candidate"] is True
    assert report["new_live_entry_authorized"] is False
    assert report["controlled_live_authorized"] is False
    assert report["live_submit_authorized"] is False


def test_database_drift_after_post_label_audit_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="changed after Phase 7 EXIT post-label audit",
    ):
        _build(
            monkeypatch,
            ready=False,
            fresh_db_sha="e" * 64,
        )


def test_route_precedence_remains_fail_closed():
    base = {
        "ledger_reconciliation_required": False,
        "historical_failed_receipts_blocked": False,
        "open_positions_require_exit_reconciliation": False,
        "valuation_completion_required": False,
        "label_completion_required": False,
        "phase7_promotion_ready": False,
        "new_entry_evidence_candidate": False,
    }
    cases = [
        ({"new_entry_evidence_candidate": True}, MODULE.ROUTE_MORE_EVIDENCE),
        ({"phase7_promotion_ready": True}, MODULE.ROUTE_PROMOTION),
        (
            {
                "phase7_promotion_ready": True,
                "label_completion_required": True,
            },
            MODULE.ROUTE_LABEL,
        ),
        (
            {
                "label_completion_required": True,
                "valuation_completion_required": True,
            },
            MODULE.ROUTE_VALUATION,
        ),
        (
            {
                "valuation_completion_required": True,
                "open_positions_require_exit_reconciliation": True,
            },
            MODULE.ROUTE_OPEN,
        ),
        (
            {
                "open_positions_require_exit_reconciliation": True,
                "historical_failed_receipts_blocked": True,
            },
            MODULE.ROUTE_FAILURE,
        ),
        (
            {
                "historical_failed_receipts_blocked": True,
                "ledger_reconciliation_required": True,
            },
            MODULE.ROUTE_LEDGER,
        ),
    ]
    for updates, expected in cases:
        value = dict(base)
        value.update(updates)
        assert MODULE._route(value) == expected


def test_resealed_handoff_cannot_authorize_new_entry(monkeypatch):
    report = _build(monkeypatch, ready=False)
    report["new_live_entry_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="new_live_entry_authorized=false"):
        MODULE.validate_exit_completion_evidence_handoff(report)


def test_resealed_handoff_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch, ready=True)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase7_promotion_authorized=false"):
        MODULE.validate_exit_completion_evidence_handoff(report)


def test_completion_handoff_is_read_only_and_non_authorizing():
    source = TOOL.read_text(encoding="utf-8")

    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert '"new_live_entry_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
    assert '"production_pio_database_modified": False' in source
