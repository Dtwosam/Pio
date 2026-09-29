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
    / "build_phase8_paired_ml_paper_entry_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase8_paired_ml_paper_entry_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _readiness(*, status: str = "READY") -> dict:
    ready = status == "READY"
    return {
        "readiness_sha256": "a" * 64,
        "readiness_status": status,
        "paired_entry_input_verification_sha256": "b" * 64,
        "source_post_audit_sha256": "c" * 64,
        "source_paper_evidence_input_verification_sha256": "d" * 64,
        "production_repository": "/opt/pio",
        "pio_database_path": "/opt/pio/data/pio.db",
        "pio_database_sha256": "e" * 64,
        "pio_wal_sha256": None,
        "pio_shm_sha256": None,
        "active_cycle_id": "cycle-1",
        "incumbent_model_id": "champion-1",
        "challenger_model_id": "challenger-1",
        "account_id": "paper-1",
        "pair_id": "pair-001",
        "pool_address": "pool-1",
        "capital_quote": 1000.0,
        "entry_cost_quote": 5.0,
        "required_pair_cash_quote": 2010.0,
        "incumbent_position_id": "p8-pair-001-incumbent",
        "challenger_position_id": "p8-pair-001-challenger",
        "incumbent_event_key": "p8-pair-001:incumbent",
        "challenger_event_key": "p8-pair-001:challenger",
        "account_ready": ready,
        "paired_entry_preconditions_ready": ready,
        "cycle_model_binding_valid": True,
        "paper_cash_sufficient_for_pair": ready,
        "pair_position_ids_available": True,
        "pair_event_keys_available": True,
        "account_creation_required": (
            status == "ACCOUNT_CREATION_REQUIRED"
        ),
        "paper_account_creation_authorized": False,
        "paper_pair_entry_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
    }


class FakeReadiness:
    STATUS_READY = "READY"

    @staticmethod
    def validate_phase8_paired_ml_paper_account_readiness(value):
        assert isinstance(value, dict)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(monkeypatch, *, status: str = "READY"):
    monkeypatch.setattr(
        MODULE,
        "_load_readiness_module",
        lambda source: FakeReadiness,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp) / "readiness.json", _readiness(status=status))
        return MODULE.build_phase8_paired_ml_paper_entry_request(
            source_tree=ROOT,
            account_readiness_path=path,
        )


def _reseal(request: dict) -> None:
    identity = {
        field: request[field]
        for field in MODULE.REQUEST_FIELDS
    }
    request["request_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_account_readiness_is_exactly_pinned():
    path = ROOT / MODULE.ACCOUNT_READINESS_TOOL
    assert path.is_file()
    assert MODULE._git_blob_sha(path) == MODULE.REVIEWED_SOURCE_BLOBS[
        MODULE.ACCOUNT_READINESS_TOOL
    ]


def test_ready_account_builds_non_authorizing_one_pair_request(
    monkeypatch,
):
    request = _build(monkeypatch)

    assert request["authorization_scope"] == MODULE.AUTHORIZATION_SCOPE
    assert request["decision"] == MODULE.DECISION
    assert request["active_cycle_id"] == "cycle-1"
    assert request["incumbent_model_id"] == "champion-1"
    assert request["challenger_model_id"] == "challenger-1"
    assert request["account_id"] == "paper-1"
    assert request["pair_id"] == "pair-001"
    assert request["capital_quote"] == 1000.0
    assert request["required_pair_cash_quote"] == 2010.0
    assert request["request_ready"] is True
    assert request["explicit_human_authorization_required"] is True
    assert request["fresh_account_readiness_recheck_required"] is True
    assert request["fresh_model_inference_readiness_required"] is True
    assert request["same_candidate_frame_required"] is True
    assert request["same_decision_snapshot_required"] is True
    assert request["equal_capital_required"] is True
    assert request["atomic_pair_open_required"] is True
    assert request["post_pair_audit_required"] is True
    assert request["paper_account_creation_authorized"] is False
    assert request["paper_pair_entry_authorization_present"] is False
    assert request["paper_pair_entry_authorized"] is False
    assert request["paper_pair_entry_executed"] is False
    assert request["paper_evidence_collection_authorized"] is False
    assert request["paper_trading_authorized"] is False
    assert request["live_submit_authorized"] is False
    assert request["new_live_capital_authorized"] is False
    assert request["phase8_promotion_authorized"] is False


def test_create_account_dependency_cannot_build_pair_request(monkeypatch):
    with pytest.raises(ValueError, match="requires READY"):
        _build(monkeypatch, status="ACCOUNT_CREATION_REQUIRED")


def test_insufficient_cash_cannot_build_pair_request(monkeypatch):
    with pytest.raises(ValueError, match="requires READY"):
        _build(monkeypatch, status="INSUFFICIENT_PAPER_CASH")


def test_resealed_request_cannot_self_authorize_pair(monkeypatch):
    request = _build(monkeypatch)
    request["paper_pair_entry_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="paper_pair_entry_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_entry_request(request)


def test_resealed_request_cannot_authorize_broad_paper_trading(monkeypatch):
    request = _build(monkeypatch)
    request["paper_trading_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="paper_trading_authorized=false",
    ):
        MODULE.validate_phase8_paired_ml_paper_entry_request(request)


def test_pair_request_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paired_ml_paper_entries(" not in source
    assert "create_paper_account(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert '"paper_pair_entry_authorized": False' in source
    assert '"paper_pair_entry_executed": False' in source
    assert '"paper_trading_authorized": False' in source
    assert '"live_submit_authorized": False' in source
