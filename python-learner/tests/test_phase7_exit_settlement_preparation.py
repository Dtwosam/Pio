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
TOKEN_X = "55555555555555555555555555555555"
TOKEN_Y = "66666666666666666666666666666666"
REWARD_0 = "77777777777777777777777777777777"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHn7PcsB83Q5p"


class _FakeZeroProof:
    @staticmethod
    def validate_exit_zero_liquidity_proof(value):
        assert isinstance(value, dict)


def _proof() -> dict:
    return {
        "proof_sha256": "a" * 64,
        "opened_decision_id": OPENED_DECISION_ID,
        "exit_decision_id": EXIT_DECISION_ID,
        "signature": "exit-signature",
        "pool_address": POOL,
        "position_address": POSITION,
        "executor_wallet_pubkey": WALLET,
        "rpc_endpoint_sha256": MODULE._sha256_text(RPC_URL),
        "position_snapshot_sha256": "b" * 64,
        "capture_slot_start": 130,
        "capture_slot_end": 130,
        "post_transaction_snapshot_order_valid": True,
        "position_account_present": True,
        "pool_identity_matches": True,
        "owner_identity_matches": True,
        "fee_owner_supported_for_settlement": True,
        "principal_x_zero": True,
        "principal_y_zero": True,
        "all_position_liquidity_zero": True,
        "all_position_x_zero": True,
        "all_position_y_zero": True,
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
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }


def _destinations() -> list[dict]:
    return [
        {
            "reward_index": 0,
            "user_token_account": REWARD_0,
        }
    ]


def _resolution(
    *,
    zero_liquidity: bool = True,
    rewards: list[dict] | None = None,
    closes_position: bool = True,
) -> dict:
    reward_values = (
        [
            {
                "reward_index": 0,
                "mint": "88888888888888888888888888888888",
                "token_program": TOKEN_2022_PROGRAM,
                "is_token_2022": True,
                "transfer_hook_account_count": 3,
            }
        ]
        if rewards is None
        else rewards
    )
    reward_indices = [item["reward_index"] for item in reward_values]
    reward_hooks = sum(
        item["transfer_hook_account_count"] for item in reward_values
    )
    return {
        "validation": {
            "position": POSITION,
            "lb_pair": POOL,
            "owner_matches_sender": True,
            "position_has_zero_liquidity": zero_liquidity,
            "token_x_program": TOKEN_PROGRAM,
            "token_y_program": TOKEN_2022_PROGRAM,
            "token_x_is_2022": False,
            "token_y_is_2022": True,
            "position_width": 21,
            "fee_transfer_hook_account_count": 2,
            "rewards": reward_values,
            "bin_array_accounts": [
                "99999999999999999999999999999999"
            ],
        },
        "build": {
            "transaction_base64": "c2V0dGxlbWVudC10cmFuc2FjdGlvbg==",
            "program_id": MODULE.DLMM_PROGRAM_ID,
            "position": POSITION,
            "lb_pair": POOL,
            "sender": WALLET,
            "claims_fees": True,
            "reward_indices_claimed": reward_indices,
            "closes_position_account": closes_position,
            "fee_transfer_hook_account_count": 2,
            "reward_transfer_hook_account_count": reward_hooks,
            "instruction_count": 2 + len(reward_indices),
        },
    }


def _guard(
    *,
    unsigned: bool = True,
    prefixes: list[str] | None = None,
    instruction_count: int = 3,
) -> dict:
    values = prefixes or [
        MODULE.CLAIM_FEE2_PREFIX,
        MODULE.CLAIM_REWARD2_PREFIX,
        MODULE.CLOSE_POSITION2_PREFIX,
    ]
    return {
        "accepted": True,
        "reason": "approved",
        "fee_payer": WALLET,
        "pool_account_present": True,
        "required_accounts_present": True,
        "instruction_count": instruction_count,
        "static_account_count": 20,
        "required_signatures": 1,
        "signatures_all_default": unsigned,
        "address_lookup_table_count": 0,
        "program_ids": [MODULE.DLMM_PROGRAM_ID],
        "instruction_fingerprints": [
            {
                "program_id": MODULE.DLMM_PROGRAM_ID,
                "data_prefix_hex": prefix,
            }
            for prefix in values
        ],
    }


def _simulation(*, succeeded: bool = True, slot: int = 131) -> dict:
    return {
        "succeeded": succeeded,
        "rpc_context_slot": slot,
        "result": {"err": None if succeeded else {"custom": 1}},
    }


def _write(path: Path, value) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _build(
    monkeypatch,
    *,
    resolution: dict | None = None,
    guard: dict | None = None,
    simulation: dict | None = None,
    proof_override: dict | None = None,
    destinations: list[dict] | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        proof = _proof()
        if proof_override:
            proof.update(proof_override)
        proof_path = _write(root / "proof.json", proof)
        destination_path = _write(
            root / "rewards.json",
            _destinations() if destinations is None else destinations,
        )

        binary = root / "meteora-executor"
        binary.write_bytes(b"reviewed-executor")
        binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

        monkeypatch.setattr(
            MODULE,
            "_load_zero_proof_module",
            lambda source: _FakeZeroProof,
        )

        calls: list[str] = []
        observed = {}

        def fake_run_json(
            *,
            executor_binary,
            args,
            env,
            stdin_text=None,
        ):
            calls.append(args[0])
            assert MODULE.KEYPAIR_ENV not in env
            assert MODULE.LIVE_SUBMIT_ENV not in env
            assert "RPC_URL" not in env
            assert env["SOLANA_RPC_URL"] == RPC_URL

            if args[0] == MODULE.BUILD_COMMAND:
                request = json.loads(stdin_text)
                observed["settlement_request"] = request
                return resolution or _resolution()
            if args[0] == MODULE.GUARD_COMMAND:
                observed["guard_args"] = args
                return guard or _guard()
            if args[0] == MODULE.SIMULATION_COMMAND:
                observed["simulation_transaction"] = stdin_text
                return simulation or _simulation()
            raise AssertionError(args[0])

        monkeypatch.setattr(MODULE, "_run_json", fake_run_json)

        report = MODULE.build_settlement_transaction_preparation(
            source_tree=ROOT,
            saved_zero_liquidity_proof_path=proof_path,
            expected_zero_liquidity_proof_sha256=proof["proof_sha256"],
            executor_binary_path=binary,
            expected_executor_binary_sha256=binary_sha,
            rpc_url=RPC_URL,
            user_token_x=TOKEN_X,
            user_token_y=TOKEN_Y,
            reward_destinations_path=destination_path,
        )
        return report, calls, observed


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


def test_settlement_preparation_is_unsigned_guarded_and_simulated(monkeypatch):
    report, calls, observed = _build(monkeypatch)

    assert calls == [
        MODULE.BUILD_COMMAND,
        MODULE.GUARD_COMMAND,
        MODULE.SIMULATION_COMMAND,
    ]
    assert observed["settlement_request"] == {
        "position": POSITION,
        "sender": WALLET,
        "user_token_x": TOKEN_X,
        "user_token_y": TOKEN_Y,
        "reward_token_destinations": _destinations(),
    }
    assert observed["simulation_transaction"] == (
        report["transaction_base64"]
    )

    assert report["validation_zero_liquidity"] is True
    assert report["token_x_is_2022"] is False
    assert report["token_y_is_2022"] is True
    assert report["reward_indices_claimed"] == [0]
    assert report["instruction_count"] == 3
    assert report["claims_fees"] is True
    assert report["closes_position_account"] is True
    assert report["transaction_guard_accepted"] is True
    assert report["guard_transaction_unsigned"] is True
    assert report["simulation_succeeded"] is True
    assert report["settlement_transaction_prepared"] is True
    assert (
        report["exact_settlement_transaction_authorization_required"]
        is True
    )
    assert report["settlement_authorized"] is False
    assert report["transaction_signing_authorized"] is False
    assert report["transaction_submission_authorized"] is False


def test_no_active_rewards_accepts_empty_destination_list(monkeypatch):
    report, _, _ = _build(
        monkeypatch,
        resolution=_resolution(rewards=[]),
        guard=_guard(
            prefixes=[
                MODULE.CLAIM_FEE2_PREFIX,
                MODULE.CLOSE_POSITION2_PREFIX,
            ],
            instruction_count=2,
        ),
        destinations=[],
    )

    assert report["reward_token_destinations"] == []
    assert report["reward_validations"] == []
    assert report["reward_indices_claimed"] == []
    assert report["instruction_count"] == 2


def test_duplicate_reward_destination_fails_before_builder(monkeypatch):
    duplicate = [
        {
            "reward_index": 0,
            "user_token_account": REWARD_0,
        },
        {
            "reward_index": 0,
            "user_token_account": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        },
    ]

    with pytest.raises(ValueError, match="duplicated"):
        _build(
            monkeypatch,
            destinations=duplicate,
        )


def test_active_reward_destination_mismatch_fails_closed(monkeypatch):
    with pytest.raises(
        ValueError,
        match="active reward destinations mismatch",
    ):
        _build(
            monkeypatch,
            resolution=_resolution(rewards=[]),
        )


def test_builder_must_reconfirm_zero_liquidity(monkeypatch):
    with pytest.raises(ValueError, match="lost zero liquidity"):
        _build(
            monkeypatch,
            resolution=_resolution(zero_liquidity=False),
        )


def test_builder_must_close_position_account(monkeypatch):
    with pytest.raises(ValueError, match="must close position account"):
        _build(
            monkeypatch,
            resolution=_resolution(closes_position=False),
        )


def test_signed_guarded_transaction_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="found signed transaction"):
        _build(
            monkeypatch,
            guard=_guard(unsigned=False),
        )


def test_wrong_guard_instruction_fingerprint_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="fingerprint prefix invalid"):
        _build(
            monkeypatch,
            guard=_guard(
                prefixes=[
                    MODULE.CLAIM_FEE2_PREFIX,
                    "0000000000000000",
                    MODULE.CLOSE_POSITION2_PREFIX,
                ],
            ),
        )


def test_failed_settlement_simulation_fails_closed(monkeypatch):
    with pytest.raises(ValueError, match="simulation did not succeed"):
        _build(
            monkeypatch,
            simulation=_simulation(succeeded=False),
        )


def test_settlement_simulation_cannot_precede_zero_proof(monkeypatch):
    with pytest.raises(
        ValueError,
        match="predates zero-liquidity snapshot",
    ):
        _build(
            monkeypatch,
            simulation=_simulation(slot=129),
        )


def test_rpc_endpoint_must_match_zero_proof(monkeypatch):
    with pytest.raises(ValueError, match="RPC endpoint mismatch"):
        _build(
            monkeypatch,
            proof_override={"rpc_endpoint_sha256": "f" * 64},
        )


def test_zero_proof_cannot_pre_authorize_settlement(monkeypatch):
    with pytest.raises(
        ValueError,
        match="unexpectedly authorizes settlement_authorized",
    ):
        _build(
            monkeypatch,
            proof_override={"settlement_authorized": True},
        )


def test_resealed_preparation_cannot_authorize_settlement(monkeypatch):
    report, _, _ = _build(monkeypatch)
    report["settlement_authorized"] = True
    _reseal(report)

    with pytest.raises(ValueError, match="settlement_authorized=false"):
        MODULE.validate_settlement_transaction_preparation(report)


def test_resealed_destination_tampering_fails_internal_digest(monkeypatch):
    report, _, _ = _build(monkeypatch)
    report["reward_token_destinations"][0]["user_token_account"] = (
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    _reseal(report)

    with pytest.raises(
        ValueError,
        match="reward-destination digest mismatch",
    ):
        MODULE.validate_settlement_transaction_preparation(report)


def test_resealed_transaction_tampering_fails_internal_digest(monkeypatch):
    report, _, _ = _build(monkeypatch)
    report["transaction_base64"] = "dGFtcGVyZWQ="
    _reseal(report)

    with pytest.raises(ValueError, match="transaction digest mismatch"):
        MODULE.validate_settlement_transaction_preparation(report)


def test_settlement_preparation_has_no_execution_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert 'BUILD_COMMAND = "build-token-settlement-from-chain"' in source
    assert 'GUARD_COMMAND = "guard-transaction"' in source
    assert 'SIMULATION_COMMAND = "simulate-transaction"' in source
    assert "env.pop(KEYPAIR_ENV, None)" in source
    assert "env.pop(LIVE_SUBMIT_ENV, None)" in source

    assert "load_executor_keypair" not in source
    assert "sign_message" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "controlled-live-submit" not in source
    assert "record_sent(" not in source
    assert '"settlement_authorized": False' in source
    assert '"transaction_signing_authorized": False' in source
    assert '"transaction_submission_authorized": False' in source
