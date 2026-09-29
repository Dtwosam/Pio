from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "execute_phase7_controlled_live_verified_exit_settlement_once.py"
)

SPEC = importlib.util.spec_from_file_location(
    "execute_phase7_controlled_live_verified_exit_settlement_once",
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
BLOCKHASH = "11111111111111111111111111111111"


class _FakeRequestModule:
    @staticmethod
    def validate_exit_settlement_single_execution_request(value):
        assert isinstance(value, dict)


class _FakeVerificationModule:
    @staticmethod
    def validate_settlement_signed_transaction_verification(value):
        assert isinstance(value, dict)


def _request() -> dict:
    return {
        "request_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": "e" * 64,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "final_settlement_transaction_sha256": "b" * 64,
        "recent_blockhash": BLOCKHASH,
        "last_valid_block_height": 1000,
        "fresh_current_block_height": 990,
        "single_execution_request_ready": True,
        "exact_settlement_authorization_verified": True,
        "keypair_identity_verified": True,
        "blockhash_not_expired": True,
        "final_transaction_unsigned": True,
        "exact_simulation_succeeded": True,
        "runtime_live_submit_opt_in_required": True,
        "dedicated_submission_journal_required": True,
        "atomic_first_submission_claim_required": True,
        "signature_persist_before_rpc_send_required": True,
        "rpc_max_retries_zero_required": True,
        "automatic_retry_prohibited": True,
        "uncertain_rpc_requires_recovery": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "separate_phase7_promotion_required": True,
    }


def _verification(signed_file_sha: str) -> dict:
    return {
        "verification_sha256": "c" * 64,
        "saved_request_sha256": "a" * 64,
        "destination_config_sha256": "e" * 64,
        "request_final_settlement_transaction_sha256": "b" * 64,
        "signed_transaction_file_sha256": signed_file_sha,
        "signed_transaction_sha256": "d" * 64,
        "signature": "sig-external",
        "executor_wallet_pubkey": WALLET,
        "recent_blockhash": BLOCKHASH,
        "exact_signed_settlement_transaction_verified": True,
        "signature_verified": True,
        "unsigned_message_matches_signed_message": True,
        "fee_payer_matches_executor": True,
        "blockhash_matches_request": True,
        "single_required_signature": True,
        "signed_transaction_has_one_signature": True,
        "signature_non_default": True,
        "verification_only": True,
        "transaction_signing_performed": False,
        "transaction_submission_attempted": False,
        "automatic_retry_performed": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


def _nested(*, accepted: bool = True) -> dict:
    return {
        "request_sha256": "a" * 64,
        "exit_decision_id": EXIT_DECISION_ID,
        "destination_config_sha256": "e" * 64,
        "final_settlement_transaction_sha256": "b" * 64,
        "signed_transaction_sha256": "d" * 64,
        "signature": "sig-external",
        "journal_status": "RPC_ACCEPTED" if accepted else "RPC_UNCERTAIN",
        "journal_claim_persisted_before_rpc_send": True,
        "current_block_height": 991,
        "last_valid_block_height": 1000,
        "blockhash_not_expired_at_execution": True,
        "rpc_max_retries": 0,
        "automatic_retry_performed": False,
        "exact_signed_transaction_verified": True,
        "signing_performed": False,
        "rpc_accepted": accepted,
        "rpc_error": None if accepted else "network outcome unknown",
        "uncertain_rpc_requires_recovery": True,
        "submission_attempted_once": True,
    }


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    accepted: bool = True,
    request_override: dict | None = None,
    verification_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        signed = root / "signed.base64"
        signed.write_text("signed-transaction", encoding="utf-8")
        signed_file_sha = hashlib.sha256(signed.read_bytes()).hexdigest()

        request = _request()
        verification = _verification(signed_file_sha)
        if request_override:
            request.update(request_override)
        if verification_override:
            verification.update(verification_override)

        request_path = _write_json(root / "request.json", request)
        verification_path = _write_json(
            root / "verification.json",
            verification,
        )

        submitter = root / "phase7-exit-submit-verified-once"
        submitter.write_bytes(b"reviewed-submitter")
        submitter.chmod(submitter.stat().st_mode | stat.S_IXUSR)
        submitter_sha = hashlib.sha256(submitter.read_bytes()).hexdigest()

        pio = root / "pio.db"
        pio.write_bytes(b"production-pio-state")
        journal = root / "exit-journal.db"

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakeRequestModule, _FakeVerificationModule),
        )
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")

        observed = {}

        def fake_run_submitter(
            *,
            submitter_binary,
            request_path,
            signed_transaction_path,
            journal_path,
            env,
        ):
            observed["env"] = env
            observed["submitter"] = submitter_binary
            observed["signed"] = signed_transaction_path
            observed["journal"] = journal_path
            journal_path.write_bytes(
                b"journal-after-accepted"
                if accepted
                else b"journal-after-uncertain"
            )
            return (0 if accepted else 2), _nested(accepted=accepted)

        monkeypatch.setattr(MODULE, "_run_submitter", fake_run_submitter)

        report = MODULE.build_verified_single_execution(
            source_tree=ROOT,
            saved_request_path=request_path,
            expected_request_sha256=request["request_sha256"],
            saved_signed_verification_path=verification_path,
            expected_signed_verification_sha256=verification[
                "verification_sha256"
            ],
            signed_transaction_path=signed,
            submitter_binary_path=submitter,
            expected_submitter_binary_sha256=submitter_sha,
            rpc_url=RPC_URL,
            submission_journal_path=journal,
            pio_database_path=pio,
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["execution_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_accepted_execution_uses_verified_bytes_without_signing(monkeypatch):
    report, observed = _build(monkeypatch, accepted=True)

    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert observed["env"][MODULE.LIVE_SUBMIT_ENV] == "1"
    assert observed["env"]["SOLANA_RPC_URL"] == RPC_URL
    assert report["signed_transaction_sha256"] == "d" * 64
    assert report["signature"] == "sig-external"
    assert report["journal_claim_persisted_before_rpc_send"] is True
    assert report["execution_current_block_height"] == 991
    assert report["rpc_max_retries"] == 0
    assert report["automatic_retry_performed"] is False
    assert report["exact_signed_transaction_verified"] is True
    assert report["transaction_signing_performed"] is False
    assert report["submission_attempted_once"] is True
    assert report["rpc_accepted"] is True
    assert report["journal_status"] == "RPC_ACCEPTED"
    assert report["production_pio_database_unchanged"] is True
    assert report["live_capital_movement_confirmed"] is False


def test_uncertain_rpc_is_preserved_for_separate_recovery(monkeypatch):
    report, _ = _build(monkeypatch, accepted=False)

    assert report["submission_process_returncode"] == 2
    assert report["rpc_accepted"] is False
    assert report["journal_status"] == "RPC_UNCERTAIN"
    assert report["rpc_error"] == "network outcome unknown"
    assert report["uncertain_rpc_requires_recovery"] is True
    assert report["automatic_retry_performed"] is False
    assert report["requires_post_settlement_confirmation"] is True


def test_live_submit_opt_in_is_required(monkeypatch):
    monkeypatch.delenv(MODULE.LIVE_SUBMIT_ENV, raising=False)

    with pytest.raises(ValueError, match="required for verified EXIT"):
        MODULE._execution_env(rpc_url=RPC_URL)


def test_signed_transaction_file_must_match_verification(monkeypatch):
    with pytest.raises(ValueError, match="differs from sealed verification"):
        _build(
            monkeypatch,
            verification_override={
                "signed_transaction_file_sha256": "f" * 64
            },
        )


def test_rpc_endpoint_must_match_request(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            request_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_verification_request_binding_must_match(monkeypatch):
    with pytest.raises(ValueError, match="saved_request_sha256 binding mismatch"):
        _build(
            monkeypatch,
            verification_override={"saved_request_sha256": "f" * 64},
        )


def test_resealed_report_cannot_claim_signing(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_signing_performed"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_signing_performed=false",
    ):
        MODULE.validate_verified_single_execution(report)


def test_resealed_report_cannot_claim_confirmation(monkeypatch):
    report, _ = _build(monkeypatch)
    report["live_capital_movement_confirmed"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="cannot claim confirmed live capital movement",
    ):
        MODULE.validate_verified_single_execution(report)


def test_execution_wrapper_has_no_keypair_or_retry_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "PIO_EXECUTOR_KEYPAIR" in source
    assert "env.pop(KEYPAIR_ENV, None)" in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "max_retries: Some(" not in source
    assert "send_and_confirm" not in source
    assert "for attempt" not in source
    assert "while True" not in source
