from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "build_phase7_promotion_request_v2.py"

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_promotion_request_v2",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


DB_SHA = "d" * 64


def _handoff() -> dict:
    return {
        "handoff_sha256": "a" * 64,
        "continuation_handoff_ready": True,
        "requires_operator_review": True,
        "requires_separate_phase7_promotion_action": True,
        "continuation_route": "PHASE7_PROMOTION_REVIEW",
        "phase7_promotion_ready": True,
        "phase7_reasons": [],
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
        "phase7_evidence_status_sha256": "b" * 64,
        "phase7_evidence_plan_sha256": "c" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "fresh_status_database_sha256_after": DB_SHA,
        "confirmed_receipts": 6,
        "failed_receipts": 0,
        "closed_positions": 3,
        "open_positions": 0,
        "distinct_closed_pools": 2,
        "valued_closed_positions": 3,
        "labeled_closed_positions": 3,
        "ledger_clean": True,
    }


class _FakeHandoffModule:
    ROUTE_PROMOTION = "PHASE7_PROMOTION_REVIEW"

    @staticmethod
    def validate_exit_final_evidence_handoff_v2(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, override: dict | None = None):
    handoff = _handoff()
    if override:
        handoff.update(override)
    monkeypatch.setattr(
        MODULE,
        "_load_handoff_module",
        lambda source: _FakeHandoffModule,
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "handoff.json", handoff)
        return MODULE.build_phase7_promotion_request_v2(
            source_tree=ROOT,
            saved_final_handoff_path=path,
            expected_final_handoff_sha256=handoff["handoff_sha256"],
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependency_is_exactly_pinned():
    path = ROOT / MODULE.FINAL_HANDOFF_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.FINAL_HANDOFF_TOOL
    ]


def test_promotion_request_binds_exact_ready_evidence(monkeypatch):
    request = _build(monkeypatch)

    assert request["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert request["excluded_scopes"] == list(MODULE.EXCLUDED_SCOPES)
    assert request["phase7_promotion_ready"] is True
    assert request["phase7_reasons"] == []
    assert request["failed_receipts"] == 0
    assert request["open_positions"] == 0
    assert request["valued_closed_positions"] == request["closed_positions"]
    assert request["labeled_closed_positions"] == request["closed_positions"]
    assert request["explicit_human_authorization_required"] is True
    assert request["fresh_phase7_promotion_recheck_required"] is True
    assert request["phase7_promotion_authorization_present"] is False
    assert request["phase7_promotion_persisted"] is False
    assert request["transaction_submission_authorized"] is False
    assert request["new_live_capital_authorized"] is False


def test_non_promotion_route_is_rejected(monkeypatch):
    with pytest.raises(
        ValueError,
        match="requires promotion-review route",
    ):
        _build(
            monkeypatch,
            override={"continuation_route": "CONTROLLED_LIVE_EVIDENCE_CANDIDATE"},
        )


def test_open_position_is_rejected(monkeypatch):
    with pytest.raises(
        ValueError,
        match="requires zero open positions",
    ):
        _build(monkeypatch, override={"open_positions": 1})


def test_failed_receipt_is_rejected(monkeypatch):
    with pytest.raises(
        ValueError,
        match="requires zero failed receipts",
    ):
        _build(monkeypatch, override={"failed_receipts": 1})


def test_incomplete_valuation_coverage_is_rejected(monkeypatch):
    with pytest.raises(
        ValueError,
        match="full valuation coverage",
    ):
        _build(monkeypatch, override={"valued_closed_positions": 2})


def test_incomplete_label_coverage_is_rejected(monkeypatch):
    with pytest.raises(
        ValueError,
        match="full label coverage",
    ):
        _build(monkeypatch, override={"labeled_closed_positions": 2})


def test_resealed_request_cannot_contain_authorization(monkeypatch):
    request = _build(monkeypatch)
    request["phase7_promotion_authorization_present"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorization_present=false",
    ):
        MODULE.validate_phase7_promotion_request_v2(request)


def test_resealed_request_cannot_authorize_submission(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_submission_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_phase7_promotion_request_v2(request)


def test_request_has_no_persistence_or_live_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase7_promotion" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert '"phase7_promotion_persisted": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
