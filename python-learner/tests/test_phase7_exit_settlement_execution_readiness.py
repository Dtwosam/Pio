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
    / "check_phase7_controlled_live_exit_settlement_execution_readiness.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_settlement_execution_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"
OPENED_DECISION_ID = "11111111-2222-4333-8444-555555555555"
EXIT_DECISION_ID = "01234567-89ab-4def-8123-456789abcdef"
POOL = "11111111111111111111111111111111"
POSITION = "33333333333333333333333333333333"
WALLET = "44444444444444444444444444444444"
USER_X = "55555555555555555555555555555555"
USER_Y = "66666666666666666666666666666666"
REWARD = "77777777777777777777777777777777"


class _FakeFinalization:
    @staticmethod
    def validate_exit_settlement_finalization(value):
        assert isinstance(value, dict)


class _FakeSigner:
    fresh = None

    @staticmethod
    def validate_verification(value):
        assert isinstance(value, dict)

    @classmethod
    def verify_authorization(cls, **kwargs):
        assert cls.fresh is not None
        return dict(cls.fresh)


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


def _finalization() -> dict:
    return {
        "finalization_sha256": "a" * 64,
        "saved_preparation_sha256": "b" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "exit_signature": "sig-confirmed-exit",
        "zero_liquidity_snapshot_sha256": "c" * 64,
        "zero_liquidity_capture_slot_start": 130,
        "zero_liquidity_capture_slot_end": 130,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "destination_config_sha256": "d" * 64,
        "user_token_x": USER_X,
        "user_token_y": USER_Y,
        "reward_token_destinations": [
            {"reward_index": 0, "user_token_account": REWARD}
        ],
        "final_settlement_transaction_sha256": "e" * 64,
        "recent_blockhash": "11111111111111111111111111111111",
        "last_valid_block_height": 999,
        "exact_simulation_sha256": "f" * 64,
        "final_guard_sha256": "0" * 64,
        "final_wallet_authorization_sha256": "1" * 64,
        "finalizer_binary_sha256": "2" * 64,
        "final_risk_accepted": True,
        "final_transaction_guard_accepted": True,
        "final_guard_instruction_sequence_valid": True,
        "final_transaction_unsigned": True,
        "expected_wallet_verified": True,
        "exact_simulation_succeeded": True,
        "settlement_transaction_finalized": True,
        "exact_settlement_transaction_authorization_required": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "single_submission_only_required": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        **_false_boundaries(),
    }


def _verification() -> dict:
    finalization = _finalization()
    return {
        "verification_sha256": "3" * 64,
        "finalization_sha256": finalization["finalization_sha256"],
        "preparation_sha256": finalization["saved_preparation_sha256"],
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "exit_signature": finalization["exit_signature"],
        "zero_liquidity_snapshot_sha256": finalization[
            "zero_liquidity_snapshot_sha256"
        ],
        "zero_liquidity_capture_slot_start": 130,
        "zero_liquidity_capture_slot_end": 130,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": finalization["rpc_endpoint_sha256"],
        "destination_config_sha256": finalization[
            "destination_config_sha256"
        ],
        "user_token_x": USER_X,
        "user_token_y": USER_Y,
        "reward_token_destinations": finalization[
            "reward_token_destinations"
        ],
        "final_settlement_transaction_sha256": finalization[
            "final_settlement_transaction_sha256"
        ],
        "recent_blockhash": finalization["recent_blockhash"],
        "last_valid_block_height": 999,
        "exact_simulation_sha256": finalization[
            "exact_simulation_sha256"
        ],
        "final_guard_sha256": finalization["final_guard_sha256"],
        "final_wallet_authorization_sha256": finalization[
            "final_wallet_authorization_sha256"
        ],
        "finalizer_binary_sha256": finalization["finalizer_binary_sha256"],
        "issued_at": "2026-09-29T15:30:10Z",
        "expires_at": "2026-09-29T15:31:10Z",
        "signature_verified": True,
        "authorization_not_expired": True,
        "human_exact_settlement_transaction_authorization_verified": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "final_settlement_execution_gate_required": True,
        "single_submission_only_required": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exact_transaction_authorization_present": True,
        **_false_boundaries(),
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    finalization_override: dict | None = None,
    verification_override: dict | None = None,
    fresh_override: dict | None = None,
    block_height: int = 950,
    now: str = "2026-09-29T15:30:30Z",
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finalization = _finalization()
        verification = _verification()
        if finalization_override:
            finalization.update(finalization_override)
        if verification_override:
            verification.update(verification_override)
        fresh = dict(verification)
        if fresh_override:
            fresh.update(fresh_override)

        finalization_path = _write(
            root / "finalization.json",
            finalization,
        )
        verification_path = _write(
            root / "verification.json",
            verification,
        )
        request_path = root / "request.json"
        payload_path = root / "payload.json"
        signature_path = root / "signature"
        allowed_path = root / "allowed"
        for path in (
            request_path,
            payload_path,
            signature_path,
            allowed_path,
        ):
            path.write_text("fixture", encoding="utf-8")

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeFinalization, _FakeSigner),
        )
        monkeypatch.setattr(
            MODULE,
            "_rpc_block_height",
            lambda rpc_url: block_height,
        )
        _FakeSigner.fresh = fresh

        report = MODULE.build_exit_settlement_execution_readiness(
            source_tree=ROOT,
            saved_finalization_path=finalization_path,
            expected_finalization_sha256=finalization[
                "finalization_sha256"
            ],
            saved_authorization_verification_path=verification_path,
            expected_saved_authorization_verification_sha256=verification[
                "verification_sha256"
            ],
            authorization_request_path=request_path,
            authorization_payload_path=payload_path,
            authorization_signature_path=signature_path,
            authorization_allowed_signers_path=allowed_path,
            expected_authorization_allowed_signers_sha256="4" * 64,
            rpc_url=RPC_URL,
            now=now,
        )
        return report


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["readiness_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_readiness_reverifies_authorization_and_blockhash(monkeypatch):
    report = _build(monkeypatch)

    assert report["authorization_verification_matches_saved"] is True
    assert report["current_block_height"] == 950
    assert report["last_valid_block_height"] == 999
    assert report["block_height_remaining"] == 49
    assert report["blockhash_not_expired"] is True
    assert report[
        "human_exact_settlement_transaction_authorization_verified"
    ] is True
    assert report["settlement_execution_readiness_ready"] is True
    assert report["readiness_only"] is True
    assert report["requires_keypair_identity_admission"] is True
    assert report["settlement_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_fresh_authorization_must_equal_saved_verification(monkeypatch):
    with pytest.raises(ValueError, match="differs from saved verification"):
        _build(
            monkeypatch,
            fresh_override={"verification_sha256": "9" * 64},
        )


def test_blockhash_expiry_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="blockhash has expired"):
        _build(monkeypatch, block_height=1000)


def test_authorization_expiry_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="authorization has expired"):
        _build(
            monkeypatch,
            now="2026-09-29T15:31:11Z",
        )


def test_destination_config_binding_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="destination_config_sha256 binding mismatch",
    ):
        _build(
            monkeypatch,
            verification_override={"destination_config_sha256": "8" * 64},
            fresh_override={"destination_config_sha256": "8" * 64},
        )


def test_zero_liquidity_lineage_drift_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="zero_liquidity_snapshot_sha256 binding mismatch",
    ):
        _build(
            monkeypatch,
            verification_override={
                "zero_liquidity_snapshot_sha256": "8" * 64
            },
            fresh_override={
                "zero_liquidity_snapshot_sha256": "8" * 64
            },
        )


def test_rpc_endpoint_must_match_finalization(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            finalization_override={"rpc_endpoint_sha256": "8" * 64},
        )


def test_resealed_readiness_cannot_authorize_signing(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_exit_settlement_execution_readiness(report)


def test_resealed_readiness_cannot_authorize_submission(monkeypatch):
    report = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_settlement_execution_readiness(report)


def test_settlement_readiness_has_no_keypair_or_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "subprocess" not in source
    assert '"settlement_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
