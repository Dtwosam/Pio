from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_phase7_controlled_live_exit_transaction_preparation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_transaction_preparation",
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
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def _readiness(binary_sha: str) -> dict:
    return {
        "readiness_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "opening_signature": "sig-open",
        "decision_record_id": EXIT_DECISION_ID,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "executor_binary_sha256": binary_sha,
        "fresh_capture_slot_start": 104,
        "fresh_capture_slot_end": 105,
        "human_exit_authorization_verified": True,
        "position_closed_proven": False,
        "position_still_open": True,
        "exit_transaction_preparation_readiness_ready": True,
        "unsigned_exit_transaction_construction_required": True,
        "fresh_chain_resolution_required": True,
        "fresh_simulation_required": True,
        "separate_exit_transaction_authorization_required": True,
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


class _FakeReadiness:
    @staticmethod
    def validate_exit_transaction_preparation_readiness(value):
        assert isinstance(value, dict)


def _resolution(
    *,
    owner_matches: bool = True,
    token_x_valid: bool = True,
    token_y_valid: bool = True,
) -> dict:
    return {
        "validation": {
            "position": POSITION,
            "lb_pair": POOL,
            "owner_matches_sender": owner_matches,
            "token_x_program": TOKEN_PROGRAM,
            "token_y_program": TOKEN_PROGRAM,
            "token_x_is_2022": False,
            "token_y_is_2022": False,
            "user_token_x_valid": token_x_valid,
            "user_token_y_valid": token_y_valid,
            "removal_bin_count": 2,
            "transfer_hook_account_count": 0,
        },
        "build": {
            "transaction_base64": "dGVzdC10cmFuc2FjdGlvbg==",
            "program_id": MODULE.DLMM_PROGRAM_ID,
            "position": POSITION,
            "lb_pair": POOL,
            "sender": WALLET,
            "removes_all_position_liquidity": True,
            "removal_bin_count": 2,
            "transfer_hook_account_count": 0,
            "claims_fees_or_rewards": False,
            "closes_position_account": False,
        },
    }


def _guard(*, accepted: bool = True, unsigned: bool = True) -> dict:
    return {
        "accepted": accepted,
        "reason": "approved" if accepted else "rejected",
        "fee_payer": WALLET,
        "pool_account_present": True,
        "required_accounts_present": True,
        "instruction_count": 1,
        "static_account_count": 12,
        "required_signatures": 1,
        "signatures_all_default": unsigned,
        "address_lookup_table_count": 0,
        "program_ids": [MODULE.DLMM_PROGRAM_ID],
        "instruction_fingerprints": [
            {
                "program_id": MODULE.DLMM_PROGRAM_ID,
                "data_prefix_hex": MODULE.REMOVE_LIQUIDITY2_PREFIX,
            }
        ],
    }


def _simulation(*, succeeded: bool = True, slot: int = 106) -> dict:
    return {
        "succeeded": succeeded,
        "rpc_context_slot": slot,
        "result": {"err": None if succeeded else {"custom": 1}},
    }


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    resolution: dict | None = None,
    guard: dict | None = None,
    simulation: dict | None = None,
    readiness_override: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        readiness = _readiness(binary_sha)
        if readiness_override:
            readiness.update(readiness_override)
        readiness_path = _write(root / "readiness.json", readiness)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: _FakeReadiness,
        )

        calls: list[str] = []

        def fake_run_json(*, executor_binary, args, env, stdin_text=None):
            calls.append(args[0])
            assert MODULE.KEYPAIR_ENV not in env
            assert MODULE.LIVE_SUBMIT_ENV not in env
            assert "RPC_URL" not in env
            assert env["SOLANA_RPC_URL"] == RPC_URL
            if args[0] == MODULE.BUILD_COMMAND:
                request = json.loads(stdin_text)
                assert request == {
                    "position": POSITION,
                    "user_token_x": TOKEN_X,
                    "user_token_y": TOKEN_Y,
                    "sender": WALLET,
                }
                return resolution or _resolution()
            if args[0] == MODULE.GUARD_COMMAND:
                return guard or _guard()
            if args[0] == MODULE.SIMULATION_COMMAND:
                assert stdin_text == "dGVzdC10cmFuc2FjdGlvbg=="
                return simulation or _simulation()
            raise AssertionError(args[0])

        monkeypatch.setattr(MODULE, "_run_json", fake_run_json)

        report = MODULE.build_exit_transaction_preparation(
            source_tree=ROOT,
            saved_readiness_path=readiness_path,
            expected_readiness_sha256=readiness["readiness_sha256"],
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            rpc_url=RPC_URL,
            user_token_x=TOKEN_X,
            user_token_y=TOKEN_Y,
        )
        return report, calls


def _reseal(report: dict) -> None:
    identity = {field: report[field] for field in MODULE.REPORT_FIELDS}
    report["preparation_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_dependencies_are_exactly_pinned():
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        path = ROOT / relative
        assert path.is_file()
        assert MODULE._git_blob_sha(path) == expected


def test_unsigned_exit_preparation_is_guarded_simulated_and_non_authorizing(
    monkeypatch,
):
    report, calls = _build(monkeypatch)

    assert calls == [
        MODULE.BUILD_COMMAND,
        MODULE.GUARD_COMMAND,
        MODULE.SIMULATION_COMMAND,
    ]
    assert report["exit_decision_id"] == EXIT_DECISION_ID
    assert report["position_address"] == POSITION
    assert report["pool_address"] == POOL
    assert report["validation_owner_matches_sender"] is True
    assert report["validation_user_token_x_valid"] is True
    assert report["validation_user_token_y_valid"] is True
    assert report["removal_bin_count"] == 2
    assert report["transaction_guard_accepted"] is True
    assert report["guard_transaction_unsigned"] is True
    assert report["simulation_succeeded"] is True
    assert report["exit_transaction_prepared"] is True
    assert report["exact_exit_transaction_authorization_required"] is True
    assert report["exit_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_execution_environment_strips_keypair_and_live_submit(monkeypatch):
    monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
    monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
    monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

    env = MODULE._execution_env(rpc_url=RPC_URL)

    assert MODULE.KEYPAIR_ENV not in env
    assert MODULE.LIVE_SUBMIT_ENV not in env
    assert "RPC_URL" not in env
    assert env["SOLANA_RPC_URL"] == RPC_URL


def test_rpc_endpoint_must_match_readiness(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        readiness = _readiness(binary_sha)
        readiness["rpc_endpoint_sha256"] = "f" * 64
        readiness_path = _write(root / "readiness.json", readiness)

        monkeypatch.setattr(
            MODULE,
            "_load_reviewed",
            lambda source: _FakeReadiness,
        )
        with pytest.raises(ValueError, match="RPC endpoint mismatch"):
            MODULE.build_exit_transaction_preparation(
                source_tree=ROOT,
                saved_readiness_path=readiness_path,
                expected_readiness_sha256=readiness["readiness_sha256"],
                executor_binary_path=binary,
                expected_executor_binary_sha256=binary_sha,
                rpc_url=RPC_URL,
                user_token_x=TOKEN_X,
                user_token_y=TOKEN_Y,
            )


def test_invalid_destination_token_account_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="user_token_x validation failed"):
        _build(
            monkeypatch,
            resolution=_resolution(token_x_valid=False),
        )


def test_guard_rejection_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="guard rejected"):
        _build(
            monkeypatch,
            guard=_guard(accepted=False),
        )


def test_signed_transaction_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="found a signed transaction"):
        _build(
            monkeypatch,
            guard=_guard(unsigned=False),
        )


def test_failed_simulation_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="simulation did not succeed"):
        _build(
            monkeypatch,
            simulation=_simulation(succeeded=False),
        )


def test_simulation_cannot_precede_readiness_snapshot(monkeypatch):
    with pytest.raises(ValueError, match="predates readiness snapshot"):
        _build(
            monkeypatch,
            simulation=_simulation(slot=103),
        )


def test_readiness_cannot_pre_authorize_exit(monkeypatch):
    with pytest.raises(
        ValueError,
        match="unexpectedly authorizes exit_authorized",
    ):
        _build(
            monkeypatch,
            readiness_override={"exit_authorized": True},
        )


def test_resealed_preparation_cannot_authorize_exit(monkeypatch):
    report, _ = _build(monkeypatch)
    report["exit_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="exit_authorized=false"):
        MODULE.validate_exit_transaction_preparation(report)


def test_resealed_preparation_cannot_authorize_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_transaction_preparation(report)


def test_preparation_only_uses_non_submitting_rust_commands():
    source = TOOL.read_text(encoding="utf-8")

    assert 'BUILD_COMMAND = "build-token-exit-from-chain"' in source
    assert 'GUARD_COMMAND = "guard-transaction"' in source
    assert 'SIMULATION_COMMAND = "simulate-transaction"' in source
    assert "controlled-live-submit" not in source
    assert "execution-confirmation" not in source
    assert "execution-recovery" not in source
    assert "verify-position-closed" not in source
    assert "record_sent(" not in source
    assert '"exit_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
