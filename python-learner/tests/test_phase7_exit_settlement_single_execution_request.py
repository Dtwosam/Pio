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
    / "build_phase7_controlled_live_exit_settlement_single_execution_request.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_settlement_single_execution_request",
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
USER_X = "55555555555555555555555555555555"
USER_Y = "66666666666666666666666666666666"
REWARD = "77777777777777777777777777777777"
TX = "c2V0dGxlbWVudC10eA=="


def _false_boundaries() -> dict:
    return {
        "settlement_authorized": False,
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


def _destinations() -> list[dict]:
    return [{"reward_index": 0, "user_token_account": REWARD}]


def _destination_sha() -> str:
    return hashlib.sha256(
        MODULE._canonical_bytes({
            "user_token_x": USER_X,
            "user_token_y": USER_Y,
            "reward_token_destinations": _destinations(),
        })
    ).hexdigest()


def _admission() -> dict:
    return {
        "admission_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "exit_signature": "sig-exit",
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": _destination_sha(),
        "final_settlement_transaction_sha256": MODULE._sha256_text(TX),
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "fresh_current_block_height": 950,
        "fresh_block_height_remaining": 49,
        "rpc_endpoint_sha256": "b" * 64,
        "executor_binary_sha256": "c" * 64,
        "executor_keypair_identity_verified": True,
        "blockhash_not_expired": True,
        "fresh_readiness_valid": True,
        "human_exact_settlement_transaction_authorization_verified": True,
        "transaction_authorization_not_expired": True,
        "final_transaction_still_unsigned": True,
        "final_exact_simulation_still_succeeded": True,
        "settlement_execution_admission_ready": True,
        "requires_immediate_single_execution": True,
        "requires_fresh_blockhash_recheck_at_execution": True,
        "requires_separate_single_shot_submitter": True,
        "requires_post_settlement_confirmation": True,
        "requires_post_close_account_absence_proof": True,
        "requires_post_exit_state_reconciliation": True,
        **_false_boundaries(),
    }


def _finalization() -> dict:
    return {
        "finalization_sha256": "d" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "exit_signature": "sig-exit",
        "zero_liquidity_snapshot_sha256": "e" * 64,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": _destination_sha(),
        "user_token_x": USER_X,
        "user_token_y": USER_Y,
        "reward_token_destinations": _destinations(),
        "rpc_endpoint_sha256": "b" * 64,
        "final_settlement_transaction_base64": TX,
        "final_settlement_transaction_sha256": MODULE._sha256_text(TX),
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "final_transaction_unsigned": True,
        "exact_simulation_succeeded": True,
        **_false_boundaries(),
    }


class _FakeAdmission:
    @staticmethod
    def validate_exit_settlement_execution_admission(value):
        assert isinstance(value, dict)


class _FakeFinalization:
    @staticmethod
    def validate_exit_settlement_finalization(value):
        assert isinstance(value, dict)


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
        return MODULE.build_exit_settlement_single_execution_request(
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


def test_settlement_request_is_typed_bound_and_non_authorizing(monkeypatch):
    request = _build(monkeypatch)

    assert request["artifact_type"] == (
        "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_SINGLE_EXECUTION_REQUEST_V1"
    )
    assert request["execution_scope"] == (
        "SUBMIT_EXACT_VERIFIED_PHASE7_EXIT_SETTLEMENT_TRANSACTION_ONCE_ONLY"
    )
    assert request["destination_config_sha256"] == _destination_sha()
    assert request["reward_token_destinations"] == _destinations()
    assert request["final_settlement_transaction_base64"] == TX
    assert request["single_execution_request_ready"] is True
    assert request["rpc_max_retries_zero_required"] is True
    assert request["automatic_retry_prohibited"] is True
    assert request["settlement_authorized"] is False
    assert request["transaction_signing_authorized"] is False
    assert request["transaction_submission_authorized"] is False


def test_destination_binding_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="destination_config_sha256 binding mismatch",
    ):
        _build(
            monkeypatch,
            admission_override={"destination_config_sha256": "f" * 64},
        )


def test_transaction_binding_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="final_settlement_transaction_sha256 binding mismatch",
    ):
        _build(
            monkeypatch,
            admission_override={
                "final_settlement_transaction_sha256": "f" * 64
            },
        )


def test_expired_blockhash_fails_validation(monkeypatch):
    with pytest.raises(ValueError, match="blockhash expired"):
        _build(
            monkeypatch,
            admission_override={
                "fresh_current_block_height": 1000,
                "fresh_block_height_remaining": 0,
            },
        )


def test_admission_cannot_pre_authorize_submission(monkeypatch):
    with pytest.raises(
        ValueError,
        match="unexpectedly authorizes transaction_submission_authorized",
    ):
        _build(
            monkeypatch,
            admission_override={"transaction_submission_authorized": True},
        )


def test_resealed_request_cannot_authorize_signing(monkeypatch):
    request = _build(monkeypatch)
    request["transaction_signing_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_exit_settlement_single_execution_request(request)


def test_resealed_request_cannot_authorize_resubmission(monkeypatch):
    request = _build(monkeypatch)
    request["automatic_resubmission_authorized"] = True
    _reseal(request)

    with pytest.raises(
        ValueError,
        match="automatic_resubmission_authorized=false",
    ):
        MODULE.validate_exit_settlement_single_execution_request(request)


def test_request_tool_has_no_signing_or_submission_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"settlement_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
