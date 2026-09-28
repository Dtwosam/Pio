from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_phase5_promotion_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_phase5_promotion_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _status() -> dict:
    return {
        "phase5_evidence_status_sha256": "2" * 64,
        "account": "pio-proof-1",
        "run_id": "phase5-collection-1",
        "phase5_criteria_sha256": "3" * 64,
        "endurance_sha256": "4" * 64,
        "ledger_audit_sha256": "5" * 64,
        "closed_positions": 3,
        "distinct_valued_pools": 2,
        "phase3_promoted": True,
        "phase5_promotion_ready": True,
        "phase5_reasons": [],
        "requires_additional_paper_evidence": False,
        "phase5_promotion_persisted": False,
        "phase5_promotion_authorized": False,
        "recurring_paper_automation_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }


def _audit(*, status: dict | None = None) -> dict:
    fresh = copy.deepcopy(status if status is not None else _status())
    return {
        "post_collection_audit_sha256": "1" * 64,
        "post_collection_audit_ready": True,
        "phase5_promotion_ready": True,
        "requires_new_collection_plan": False,
        "requires_additional_paper_evidence": False,
        "phase5_reasons": [],
        "phase5_promotion_persisted": False,
        "phase5_promotion_authorized": False,
        "production_repository": "/opt/pio",
        "fresh_phase5_evidence_status_sha256": fresh[
            "phase5_evidence_status_sha256"
        ],
        "fresh_phase5_evidence_status": fresh,
    }


def _write(root: Path, value: dict) -> Path:
    path = root / "post-collection.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, audit: dict | None = None) -> dict:
    value = copy.deepcopy(audit if audit is not None else _audit())

    class FakeAudit:
        @staticmethod
        def validate_post_collection_audit(candidate):
            assert isinstance(candidate, dict)

    class FakeStatus:
        @staticmethod
        def validate_phase5_evidence_status(candidate):
            assert isinstance(candidate, dict)

    monkeypatch.setattr(
        MODULE,
        "_load_reviewed_modules",
        lambda source: (FakeAudit, FakeStatus),
    )

    with tempfile.TemporaryDirectory() as tmp:
        return MODULE.build_phase5_promotion_request(
            source_tree=ROOT,
            post_collection_audit_path=_write(Path(tmp), value),
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_request_binds_exact_evidence_without_authorizing_persistence(
    monkeypatch,
):
    request = _build(monkeypatch)

    assert request["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert request["post_collection_audit_sha256"] == "1" * 64
    assert request["phase5_evidence_status_sha256"] == "2" * 64
    assert request["phase5_criteria_sha256"] == "3" * 64
    assert request["endurance_sha256"] == "4" * 64
    assert request["ledger_audit_sha256"] == "5" * 64
    assert request["closed_positions"] == 3
    assert request["distinct_valued_pools"] == 2
    assert request["phase3_promoted"] is True
    assert request["phase5_promotion_ready"] is True
    assert request["phase5_reasons"] == []
    assert request["promotion_request_ready"] is True
    assert request["phase5_promotion_authorization_present"] is False
    assert request["phase5_promotion_persisted"] is False
    assert request["explicit_human_authorization_required"] is True
    assert request["fresh_phase5_promotion_recheck_required"] is True
    assert request["paper_timer_enable_authorized"] is False
    assert request["transaction_signing_authorized"] is False
    assert request["transaction_submission_authorized"] is False
    assert request["live_capital_authorized"] is False


def test_request_rejects_incomplete_phase5_evidence(monkeypatch):
    audit = _audit()
    audit["phase5_promotion_ready"] = False
    audit["requires_new_collection_plan"] = True
    audit["requires_additional_paper_evidence"] = True
    audit["phase5_reasons"] = ["more PAPER evidence required"]

    with pytest.raises(ValueError, match="not promotion-ready"):
        _build(monkeypatch, audit=audit)


def test_request_rejects_missing_phase3_promotion(monkeypatch):
    status = _status()
    status["phase3_promoted"] = False
    audit = _audit(status=status)

    with pytest.raises(ValueError, match="persistent Phase 3 promotion"):
        _build(monkeypatch, audit=audit)


def test_request_rejects_nested_status_still_requiring_evidence(monkeypatch):
    status = _status()
    status["requires_additional_paper_evidence"] = True
    audit = _audit(status=status)

    with pytest.raises(ValueError, match="still requires evidence"):
        _build(monkeypatch, audit=audit)


def test_request_rejects_nested_status_blocking_reasons(monkeypatch):
    status = _status()
    status["phase5_reasons"] = ["blocked"]
    audit = _audit(status=status)

    with pytest.raises(ValueError, match="blocking reasons"):
        _build(monkeypatch, audit=audit)


def test_request_rejects_already_persisted_promotion(monkeypatch):
    audit = _audit()
    audit["phase5_promotion_persisted"] = True

    with pytest.raises(ValueError, match="already persisted"):
        _build(monkeypatch, audit=audit)


def test_resealed_request_cannot_persist_promotion(monkeypatch):
    request = _build(monkeypatch)
    request["phase5_promotion_persisted"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="phase5_promotion_persisted=false"):
        MODULE.validate_phase5_promotion_request(request)


def test_resealed_request_cannot_claim_human_authorization(monkeypatch):
    request = _build(monkeypatch)
    request["phase5_promotion_authorization_present"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="phase5_promotion_authorization_present=false",
    ):
        MODULE.validate_phase5_promotion_request(request)


def test_resealed_request_cannot_authorize_timer(monkeypatch):
    request = _build(monkeypatch)
    request["paper_timer_enable_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="paper_timer_enable_authorized=false"):
        MODULE.validate_phase5_promotion_request(request)


def test_resealed_request_cannot_authorize_live_capital(monkeypatch):
    request = _build(monkeypatch)
    request["live_capital_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="live_capital_authorized=false"):
        MODULE.validate_phase5_promotion_request(request)


def test_promotion_request_tool_has_no_production_or_persistence_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert "persist_phase5_promotion" not in source
    assert "save_phase_promotion_evidence" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"phase5_promotion_authorization_present": False' in source
    assert '"phase5_promotion_persisted": False' in source
    assert '"paper_timer_enable_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
