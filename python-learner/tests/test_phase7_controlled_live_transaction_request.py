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
    / "build_phase7_controlled_live_transaction_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_transaction_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _presubmit() -> dict:
    return {
        "presubmit_gate_sha256": "1" * 64,
        "fresh_authorization_readiness_sha256": "2" * 64,
        "decision_id": "11111111-2222-4333-8444-555555555555",
        "pool_address": "11111111111111111111111111111111",
        "executor_wallet_pubkey": "22222222222222222222222222222222",
        "phase7_evidence_status_sha256": "3" * 64,
        "phase7_evidence_plan_sha256": "4" * 64,
        "input_preflight_sha256": "5" * 64,
        "signed_authorization_verification_sha256": "6" * 64,
        "controlled_live_config_sha256": "7" * 64,
        "proposal_sha256": "8" * 64,
        "input_manifest_sha256": "9" * 64,
        "execution_request_sha256": "a" * 64,
        "risk_report_sha256": "b" * 64,
        "transaction_guard_sha256": "c" * 64,
        "wallet_authorization_sha256": "d" * 64,
        "prepared_transaction_sha256": "e" * 64,
        "final_simulation_sha256": "f" * 64,
        "execution_intent_snapshot_sha256": "0" * 64,
        "prepared_last_valid_block_height": 123,
        "prepared_rpc_context_slot": 100,
        "final_simulation_rpc_context_slot": 101,
        "presubmit_evidence_ready": True,
        "execution_intent_status": "SIMULATION_PASSED",
        "signature_absent": True,
        "error_absent": True,
        "requires_fresh_blockhash_expiry_recheck": True,
        "requires_separate_transaction_execution_gate": True,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "phase7_promotion_persisted": False,
    }


def _build(monkeypatch, value: dict | None = None) -> dict:
    presubmit = copy.deepcopy(value if value is not None else _presubmit())

    class FakePresubmit:
        @staticmethod
        def validate_phase7_presubmit_evidence_gate(candidate):
            assert isinstance(candidate, dict)

    monkeypatch.setattr(
        MODULE,
        "_load_presubmit_module",
        lambda source: FakePresubmit,
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "presubmit.json"
        path.write_text(json.dumps(presubmit), encoding="utf-8")
        return MODULE.build_phase7_transaction_execution_request(
            source_tree=ROOT,
            presubmit_gate_path=path,
        )


def _reseal(request: dict) -> None:
    identity = {field: request[field] for field in MODULE.REQUEST_FIELDS}
    request["request_sha256"] = MODULE._sha256(identity)


def test_reviewed_presubmit_gate_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_ready_request_binds_exact_presubmit_transaction_but_authorizes_nothing(
    monkeypatch,
):
    request = _build(monkeypatch)

    assert request["presubmit_gate_sha256"] == "1" * 64
    assert request["authorization_readiness_sha256"] == "2" * 64
    assert request["prepared_transaction_sha256"] == "e" * 64
    assert request["final_simulation_sha256"] == "f" * 64
    assert request["prepared_last_valid_block_height"] == 123
    assert request["transaction_execution_request_ready"] is True
    assert request["explicit_human_transaction_authorization_required"] is True
    assert request["fresh_presubmit_recheck_required"] is True
    assert request["fresh_blockhash_expiry_recheck_required"] is True
    assert request["transaction_execution_authorization_present"] is False
    assert request["controlled_live_authorized"] is False
    assert request["live_submit_authorized"] is False
    assert request["transaction_signing_authorized"] is False
    assert request["transaction_submission_authorized"] is False
    assert request["live_capital_authorized"] is False


def test_request_rejects_non_ready_presubmit(monkeypatch):
    presubmit = _presubmit()
    presubmit["presubmit_evidence_ready"] = False

    with pytest.raises(ValueError, match="not ready"):
        _build(monkeypatch, presubmit)


def test_request_rejects_intent_that_started_signing(monkeypatch):
    presubmit = _presubmit()
    presubmit["execution_intent_status"] = "SIGNING"

    with pytest.raises(ValueError, match="not SIMULATION_PASSED"):
        _build(monkeypatch, presubmit)


def test_request_rejects_existing_signature(monkeypatch):
    presubmit = _presubmit()
    presubmit["signature_absent"] = False

    with pytest.raises(ValueError, match="already has a signature"):
        _build(monkeypatch, presubmit)


def test_request_rejects_execution_error(monkeypatch):
    presubmit = _presubmit()
    presubmit["error_absent"] = False

    with pytest.raises(ValueError, match="already has an error"):
        _build(monkeypatch, presubmit)


def test_resealed_request_cannot_authorize_signing(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_signing_authorized"] = True
    _reseal(request)

    with pytest.raises(ValueError, match="transaction_signing_authorized=false"):
        MODULE.validate_phase7_transaction_execution_request(request)


def test_resealed_request_cannot_authorize_submission(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_submission_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_phase7_transaction_execution_request(request)


def test_resealed_request_cannot_claim_execution_authorization(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_execution_authorization_present"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="transaction_execution_authorization_present=false",
    ):
        MODULE.validate_phase7_transaction_execution_request(request)


def test_resealed_request_cannot_expand_scope(monkeypatch):
    request = _build(monkeypatch)
    request["authorization_scope"] = "ANY_TRANSACTION"
    _reseal(request)

    with pytest.raises(ValueError, match="scope mismatch"):
        MODULE.validate_phase7_transaction_execution_request(request)


def test_resealed_request_cannot_remove_excluded_scope(monkeypatch):
    request = _build(monkeypatch)
    request["excluded_scopes"] = request["excluded_scopes"][:-1]
    _reseal(request)

    with pytest.raises(ValueError, match="excluded scopes mismatch"):
        MODULE.validate_phase7_transaction_execution_request(request)


def test_resealed_request_cannot_change_prepared_transaction_without_digest_change(
    monkeypatch,
):
    request = _build(monkeypatch)
    original_request_sha = request["request_sha256"]
    request["prepared_transaction_sha256"] = "1" * 64

    assert request["request_sha256"] == original_request_sha
    with pytest.raises(ValueError, match="digest mismatch"):
        MODULE.validate_phase7_transaction_execution_request(request)


def test_transaction_request_tool_has_no_execution_or_key_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "subprocess" not in source
    assert "load_executor_keypair" not in source
    assert "submit_execution_intent" not in source
    assert "begin_signing(" not in source
    assert "record_sent(" not in source
    assert "systemctl" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source
    assert "ssh-keygen" not in source
    assert "persist_phase7_promotion" not in source
    assert "save_phase_promotion_evidence(" not in source
    assert 'git", "pull' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert '"transaction_execution_authorization_present": False' in source
    assert '"controlled_live_authorized": False' in source
    assert '"live_submit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"live_capital_authorized": False' in source
