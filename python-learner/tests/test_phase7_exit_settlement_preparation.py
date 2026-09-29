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
    / "build_phase7_controlled_live_exit_settlement_preparation.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_phase7_controlled_live_exit_settlement_preparation",
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


class _FakeProofModule:
    @staticmethod
    def validate_exit_zero_liquidity_proof(value):
        assert isinstance(value, dict)


def _proof() -> dict:
    return {
        "proof_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "signature": "sig-confirmed-exit",
        "transaction_slot": 120,
        "position_snapshot_sha256": "b" * 64,
        "capture_slot_start": 121,
        "capture_slot_end": 121,
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "zero_liquidity_proven": True,
        "settlement_preparation_required": True,
        "position_account_close_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "settlement_authorized": False,
        "transaction_signing_performed": False,
        "transaction_submission_attempted": False,
        "automatic_resubmission_performed": False,
        "live_capital_effect_reconciled": False,
        "phase7_promotion_persisted": False,
        "production_pio_database_modified": False,
    }


def _destinations(*, reward: bool = True) -> dict:
    return {
        "user_token_x": USER_X,
        "user_token_y": USER_Y,
        "reward_token_destinations": (
            [{"reward_index": 0, "user_token_account": REWARD_0}]
            if reward
            else []
        ),
    }


def _builder(*, reward: bool = True) -> dict:
    reward_indices = [0] if reward else []
    return {
        "validation": {
            "position": POSITION,
            "lb_pair": POOL,
            "owner_matches_sender": True,
            "position_has_zero_liquidity": True,
            "token_x_program": "TokenProgramX",
            "token_y_program": "TokenProgramY",
            "token_x_is_2022": True,
            "token_y_is_2022": False,
            "position_width": 21,
            "fee_transfer_hook_account_count": 2,
            "rewards": (
                [{
                    "reward_index": 0,
                    "mint": "RewardMint",
                    "token_program": "RewardProgram",
                    "is_token_2022": True,
                    "transfer_hook_account_count": 3,
                }]
                if reward
                else []
            ),
            "bin_array_accounts": ["BinArray1"],
        },
        "build": {
            "transaction_base64": "c2V0dGxlbWVudC10eA==",
            "program_id": MODULE.DLMM_PROGRAM_ID,
            "position": POSITION,
            "lb_pair": POOL,
            "sender": WALLET,
            "claims_fees": True,
            "reward_indices_claimed": reward_indices,
            "closes_position_account": True,
            "fee_transfer_hook_account_count": 2,
            "reward_transfer_hook_account_count": 3 if reward else 0,
            "instruction_count": 3 if reward else 2,
        },
    }


def _guard(*, reward: bool = True, signed: bool = False) -> dict:
    prefixes = [
        MODULE.CLAIM_FEE2_PREFIX,
        *([MODULE.CLAIM_REWARD2_PREFIX] if reward else []),
        MODULE.CLOSE_POSITION2_PREFIX,
    ]
    return {
        "accepted": True,
        "reason": "approved",
        "fee_payer": WALLET,
        "pool_account_present": True,
        "required_accounts_present": True,
        "instruction_count": len(prefixes),
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
    }


def _simulation(*, succeeded: bool = True, slot: int = 130) -> dict:
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
    reward: bool = True,
    proof_override: dict | None = None,
    builder: dict | None = None,
    guard: dict | None = None,
    simulation: dict | None = None,
    destinations: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        proof = _proof()
        if proof_override:
            proof.update(proof_override)
        proof_path = _write(root / "proof.json", proof)
        destination_value = destinations or _destinations(reward=reward)
        destination_path = _write(
            root / "destinations.json",
            destination_value,
        )

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_zero_liquidity_module",
            lambda source: _FakeProofModule,
        )
        observed = {"commands": []}

        def fake_run_json(command, *, env, allowed_returncodes={0}, timeout=45):
            observed["commands"].append(command)
            observed["env"] = env
            if command[1] == MODULE.BUILD_COMMAND:
                return builder or _builder(reward=reward)
            if command[1] == MODULE.GUARD_COMMAND:
                return guard or _guard(reward=reward)
            if command[1] == MODULE.SIMULATE_COMMAND:
                return simulation or _simulation()
            raise AssertionError(command)

        monkeypatch.setattr(MODULE, "_run_json", fake_run_json)
        monkeypatch.setenv(MODULE.KEYPAIR_ENV, "/secret/keypair.json")
        monkeypatch.setenv(MODULE.LIVE_SUBMIT_ENV, "1")
        monkeypatch.setenv("RPC_URL", "https://fallback.invalid")

        report = MODULE.build_exit_settlement_preparation(
            source_tree=ROOT,
            saved_zero_liquidity_proof_path=proof_path,
            expected_zero_liquidity_proof_sha256=proof["proof_sha256"],
            destination_config_path=destination_path,
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            rpc_url=RPC_URL,
        )
        return report, observed


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


def test_anchor_instruction_prefixes_are_exact():
    assert MODULE.CLAIM_FEE2_PREFIX == hashlib.sha256(
        b"global:claim_fee2"
    ).digest()[:8].hex()
    assert MODULE.CLAIM_REWARD2_PREFIX == hashlib.sha256(
        b"global:claim_reward2"
    ).digest()[:8].hex()
    assert MODULE.CLOSE_POSITION2_PREFIX == hashlib.sha256(
        b"global:close_position2"
    ).digest()[:8].hex()


def test_token_aware_settlement_is_guarded_simulated_and_non_authorizing(
    monkeypatch,
):
    report, observed = _build(monkeypatch)

    commands = [command[1] for command in observed["commands"]]
    assert commands == [
        MODULE.BUILD_COMMAND,
        MODULE.GUARD_COMMAND,
        MODULE.SIMULATE_COMMAND,
    ]
    assert MODULE.KEYPAIR_ENV not in observed["env"]
    assert MODULE.LIVE_SUBMIT_ENV not in observed["env"]
    assert "RPC_URL" not in observed["env"]
    assert observed["env"]["SOLANA_RPC_URL"] == RPC_URL

    assert report["builder_position_has_zero_liquidity"] is True
    assert report["builder_owner_matches_sender"] is True
    assert report["token_x_is_2022"] is True
    assert report["reward_indices_claimed"] == [0]
    assert report["instruction_count"] == 3
    assert report["claims_fees"] is True
    assert report["closes_position_account"] is True
    assert report["guard_instruction_sequence_valid"] is True
    assert report["guard_unsigned"] is True
    assert report["simulation_succeeded"] is True
    assert report["simulation_at_or_after_zero_liquidity_snapshot"] is True
    assert report["zero_liquidity_capture_slot_start"] == 121
    assert report["settlement_prepared"] is True
    assert report["fresh_blockhash_exact_finalization_required"] is True
    assert report["exact_settlement_transaction_authorization_required"] is True
    assert report["settlement_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_settlement_without_active_rewards_has_fee_then_close(monkeypatch):
    report, _ = _build(monkeypatch, reward=False)

    assert report["reward_indices_claimed"] == []
    assert report["reward_token_destinations"] == []
    assert report["instruction_count"] == 2
    assert report["reward_transfer_hook_account_count"] == 0
    assert report["settlement_prepared"] is True


def test_reward_destination_must_match_claimed_reward(monkeypatch):
    bad = _builder(reward=True)
    bad["build"]["reward_indices_claimed"] = []

    with pytest.raises(
        ValueError,
        match="claimed rewards differ from supplied destinations",
    ):
        _build(monkeypatch, builder=bad)


def test_instruction_sequence_must_end_in_close(monkeypatch):
    bad = _guard(reward=True)
    bad["instruction_fingerprints"][-1]["data_prefix_hex"] = (
        MODULE.CLAIM_FEE2_PREFIX
    )

    with pytest.raises(
        ValueError,
        match="instruction sequence mismatch",
    ):
        _build(monkeypatch, guard=bad)


def test_presigned_settlement_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="already signed"):
        _build(
            monkeypatch,
            guard=_guard(reward=True, signed=True),
        )


def test_failed_simulation_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="simulation failed"):
        _build(
            monkeypatch,
            simulation=_simulation(succeeded=False),
        )


def test_simulation_cannot_precede_zero_liquidity_snapshot(monkeypatch):
    with pytest.raises(
        ValueError,
        match="predates zero-liquidity snapshot",
    ):
        _build(
            monkeypatch,
            simulation=_simulation(slot=120),
        )


def test_zero_liquidity_proof_cannot_pre_authorize_settlement(monkeypatch):
    with pytest.raises(
        ValueError,
        match="refuses proof settlement_authorized=true",
    ):
        _build(
            monkeypatch,
            proof_override={"settlement_authorized": True},
        )


def test_rpc_endpoint_must_match_zero_liquidity_proof(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            proof_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_duplicate_reward_destination_is_rejected(monkeypatch):
    destinations = {
        "user_token_x": USER_X,
        "user_token_y": USER_Y,
        "reward_token_destinations": [
            {"reward_index": 0, "user_token_account": REWARD_0},
            {"reward_index": 0, "user_token_account": REWARD_0},
        ],
    }

    with pytest.raises(ValueError, match="invalid or duplicated"):
        _build(monkeypatch, destinations=destinations)


def test_resealed_destination_tampering_fails_nested_digest(monkeypatch):
    report, _ = _build(monkeypatch)
    report["reward_token_destinations"][0]["user_token_account"] = (
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="destination-config digest mismatch",
    ):
        MODULE.validate_exit_settlement_preparation(report)


def test_resealed_transaction_tampering_fails_nested_digest(monkeypatch):
    report, _ = _build(monkeypatch)
    report["settlement_transaction_base64"] = "dGFtcGVyZWQ="
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction digest mismatch",
    ):
        MODULE.validate_exit_settlement_preparation(report)


def test_resealed_preparation_cannot_authorize_signing(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_signing_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_signing_authorized=false",
    ):
        MODULE.validate_exit_settlement_preparation(report)


def test_resealed_preparation_cannot_authorize_submission(monkeypatch):
    report, _ = _build(monkeypatch)
    report["transaction_submission_authorized"] = True
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="transaction_submission_authorized=false",
    ):
        MODULE.validate_exit_settlement_preparation(report)


def test_settlement_preparation_has_no_signing_or_submission_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'BUILD_COMMAND = "build-token-settlement-from-chain"' in source
    assert 'GUARD_COMMAND = "guard-transaction"' in source
    assert 'SIMULATE_COMMAND = "simulate-transaction"' in source
    assert "env.pop(KEYPAIR_ENV, None)" in source
    assert "env.pop(LIVE_SUBMIT_ENV, None)" in source

    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "controlled-live-submit" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert '"settlement_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
    assert '"phase7_promotion_authorized": False' in source
