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
    / "build_phase7_controlled_live_exit_single_execution_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_single_execution_request",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


OPENED_DECISION_ID = "11111111-2222-4333-8444-555555555555"
EXIT_DECISION_ID = "01234567-89ab-4def-8123-456789abcdef"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
WALLET = "44444444444444444444444444444444"


class _FakeAdmission:
    @staticmethod
    def validate_exit_transaction_execution_admission(value):
        assert isinstance(value, dict)


class _FakeFinalization:
    @staticmethod
    def validate_exit_transaction_finalization(value):
        assert isinstance(value, dict)


def _tx() -> str:
    return "ZmluYWxpemVkLXR4"


def _finalization() -> dict:
    return {
        "finalization_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": "1" * 64,
        "final_transaction_base64": _tx(),
        "final_transaction_sha256": MODULE._sha256_text(_tx()),
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "final_transaction_unsigned": True,
        "exact_simulation_succeeded": True,
        "exit_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


def _admission() -> dict:
    return {
        "admission_sha256": "b" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": "1" * 64,
        "final_transaction_sha256": MODULE._sha256_text(_tx()),
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "fresh_current_block_height": 990,
        "fresh_block_height_remaining": 9,
        "executor_binary_sha256": "2" * 64,
        "executor_keypair_identity_verified": True,
        "blockhash_not_expired": True,
        "fresh_readiness_valid": True,
        "human_exact_exit_transaction_authorization_verified": True,
        "transaction_authorization_not_expired": True,
        "preparation_authorization_not_expired": True,
        "decision_not_expired": True,
        "final_transaction_still_unsigned": True,
        "final_exact_simulation_still_succeeded": True,
        "exit_transaction_execution_admission_ready": True,
        "requires_immediate_single_execution": True,
        "requires_fresh_blockhash_recheck_at_execution": True,
        "requires_separate_single_shot_submitter": True,
        "exit_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    admission_override: dict | None = None,
    finalization_override: dict | None = None,
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        admission = _admission()
        finalization = _finalization()
        if admission_override:
            admission.update(admission_override)
        if finalization_override:
            finalization.update(finalization_override)

        admission_path = _write(root / "admission.json", admission)
        finalization_path = _write(root / "finalization.json", finalization)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeAdmission, _FakeFinalization),
        )
        return MODULE.build_exit_single_execution_request(
            source_tree=ROOT,
            saved_admission_path=admission_path,
            expected_admission_sha256=admission["admission_sha256"],
            saved_finalization_path=finalization_path,
            expected_finalization_sha256=finalization[
                "finalization_sha256"
            ],
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


def test_single_execution_request_binds_exact_transaction_and_retry_contract(
    monkeypatch,
):
    request = _build(monkeypatch)

    assert request["admission_sha256"] == "b" * 64
    assert request["finalization_sha256"] == "a" * 64
    assert request["final_transaction_base64"] == _tx()
    assert request["final_transaction_sha256"] == MODULE._sha256_text(_tx())
    assert request["single_execution_request_ready"] is True
    assert request["dedicated_submission_journal_required"] is True
    assert request["atomic_first_submission_claim_required"] is True
    assert request["signature_persist_before_rpc_send_required"] is True
    assert request["rpc_max_retries_zero_required"] is True
    assert request["automatic_retry_prohibited"] is True
    assert request["uncertain_rpc_requires_recovery"] is True
    assert request["transaction_signing_authorized"] is False
    assert request["transaction_submission_authorized"] is False


def test_finalization_binding_drift_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="position_address binding mismatch"):
        _build(
            monkeypatch,
            admission_override={
                "position_address": "77777777777777777777777777777777"
            },
        )


def test_expired_blockhash_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="blockhash expired"):
        _build(
            monkeypatch,
            admission_override={
                "fresh_current_block_height": 1000,
                "fresh_block_height_remaining": 0,
            },
        )


def test_admission_must_verify_keypair_identity(monkeypatch):
    with pytest.raises(
        ValueError,
        match="lost executor_keypair_identity_verified",
    ):
        _build(
            monkeypatch,
            admission_override={"executor_keypair_identity_verified": False},
        )


def test_finalized_transaction_must_remain_unsigned(monkeypatch):
    with pytest.raises(ValueError, match="no longer unsigned"):
        _build(
            monkeypatch,
            finalization_override={"final_transaction_unsigned": False},
        )


def test_transaction_bytes_digest_is_revalidated(monkeypatch):
    request = _build(monkeypatch)
    request["final_transaction_base64"] = "dGFtcGVyZWQ="
    _reseal(request)

    with pytest.raises(ValueError, match="transaction digest mismatch"):
        MODULE.validate_exit_single_execution_request(request)


def test_resealed_request_cannot_enable_automatic_resubmission(monkeypatch):
    request = _build(monkeypatch)
    request["automatic_resubmission_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="automatic_resubmission_authorized=false",
    ):
        MODULE.validate_exit_single_execution_request(request)


def test_resealed_request_cannot_authorize_signing(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_signing_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_exit_single_execution_request(request)


def test_request_tool_has_no_signing_submission_or_persistence_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "sqlite" not in source.lower()
    assert "load_executor_keypair" not in source
    assert "sign_prepared_transaction" not in source
    assert "send_transaction" not in source
    assert "controlled-live-submit" not in source
    assert "record_sent(" not in source
    assert '"rpc_max_retries_zero_required": True' in source
    assert '"automatic_retry_prohibited": True' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
