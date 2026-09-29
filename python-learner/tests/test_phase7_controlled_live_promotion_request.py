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
    / "build_phase7_controlled_live_promotion_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_promotion_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _handoff() -> dict:
    return {
        "handoff_sha256": "a" * 64,
        "phase7_evidence_status_sha256": "b" * 64,
        "phase7_evidence_plan_sha256": "c" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "fresh_status_database_sha256_after": "d" * 64,
        "confirmed_receipts": 6,
        "failed_receipts": 0,
        "closed_positions": 3,
        "open_positions": 0,
        "distinct_closed_pools": 2,
        "valued_closed_positions": 3,
        "labeled_closed_positions": 3,
        "ledger_clean": True,
        "continuation_route": "PHASE7_PROMOTION_REVIEW",
        "phase7_promotion_ready": True,
        "phase7_reasons": [],
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "production_pio_database_modified": False,
    }


class _FakeHandoff:
    ROUTE_PROMOTION = "PHASE7_PROMOTION_REVIEW"

    @staticmethod
    def validate_exit_completion_evidence_handoff(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, override: dict | None = None):
    value = copy.deepcopy(_handoff())
    if override:
        value.update(override)

    monkeypatch.setattr(
        MODULE,
        "_load_handoff_module",
        lambda source: _FakeHandoff,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _write(root / "handoff.json", value)
        return MODULE.build_phase7_promotion_request(
            source_tree=ROOT,
            completion_handoff_path=path,
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_handoff_tool_is_exactly_pinned():
    path = ROOT / MODULE.HANDOFF_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.HANDOFF_TOOL
    ]


def test_ready_request_binds_exact_completed_evidence_but_authorizes_nothing(
    monkeypatch,
):
    request = _build(monkeypatch)

    assert request["completion_handoff_sha256"] == "a" * 64
    assert request["phase7_evidence_status_sha256"] == "b" * 64
    assert request["phase7_evidence_plan_sha256"] == "c" * 64
    assert request["pio_database_sha256"] == "d" * 64
    assert request["closed_positions"] == 3
    assert request["valued_closed_positions"] == 3
    assert request["labeled_closed_positions"] == 3
    assert request["promotion_request_ready"] is True
    assert request["explicit_human_authorization_required"] is True
    assert request["phase7_promotion_authorization_present"] is False
    assert request["phase7_promotion_persisted"] is False
    assert request["live_submit_authorized"] is False
    assert request["new_live_capital_authorized"] is False


def test_non_promotion_route_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="not routed to promotion"):
        _build(
            monkeypatch,
            {"continuation_route": "CONTROLLED_LIVE_EVIDENCE_CANDIDATE"},
        )


def test_open_position_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="open positions"):
        _build(monkeypatch, {"open_positions": 1})


def test_failed_receipt_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="failed receipts"):
        _build(monkeypatch, {"failed_receipts": 1})


def test_incomplete_valuation_coverage_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="valuation coverage"):
        _build(monkeypatch, {"valued_closed_positions": 2})


def test_incomplete_label_coverage_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="label coverage"):
        _build(monkeypatch, {"labeled_closed_positions": 2})


def test_resealed_request_cannot_claim_promotion_persisted(monkeypatch):
    request = _build(monkeypatch)
    request["phase7_promotion_persisted"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="phase7_promotion_persisted=false"):
        MODULE.validate_phase7_promotion_request(request)


def test_resealed_request_cannot_authorize_live_submit(monkeypatch):
    request = _build(monkeypatch)
    request["live_submit_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="live_submit_authorized=false"):
        MODULE.validate_phase7_promotion_request(request)


def test_request_tool_has_no_persistence_or_transaction_execution():
    source = TOOL.read_text(encoding="utf-8")

    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"phase7_promotion_persisted": False' in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"new_live_capital_authorized": False' in source
