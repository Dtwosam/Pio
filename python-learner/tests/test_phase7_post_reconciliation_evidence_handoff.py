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
    / "build_phase7_controlled_live_post_reconciliation_handoff.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_post_reconciliation_handoff",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


DECISION_ID = "11111111-2222-4333-8444-555555555555"
POSITION = "33333333333333333333333333333333"
DB_SHA = "d" * 64


def _post(*, status: str = "CONFIRMED") -> dict:
    confirmed = status == "CONFIRMED"
    return {
        "audit_sha256": "a" * 64,
        "post_reconciliation_audit_ready": True,
        "phase7_evidence_status_recheck_required": True,
        "intent_status": status,
        "position_address": POSITION if confirmed else None,
        "position_status": "OPEN" if confirmed else None,
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256_after": DB_SHA,
        "new_live_entry_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
    }


def _status(*, status: str = "CONFIRMED", db_sha: str = DB_SHA) -> dict:
    confirmed = status == "CONFIRMED"
    return {
        "phase7_evidence_status_sha256": "b" * 64,
        "phase7_evidence_status_ready": True,
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256_before": db_sha,
        "pio_database_sha256_after": db_sha,
        "confirmed_receipts": 1 if confirmed else 0,
        "failed_receipts": 0 if confirmed else 1,
        "closed_positions": 0,
        "open_positions": 1 if confirmed else 0,
        "distinct_closed_pools": 0,
        "valued_closed_positions": 0,
        "labeled_closed_positions": 0,
        "phase7_promotion_ready": False,
        "phase7_reasons": (
            ["open/unsettled live positions 1 exceed 0"]
            if confirmed
            else ["failed live receipts 1 exceed 0"]
        ),
    }


def _plan(*, status: str = "CONFIRMED") -> dict:
    confirmed = status == "CONFIRMED"
    return {
        "plan_sha256": "c" * 64,
        "phase7_evidence_status_sha256": "b" * 64,
        "ledger_clean": True,
        "historical_failed_receipts_blocked": not confirmed,
        "open_positions_require_exit_reconciliation": confirmed,
        "ledger_reconciliation_required": False,
        "valuation_completion_required": False,
        "label_completion_required": False,
        "phase7_promotion_ready": False,
        "new_entry_evidence_candidate": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    intent_status: str = "CONFIRMED",
    fresh_db_sha: str = DB_SHA,
    plan_override: dict | None = None,
) -> dict:
    post = _post(status=intent_status)
    status = _status(status=intent_status, db_sha=fresh_db_sha)
    plan = _plan(status=intent_status)
    if plan_override:
        plan.update(plan_override)

    class FakePost:
        @staticmethod
        def validate_post_reconciliation_audit(value):
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

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        production = root / "production"
        production.mkdir()
        audit_path = _write(root / "post.json", post)
        phase6_path = _write(root / "phase6.json", {"unused": True})

        return MODULE.build_post_reconciliation_evidence_handoff(
            repository=production,
            source_tree=ROOT,
            saved_post_reconciliation_audit_path=audit_path,
            expected_post_reconciliation_audit_sha256=post["audit_sha256"],
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


def test_confirmed_open_position_routes_to_lifecycle_without_new_entry(monkeypatch):
    report = _build(monkeypatch, intent_status="CONFIRMED")

    assert report["continuation_route"] == MODULE.ROUTE_OPEN
    assert report["position_address"] == POSITION
    assert report["position_status"] == "OPEN"
    assert report["open_positions"] == 1
    assert report["open_positions_require_exit_reconciliation"] is True
    assert report["new_entry_evidence_candidate"] is False
    assert report["new_live_entry_authorized"] is False
    assert report["transaction_submission_authorized"] is False
    assert report["new_live_capital_authorized"] is False


def test_failed_enter_routes_to_failure_review(monkeypatch):
    report = _build(monkeypatch, intent_status="FAILED")

    assert report["continuation_route"] == MODULE.ROUTE_FAILURE
    assert report["position_address"] is None
    assert report["position_status"] is None
    assert report["failed_receipts"] == 1
    assert report["historical_failed_receipts_blocked"] is True
    assert report["new_entry_evidence_candidate"] is False


def test_route_precedence_is_fail_closed():
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


def test_database_drift_after_reconciliation_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="changed after post-reconciliation"):
        _build(
            monkeypatch,
            intent_status="CONFIRMED",
            fresh_db_sha="e" * 64,
        )


def test_confirmed_open_position_must_keep_lifecycle_route(monkeypatch):
    with pytest.raises(ValueError, match="does not route the open position"):
        _build(
            monkeypatch,
            intent_status="CONFIRMED",
            plan_override={
                "open_positions_require_exit_reconciliation": False,
                "new_entry_evidence_candidate": False,
            },
        )


def test_confirmed_open_position_cannot_expose_new_entry_candidate(monkeypatch):
    with pytest.raises(ValueError, match="incorrectly exposes another ENTER"):
        _build(
            monkeypatch,
            intent_status="CONFIRMED",
            plan_override={"new_entry_evidence_candidate": True},
        )


def test_resealed_handoff_cannot_authorize_new_entry(monkeypatch):
    report = _build(monkeypatch)
    report["new_live_entry_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="new_live_entry_authorized=false"):
        MODULE.validate_post_reconciliation_evidence_handoff(report)


def test_resealed_handoff_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="transaction_submission_authorized=false"):
        MODULE.validate_post_reconciliation_evidence_handoff(report)


def test_resealed_handoff_cannot_authorize_promotion(monkeypatch):
    report = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="phase7_promotion_authorized=false"):
        MODULE.validate_post_reconciliation_evidence_handoff(report)


def test_handoff_tool_is_read_only_and_non_authorizing():
    source = TOOL.read_text(encoding="utf-8")

    assert '"controlled-live-submit-once"' not in source
    assert '"controlled-live-submit"' not in source
    assert '"execution-confirmation"' not in source
    assert '"execution-recovery"' not in source
    assert "apply_live_execution_effect(" not in source
    assert "apply_live_position_effect(" not in source
    assert '"new_live_entry_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
