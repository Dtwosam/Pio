from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "prove_phase7_controlled_live_exit_settlement_account_absent.py"
)

SPEC = importlib.util.spec_from_file_location(
    "prove_phase7_controlled_live_exit_settlement_account_absent",
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


class _FakeReceiptModule:
    @staticmethod
    def validate_exit_settlement_terminal_receipt(value):
        assert isinstance(value, dict)


def _receipt() -> dict:
    return {
        "receipt_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "destination_config_sha256": "b" * 64,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "signature": "settlement-signature",
        "transaction_slot": 200,
        "terminal_receipt_status": "CONFIRMED",
        "transaction_succeeded": True,
        "terminal_receipt_ready": True,
        "confirmation_matches_transaction_snapshot": True,
        "settlement_events_match_request": True,
        "position_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "transaction_submission_attempted": False,
        "automatic_resubmission_performed": False,
        "live_capital_effect_reconciled": False,
        "phase7_promotion_persisted": False,
        "production_pio_database_modified": False,
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    receipt_override: dict | None = None,
    closed: bool = True,
    closure_slot: int = 201,
    closure_position: str = POSITION,
    returncode: int | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        receipt = _receipt()
        if receipt_override:
            receipt.update(receipt_override)
        receipt_path = _write(root / "receipt.json", receipt)

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_receipt_module",
            lambda source: _FakeReceiptModule,
        )
        observed = {}

        def fake_run_closure(
            *,
            executor_binary,
            rpc_url,
            position_address,
            env,
        ):
            observed["binary"] = executor_binary
            observed["rpc_url"] = rpc_url
            observed["position"] = position_address
            observed["env"] = env
            rc = (0 if closed else 2) if returncode is None else returncode
            return (
                rc,
                {
                    "position": closure_position,
                    "closed": closed,
                    "rpc_context_slot": closure_slot,
                },
            )

        monkeypatch.setattr(MODULE, "_run_closure", fake_run_closure)
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        monkeypatch.setenv("SOLANA_RPC_URL", "https://env.invalid")
        monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

        report = MODULE.build_exit_settlement_account_absence_proof(
            source_tree=ROOT,
            saved_terminal_receipt_path=receipt_path,
            expected_terminal_receipt_sha256=receipt["receipt_sha256"],
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            rpc_url=RPC_URL,
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["proof_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_confirmed_account_absence_opens_reconciliation_only(monkeypatch):
    report, observed = _build(monkeypatch)

    assert observed["rpc_url"] == RPC_URL
    assert observed["position"] == POSITION
    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert MODULE.LIVE_SUBMIT_ENV not in observed["env"]
    assert "SOLANA_RPC_URL" not in observed["env"]
    assert "RPC_URL" not in observed["env"]

    assert report["position_account_absent"] is True
    assert report["closure_rpc_context_slot"] == 201
    assert report["closure_observation_at_or_after_settlement"] is True
    assert report["position_account_absence_proven"] is True
    assert report["post_exit_state_reconciliation_ready"] is True
    assert report["live_capital_effect_reconciled"] is False
    assert report["phase7_promotion_authorized"] is False
    assert report["phase7_promotion_persisted"] is False


def test_position_still_present_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="still present"):
        _build(monkeypatch, closed=False)


def test_stale_account_absence_observation_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="predates settlement"):
        _build(monkeypatch, closure_slot=199)


def test_wrong_position_binding_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="position mismatch"):
        _build(
            monkeypatch,
            closure_position="77777777777777777777777777777777",
        )


def test_unconfirmed_receipt_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="confirmed receipt"):
        _build(
            monkeypatch,
            receipt_override={
                "terminal_receipt_status": "FAILED",
                "transaction_succeeded": False,
                "position_account_absence_proof_required": False,
                "post_exit_state_reconciliation_required": False,
            },
        )


def test_rpc_endpoint_must_match_receipt(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            receipt_override={
                "rpc_endpoint_sha256": "f" * 64,
            },
        )


def test_resealed_absence_proof_cannot_claim_reconciliation_complete(
    monkeypatch,
):
    report, _ = _build(monkeypatch)
    report["live_capital_effect_reconciled"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="live_capital_effect_reconciled=false",
    ):
        MODULE.validate_exit_settlement_account_absence_proof(report)


def test_resealed_absence_proof_cannot_authorize_promotion(monkeypatch):
    report, _ = _build(monkeypatch)
    report["phase7_promotion_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="phase7_promotion_authorized=false",
    ):
        MODULE.validate_exit_settlement_account_absence_proof(report)


def test_account_absence_tool_is_read_only():
    source = TOOL.read_text(encoding="utf-8")

    assert 'CLOSURE_COMMAND = "verify-position-closed"' in source
    assert "env.pop(KEYPAIR_ENV, None)" in source
    assert "env.pop(LIVE_SUBMIT_ENV, None)" in source
    assert "env.pop(\"SOLANA_RPC_URL\", None)" in source

    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"transaction_submission_attempted": False' in source
    assert '"automatic_resubmission_performed": False' in source
    assert '"live_capital_effect_reconciled": False' in source
