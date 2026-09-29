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
    / "build_phase7_controlled_live_exit_transaction_finalization.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_transaction_finalization",
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
TOKEN_X = "55555555555555555555555555555555"
TOKEN_Y = "66666666666666666666666666666666"


class _FakePreparation:
    @staticmethod
    def validate_exit_transaction_preparation(value):
        assert isinstance(value, dict)


class _FakeReadiness:
    @staticmethod
    def validate_exit_transaction_preparation_readiness(value):
        assert isinstance(value, dict)


def _preparation() -> dict:
    transaction = "cHJlcGFyYXRpb24tdHg="
    return {
        "preparation_sha256": "a" * 64,
        "saved_readiness_sha256": "b" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "opening_signature": "sig-open",
        "exit_decision_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "user_token_x": TOKEN_X,
        "user_token_y": TOKEN_Y,
        "transaction_base64": transaction,
        "transaction_sha256": MODULE._sha256_text(transaction),
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


def _readiness() -> dict:
    return {
        "readiness_sha256": "b" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "opening_signature": "sig-open",
        "decision_record_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "authorization_issued_at": "2026-09-29T10:01:30Z",
        "authorization_expires_at": "2026-09-29T10:03:30Z",
        "decision_expires_at": "2026-09-29T10:05:00Z",
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


def _finalizer_report(
    *,
    signed: bool = False,
    wallet: str = WALLET,
    simulation_succeeded: bool = True,
) -> dict:
    return {
        "accepted": True,
        "stage": "PRESIGN_READY",
        "reason": "risk_transaction_wallet_and_exact_simulation_passed",
        "risk": {
            "decision_id": EXIT_DECISION_ID,
            "mode": "LIVE",
            "action": "EXIT",
            "accepted": True,
            "reason": "approved",
        },
        "prepared": {
            "transaction_base64": "ZmluYWxpemVkLXR4",
            "recent_blockhash": "11111111111111111111111111111111",
            "last_valid_block_height": 999,
            "rpc_context_slot": 110,
            "signatures_all_default": not signed,
        },
        "transaction": {
            "accepted": True,
            "reason": "approved",
            "fee_payer": WALLET,
            "pool_account_present": True,
            "required_accounts_present": True,
            "instruction_count": 1,
            "static_account_count": 12,
            "required_signatures": 1,
            "signatures_all_default": not signed,
            "address_lookup_table_count": 0,
            "program_ids": [MODULE.DLMM_PROGRAM_ID],
            "instruction_fingerprints": [
                {
                    "program_id": MODULE.DLMM_PROGRAM_ID,
                    "data_prefix_hex": MODULE.REMOVE_LIQUIDITY2_PREFIX,
                }
            ],
        },
        "wallet": {
            "accepted": wallet == WALLET,
            "reason": "approved" if wallet == WALLET else "wrong_wallet",
            "wallet_pubkey": wallet,
            "transaction_fee_payer": WALLET,
        },
        "simulation": {
            "succeeded": simulation_succeeded,
            "rpc_context_slot": 111,
            "result": {
                "err": None if simulation_succeeded else {"custom": 1}
            },
        },
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    readiness_override: dict | None = None,
    preparation_override: dict | None = None,
    finalizer_report: dict | None = None,
    now: str = "2026-09-29T10:02:00Z",
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finalizer = root / "phase7-exit-finalizer"
        finalizer.write_bytes(b"reviewed-finalizer")
        finalizer.chmod(finalizer.stat().st_mode | stat.S_IXUSR)
        finalizer_sha = hashlib.sha256(finalizer.read_bytes()).hexdigest()

        preparation = _preparation()
        readiness = _readiness()
        if preparation_override:
            preparation.update(preparation_override)
        if readiness_override:
            readiness.update(readiness_override)
        prep_path = _write(root / "preparation.json", preparation)
        ready_path = _write(root / "readiness.json", readiness)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: (_FakePreparation, _FakeReadiness),
        )

        observed = {}

        def fake_run_finalizer(
            *,
            finalizer_binary,
            request_path,
            risk_config_path,
            guard_config_path,
            expected_wallet_pubkey,
            env,
        ):
            observed["env"] = env
            observed["request"] = json.loads(
                request_path.read_text(encoding="utf-8")
            )
            observed["risk"] = json.loads(
                risk_config_path.read_text(encoding="utf-8")
            )
            observed["guard"] = json.loads(
                guard_config_path.read_text(encoding="utf-8")
            )
            assert expected_wallet_pubkey == WALLET
            return finalizer_report or _finalizer_report()

        monkeypatch.setattr(MODULE, "_run_finalizer", fake_run_finalizer)

        report = MODULE.build_exit_transaction_finalization(
            source_tree=ROOT,
            saved_preparation_path=prep_path,
            expected_preparation_sha256=preparation["preparation_sha256"],
            saved_readiness_path=ready_path,
            expected_readiness_sha256=readiness["readiness_sha256"],
            finalizer_binary_path=finalizer,
            expected_finalizer_binary_sha256=finalizer_sha,
            rpc_url=RPC_URL,
            now=now,
        )
        return report, observed


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["finalization_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_finalization_refreshes_and_exact_simulates_without_authorizing(monkeypatch):
    report, observed = _build(monkeypatch)

    assert observed["request"]["proposal"]["action"] == "EXIT"
    assert observed["request"]["proposal"]["mode"] == "LIVE"
    assert observed["request"]["transaction_base64"] == (
        _preparation()["transaction_base64"]
    )
    assert observed["guard"]["expected_fee_payer"] == WALLET
    assert POSITION in observed["guard"]["required_account_pubkeys"]
    assert TOKEN_X in observed["guard"]["required_account_pubkeys"]
    assert TOKEN_Y in observed["guard"]["required_account_pubkeys"]

    assert report["final_transaction_sha256"] != (
        report["preparation_transaction_sha256"]
    )
    assert report["authorization_not_expired"] is True
    assert report["decision_not_expired"] is True
    assert report["final_transaction_guard_accepted"] is True
    assert report["final_transaction_unsigned"] is True
    assert report["expected_wallet_verified"] is True
    assert report["exact_simulation_succeeded"] is True
    assert report["exit_transaction_finalized"] is True
    assert report["exact_exit_transaction_authorization_required"] is True
    assert report["exit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_finalization_environment_strips_keypair_and_live_submit(monkeypatch):
    monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
    monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
    monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

    _, observed = _build(monkeypatch)
    env = observed["env"]

    assert MODULE.KEYPAIR_ENV not in env
    assert MODULE.LIVE_SUBMIT_ENV not in env
    assert "RPC_URL" not in env
    assert env["SOLANA_RPC_URL"] == RPC_URL


def test_expired_authorization_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="authorization has expired"):
        _build(
            monkeypatch,
            now="2026-09-29T10:03:31Z",
        )


def test_readiness_binding_mismatch_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="does not bind supplied readiness"):
        _build(
            monkeypatch,
            preparation_override={"saved_readiness_sha256": "f" * 64},
        )


def test_signed_final_transaction_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="contains signatures"):
        _build(
            monkeypatch,
            finalizer_report=_finalizer_report(signed=True),
        )


def test_wrong_expected_wallet_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="expected-wallet check failed"):
        _build(
            monkeypatch,
            finalizer_report=_finalizer_report(
                wallet="77777777777777777777777777777777"
            ),
        )


def test_failed_exact_simulation_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="exact simulation failed"):
        _build(
            monkeypatch,
            finalizer_report=_finalizer_report(
                simulation_succeeded=False
            ),
        )


def test_preparation_cannot_pre_authorize_exit(monkeypatch):
    with pytest.raises(
        ValueError,
        match="unexpectedly authorizes exit_authorized",
    ):
        _build(
            monkeypatch,
            preparation_override={"exit_authorized": True},
        )


def test_resealed_finalization_cannot_authorize_exit(monkeypatch):
    report, _ = _build(monkeypatch)
    report["exit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="exit_authorized=false"):
        MODULE.validate_exit_transaction_finalization(report)


def test_resealed_finalization_cannot_authorize_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_transaction_finalization(report)


def test_finalization_tool_has_no_signing_or_submission_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "load_executor_keypair" not in source
    assert "inspect_executor_wallet_from_env" not in source
    assert "controlled-live-submit" not in source
    assert "execution_store" not in source
    assert "record_final_presign" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"exit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
