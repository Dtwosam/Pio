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
    / "build_phase7_controlled_live_exit_settlement_finalization.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_settlement_finalization",
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
REWARD_0 = "77777777777777777777777777777777"


class _FakePreparationModule:
    @staticmethod
    def validate_exit_settlement_preparation(value):
        assert isinstance(value, dict)


def _preparation() -> dict:
    return {
        "preparation_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "exit_signature": "sig-confirmed-exit",
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "destination_config_sha256": "b" * 64,
        "user_token_x": USER_X,
        "user_token_y": USER_Y,
        "reward_token_destinations": [
            {"reward_index": 0, "user_token_account": REWARD_0}
        ],
        "reward_indices_claimed": [0],
        "settlement_transaction_base64": "cHJlcGFyZWQtc2V0dGxlbWVudA==",
        "settlement_transaction_sha256": "c" * 64,
        "settlement_prepared": True,
        "fresh_blockhash_exact_finalization_required": True,
        "exact_settlement_transaction_authorization_required": True,
        "single_submission_only_required": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "settlement_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_persisted": False,
        "production_pio_database_modified": False,
    }


def _finalizer_report(
    *,
    signed: bool = False,
    simulation_succeeded: bool = True,
    wrong_sequence: bool = False,
) -> dict:
    prefixes = [
        MODULE.CLAIM_FEE2_PREFIX,
        MODULE.CLAIM_REWARD2_PREFIX,
        MODULE.CLOSE_POSITION2_PREFIX,
    ]
    if wrong_sequence:
        prefixes[-1] = MODULE.CLAIM_FEE2_PREFIX
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
            "transaction_base64": "ZmluYWwtc2V0dGxlbWVudA==",
            "recent_blockhash": "11111111111111111111111111111111",
            "last_valid_block_height": 999,
            "rpc_context_slot": 140,
            "signatures_all_default": not signed,
        },
        "transaction": {
            "accepted": True,
            "reason": "approved",
            "fee_payer": WALLET,
            "pool_account_present": True,
            "required_accounts_present": True,
            "instruction_count": 3,
            "static_account_count": 20,
            "required_signatures": 1,
            "signatures_all_default": not signed,
            "address_lookup_table_count": 0,
            "program_ids": [MODULE.DLMM_PROGRAM_ID],
            "instruction_fingerprints": [
                {
                    "program_id": MODULE.DLMM_PROGRAM_ID,
                    "data_prefix_hex": prefix,
                }
                for prefix in prefixes
            ],
        },
        "wallet": {
            "accepted": True,
            "reason": "approved",
            "wallet_pubkey": WALLET,
            "transaction_fee_payer": WALLET,
        },
        "simulation": {
            "succeeded": simulation_succeeded,
            "rpc_context_slot": 141,
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
    preparation_override: dict | None = None,
    finalizer_report: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        preparation = _preparation()
        if preparation_override:
            preparation.update(preparation_override)
        preparation_path = _write(
            root / "preparation.json",
            preparation,
        )

        finalizer = root / "phase7-exit-finalizer"
        finalizer.write_bytes(b"reviewed-finalizer")
        finalizer.chmod(finalizer.stat().st_mode | stat.S_IXUSR)
        finalizer_sha = hashlib.sha256(finalizer.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_preparation_module",
            lambda source: _FakePreparationModule,
        )
        observed = {}

        def fake_run_finalizer(
            *,
            binary,
            request_path,
            risk_path,
            guard_path,
            expected_wallet,
            env,
        ):
            observed["env"] = env
            observed["request"] = json.loads(
                request_path.read_text(encoding="utf-8")
            )
            observed["guard"] = json.loads(
                guard_path.read_text(encoding="utf-8")
            )
            observed["expected_wallet"] = expected_wallet
            return finalizer_report or _finalizer_report()

        monkeypatch.setattr(
            MODULE,
            "_run_finalizer",
            fake_run_finalizer,
        )
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

        report = MODULE.build_exit_settlement_finalization(
            source_tree=ROOT,
            saved_preparation_path=preparation_path,
            expected_preparation_sha256=preparation[
                "preparation_sha256"
            ],
            finalizer_binary_path=finalizer,
            expected_finalizer_binary_sha256=finalizer_sha,
            rpc_url=RPC_URL,
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


def test_settlement_finalization_refreshes_without_keypair(monkeypatch):
    report, observed = _build(monkeypatch)

    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert MODULE.LIVE_SUBMIT_ENV not in observed["env"]
    assert "RPC_URL" not in observed["env"]
    assert observed["env"]["SOLANA_RPC_URL"] == RPC_URL
    assert observed["expected_wallet"] == WALLET
    assert observed["request"]["proposal"]["action"] == "EXIT"
    assert POSITION in observed["guard"]["required_account_pubkeys"]
    assert USER_X in observed["guard"]["required_account_pubkeys"]
    assert USER_Y in observed["guard"]["required_account_pubkeys"]
    assert REWARD_0 in observed["guard"]["required_account_pubkeys"]

    assert report["final_transaction_unsigned"] is True
    assert report["expected_wallet_verified"] is True
    assert report["exact_simulation_succeeded"] is True
    assert report["final_guard_instruction_sequence_valid"] is True
    assert report["settlement_transaction_finalized"] is True
    assert report["exact_settlement_transaction_authorization_required"] is True
    assert report["settlement_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_presigned_final_settlement_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="contains signatures"):
        _build(
            monkeypatch,
            finalizer_report=_finalizer_report(signed=True),
        )


def test_wrong_final_instruction_sequence_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="instruction sequence mismatch"):
        _build(
            monkeypatch,
            finalizer_report=_finalizer_report(wrong_sequence=True),
        )


def test_failed_exact_simulation_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="exact simulation failed"):
        _build(
            monkeypatch,
            finalizer_report=_finalizer_report(
                simulation_succeeded=False
            ),
        )


def test_preparation_cannot_pre_authorize_settlement(monkeypatch):
    with pytest.raises(
        ValueError,
        match="refuses preparation settlement_authorized=true",
    ):
        _build(
            monkeypatch,
            preparation_override={"settlement_authorized": True},
        )


def test_rpc_endpoint_must_match_preparation(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            preparation_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_resealed_finalization_cannot_authorize_settlement(monkeypatch):
    report, _ = _build(monkeypatch)
    report["settlement_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="settlement_authorized=false"):
        MODULE.validate_exit_settlement_finalization(report)


def test_resealed_finalization_cannot_authorize_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_settlement_finalization(report)


def test_settlement_finalization_has_no_signing_or_submission_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "env.pop(KEYPAIR_ENV, None)" in source
    assert "env.pop(LIVE_SUBMIT_ENV, None)" in source
    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"settlement_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
