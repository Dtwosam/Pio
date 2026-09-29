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
    / "check_phase7_controlled_live_exit_submission_confirmation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_phase7_controlled_live_exit_submission_confirmation",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RPC_URL = "https://rpc.example.invalid"


class _FakeExecutionModule:
    @staticmethod
    def validate_verified_single_execution(value):
        assert isinstance(value, dict)


def _execution() -> dict:
    return {
        "execution_sha256": "a" * 64,
        "opened_decision_id": "11111111-2222-4333-8444-555555555555",
        "exit_decision_id": "01234567-89ab-4def-8123-456789abcdef",
        "pool_address": "11111111111111111111111111111111",
        "position_address": "33333333333333333333333333333333",
        "executor_wallet_pubkey": "44444444444444444444444444444444",
        "signed_transaction_sha256": "b" * 64,
        "signature": "sig-external",
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "rpc_accepted": True,
        "journal_status": "RPC_ACCEPTED",
        "submission_attempted_once": True,
        "exact_signed_transaction_verified": True,
        "automatic_retry_performed": False,
        "transaction_signing_performed": False,
        "production_pio_database_modified": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    rpc_status,
    execution_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        execution = _execution()
        if execution_override:
            execution.update(execution_override)
        path = _write(root / "execution.json", execution)

        monkeypatch.setattr(
            MODULE,
            "_load_execution_module",
            lambda source: _FakeExecutionModule,
        )
        monkeypatch.setattr(
            MODULE,
            "_rpc_signature_status",
            lambda rpc_url, signature: rpc_status,
        )

        return MODULE.build_exit_submission_confirmation(
            source_tree=ROOT,
            saved_execution_path=path,
            expected_execution_sha256=execution["execution_sha256"],
            rpc_url=RPC_URL,
        )


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["confirmation_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_execution_dependency_is_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_pending_confirmation_requires_only_recheck(monkeypatch):
    report = _build(monkeypatch, rpc_status=None)

    assert report["observation_status"] == "PENDING"
    assert report["confirmation_pending"] is True
    assert report["confirmation_recheck_required"] is True
    assert report["transaction_chain_confirmation_observed"] is False
    assert report["transaction_failure_observed"] is False
    assert report["failed_execution_recovery_required"] is False
    assert report["zero_liquidity_proof_required"] is False
    assert report["separate_position_close_required"] is False


def test_confirmed_signature_opens_settlement_path(monkeypatch):
    report = _build(
        monkeypatch,
        rpc_status={
            "slot": 123,
            "err": None,
            "confirmationStatus": "confirmed",
        },
    )

    assert report["observation_status"] == "CONFIRMED"
    assert report["transaction_chain_confirmation_observed"] is True
    assert report["confirmation_pending"] is False
    assert report["failed_execution_recovery_required"] is False
    assert report["liquidity_removal_effect_reconciliation_required"] is True
    assert report["zero_liquidity_proof_required"] is True
    assert report["separate_position_close_required"] is True
    assert report["post_close_account_absence_proof_required"] is True
    assert report["post_exit_state_reconciliation_required"] is True


def test_failed_signature_routes_to_recovery_not_settlement(monkeypatch):
    report = _build(
        monkeypatch,
        rpc_status={
            "slot": 123,
            "err": {"InstructionError": [0, "Custom"]},
            "confirmationStatus": "confirmed",
        },
    )

    assert report["observation_status"] == "FAILED"
    assert report["transaction_failure_observed"] is True
    assert report["failed_execution_recovery_required"] is True
    assert report["confirmation_recheck_required"] is False
    assert report["zero_liquidity_proof_required"] is False
    assert report["separate_position_close_required"] is False


def test_processed_signature_stays_pending(monkeypatch):
    report = _build(
        monkeypatch,
        rpc_status={
            "slot": 123,
            "err": None,
            "confirmationStatus": "processed",
        },
    )

    assert report["observation_status"] == "PENDING"
    assert report["confirmation_recheck_required"] is True


def test_rpc_endpoint_must_match_execution(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            rpc_status=None,
            execution_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_automatic_retry_execution_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="automatically retried"):
        _build(
            monkeypatch,
            rpc_status=None,
            execution_override={"automatic_retry_performed": True},
        )


def test_resealed_pending_report_cannot_enable_settlement(monkeypatch):
    report = _build(monkeypatch, rpc_status=None)
    report["zero_liquidity_proof_required"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="binding mismatch"):
        MODULE.validate_exit_submission_confirmation(report)


def test_resealed_failed_report_cannot_disable_recovery(monkeypatch):
    report = _build(
        monkeypatch,
        rpc_status={
            "slot": 123,
            "err": {"InstructionError": [0, "Custom"]},
            "confirmationStatus": "confirmed",
        },
    )
    report["failed_execution_recovery_required"] = False
    _reseal(report)

    with pytest.raises(ValueError, match="recovery flag mismatch"):
        MODULE.validate_exit_submission_confirmation(report)


def test_confirmation_tool_is_read_only_and_non_resubmitting():
    source = TOOL.read_text(encoding="utf-8")

    assert "getSignatureStatuses" in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "PIO_LIVE_SUBMIT_ENABLED" not in source
    assert "subprocess" not in source
    assert '"automatic_resubmission_performed": False' in source
    assert '"read_only": True' in source
