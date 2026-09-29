from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_PREPARATION_V1"

ZERO_LIQUIDITY_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_zero_liquidity_v2.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_TOKEN_SETTLEMENT = Path("rust-executor/src/token_settlement.rs")
RUST_TOKEN_EXTENSIONS = Path("rust-executor/src/token_extensions.rs")
RUST_TRANSACTION_GUARD = Path("rust-executor/src/transaction_guard.rs")
RUST_SIMULATION = Path("rust-executor/src/simulation.rs")
REVIEWED_SOURCE_BLOBS = {
    ZERO_LIQUIDITY_TOOL: "b40f7671c245238e0d7743f760e4e66233c02b17",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    RUST_TOKEN_SETTLEMENT: "6607d5e95024d4f9869181656b903a68f71dc34a",
    RUST_TOKEN_EXTENSIONS: "7de5a3b5892f1d69c95d920d3adb3f57ff84148b",
    RUST_TRANSACTION_GUARD: "ca8189735003d4e2f2f97e4c498c4d1d67c0f878",
    RUST_SIMULATION: "d3224a83b463962b21d8671649755afaad70870b",
}

BUILD_COMMAND = "build-token-settlement-from-chain"
GUARD_COMMAND = "guard-transaction"
SIMULATE_COMMAND = "simulate-transaction"

DLMM_PROGRAM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
CLAIM_FEE2_PREFIX = "70bf65ab1c907fbb"
CLAIM_REWARD2_PREFIX = "be037f77b2579db7"
CLOSE_POSITION2_PREFIX = "ae5a2373ba2893e2"

KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_zero_liquidity_proof_sha256",
    "expected_zero_liquidity_proof_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "exit_signature",
    "exit_transaction_slot",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "destination_config_sha256",
    "user_token_x",
    "user_token_y",
    "reward_token_destinations",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "builder_report_sha256",
    "builder_position_has_zero_liquidity",
    "builder_owner_matches_sender",
    "token_x_program",
    "token_y_program",
    "token_x_is_2022",
    "token_y_is_2022",
    "position_width",
    "fee_transfer_hook_account_count",
    "reward_validation",
    "bin_array_accounts",
    "settlement_transaction_base64",
    "settlement_transaction_sha256",
    "claims_fees",
    "reward_indices_claimed",
    "closes_position_account",
    "reward_transfer_hook_account_count",
    "instruction_count",
    "guard_report_sha256",
    "guard_fee_payer_matches_executor",
    "guard_pool_account_present",
    "guard_required_accounts_present",
    "guard_unsigned",
    "guard_no_address_lookup_tables",
    "guard_instruction_sequence_valid",
    "simulation_report_sha256",
    "simulation_rpc_context_slot",
    "simulation_succeeded",
    "settlement_prepared",
    "fresh_blockhash_exact_finalization_required",
    "exact_settlement_transaction_authorization_required",
    "single_submission_only_required",
    "post_settlement_confirmation_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "settlement_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_entry_authorized",
    "new_live_capital_authorized",
    "phase7_promotion_persisted",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _git_blob_sha(path: Path) -> str:
    return _git_blob_sha_bytes(path.read_bytes())


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: str | Path, *, label: str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _regular_executable(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if not (st.st_mode & stat.S_IXUSR):
        raise ValueError(f"{label} must be executable")
    return resolved


def _load_zero_liquidity_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT settlement dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT settlement dependency mismatch: {relative}"
            )
    return _load_module(
        source / ZERO_LIQUIDITY_TOOL,
        "phase7_exit_settlement_zero_liquidity",
    )


def _execution_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("RPC_URL", None)
    env["SOLANA_RPC_URL"] = rpc_url
    return env


def _run_json(
    command: list[str],
    *,
    env: dict[str, str],
    allowed_returncodes: set[int] = {0},
    timeout: int = 45,
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode not in allowed_returncodes:
        raise ValueError(
            f"reviewed settlement command failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "reviewed settlement command returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError("reviewed settlement command returned invalid result")
    return value


def _validated_destination_config(
    value: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]]]:
    if set(value) != {
        "user_token_x",
        "user_token_y",
        "reward_token_destinations",
    }:
        raise ValueError(
            "Phase 7 EXIT settlement destination config schema mismatch"
        )
    user_x = value.get("user_token_x")
    user_y = value.get("user_token_y")
    if not isinstance(user_x, str) or not user_x:
        raise ValueError("settlement user_token_x is invalid")
    if not isinstance(user_y, str) or not user_y:
        raise ValueError("settlement user_token_y is invalid")

    rewards = value.get("reward_token_destinations")
    if not isinstance(rewards, list) or len(rewards) > 2:
        raise ValueError(
            "settlement reward_token_destinations must be a list of at most two items"
        )
    seen: set[int] = set()
    normalized: list[dict[str, Any]] = []
    for item in rewards:
        if not isinstance(item, dict) or set(item) != {
            "reward_index",
            "user_token_account",
        }:
            raise ValueError(
                "settlement reward destination schema mismatch"
            )
        index = item.get("reward_index")
        account = item.get("user_token_account")
        if index not in {0, 1} or index in seen:
            raise ValueError(
                "settlement reward destination index is invalid or duplicated"
            )
        if not isinstance(account, str) or not account:
            raise ValueError(
                "settlement reward destination token account is invalid"
            )
        seen.add(index)
        normalized.append(
            {
                "reward_index": index,
                "user_token_account": account,
            }
        )
    normalized.sort(key=lambda item: item["reward_index"])
    return user_x, user_y, normalized


def _expected_instruction_prefixes(
    reward_indices: list[int],
) -> list[str]:
    return [
        CLAIM_FEE2_PREFIX,
        *[CLAIM_REWARD2_PREFIX for _ in reward_indices],
        CLOSE_POSITION2_PREFIX,
    ]


def validate_exit_settlement_preparation(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT settlement preparation must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"preparation_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement preparation schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement preparation format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement preparation type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "Phase 7 EXIT settlement preparation lineage mismatch"
        )

    for field in (
        "saved_zero_liquidity_proof_sha256",
        "expected_zero_liquidity_proof_sha256",
        "rpc_endpoint_sha256",
        "destination_config_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "builder_report_sha256",
        "settlement_transaction_sha256",
        "guard_report_sha256",
        "simulation_report_sha256",
        "preparation_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement preparation {field} is invalid"
            )

    if (
        report["saved_zero_liquidity_proof_sha256"]
        != report["expected_zero_liquidity_proof_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement preparation proof digest mismatch"
        )
    if (
        report["executor_binary_sha256"]
        != report["expected_executor_binary_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement preparation executor trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "exit_signature",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "user_token_x",
        "user_token_y",
        "executor_binary_path",
        "token_x_program",
        "token_y_program",
        "settlement_transaction_base64",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT settlement preparation {field} is invalid"
            )

    for field in (
        "exit_transaction_slot",
        "position_width",
        "fee_transfer_hook_account_count",
        "reward_transfer_hook_account_count",
        "instruction_count",
        "simulation_rpc_context_slot",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT settlement preparation {field} is invalid"
            )
    if report["exit_transaction_slot"] <= 0:
        raise ValueError("Phase 7 EXIT settlement transaction slot is invalid")
    if report["position_width"] <= 0:
        raise ValueError("Phase 7 EXIT settlement position width is invalid")

    rewards = report.get("reward_token_destinations")
    if not isinstance(rewards, list) or len(rewards) > 2:
        raise ValueError(
            "Phase 7 EXIT settlement reward destinations are invalid"
        )
    reward_indices = report.get("reward_indices_claimed")
    if (
        not isinstance(reward_indices, list)
        or any(index not in {0, 1} for index in reward_indices)
        or len(reward_indices) != len(set(reward_indices))
    ):
        raise ValueError(
            "Phase 7 EXIT settlement claimed reward indices are invalid"
        )
    if report["instruction_count"] != 2 + len(reward_indices):
        raise ValueError(
            "Phase 7 EXIT settlement instruction count mismatch"
        )
    if not isinstance(report.get("reward_validation"), list):
        raise ValueError(
            "Phase 7 EXIT settlement reward validation is invalid"
        )
    if not isinstance(report.get("bin_array_accounts"), list):
        raise ValueError(
            "Phase 7 EXIT settlement bin-array list is invalid"
        )

    for field in (
        "builder_position_has_zero_liquidity",
        "builder_owner_matches_sender",
        "claims_fees",
        "closes_position_account",
        "guard_fee_payer_matches_executor",
        "guard_pool_account_present",
        "guard_required_accounts_present",
        "guard_unsigned",
        "guard_no_address_lookup_tables",
        "guard_instruction_sequence_valid",
        "simulation_succeeded",
        "settlement_prepared",
        "fresh_blockhash_exact_finalization_required",
        "exact_settlement_transaction_authorization_required",
        "single_submission_only_required",
        "post_settlement_confirmation_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement preparation requires {field}=true"
            )

    for field in (
        "settlement_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement preparation requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["preparation_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT settlement preparation digest mismatch"
        )


def build_exit_settlement_preparation(
    *,
    source_tree: str | Path,
    saved_zero_liquidity_proof_path: str | Path,
    expected_zero_liquidity_proof_sha256: str,
    destination_config_path: str | Path,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    proof_module = _load_zero_liquidity_module(source)

    proof = _load_json(
        saved_zero_liquidity_proof_path,
        label="saved Phase 7 EXIT zero-liquidity proof",
    )
    proof_module.validate_exit_zero_liquidity_proof(proof)
    if not _is_hex_digest(expected_zero_liquidity_proof_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT zero-liquidity proof digest is invalid"
        )
    if proof["proof_sha256"] != expected_zero_liquidity_proof_sha256:
        raise ValueError(
            "saved Phase 7 EXIT zero-liquidity proof digest mismatch"
        )
    for field in (
        "zero_liquidity_proven",
        "settlement_preparation_required",
        "position_account_close_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if proof.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement preparation requires proof {field}=true"
            )
    for field in (
        "settlement_authorized",
        "transaction_signing_performed",
        "transaction_submission_attempted",
        "automatic_resubmission_performed",
        "live_capital_effect_reconciled",
        "phase7_promotion_persisted",
        "production_pio_database_modified",
    ):
        if proof.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement preparation refuses proof {field}=true"
            )

    if _sha256_text(rpc_url) != proof["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement preparation RPC endpoint mismatch"
        )

    destinations = _load_json(
        destination_config_path,
        label="Phase 7 EXIT settlement destination config",
    )
    user_x, user_y, reward_destinations = _validated_destination_config(
        destinations
    )
    destination_sha = _sha256_value(destinations)

    binary = _regular_executable(
        executor_binary_path,
        label="Phase 7 executor binary",
    )
    binary_sha = _sha256_bytes(binary.read_bytes())
    if not _is_hex_digest(expected_executor_binary_sha256, 64):
        raise ValueError(
            "expected Phase 7 executor binary SHA-256 is invalid"
        )
    if binary_sha != expected_executor_binary_sha256:
        raise ValueError(
            "Phase 7 executor binary differs from external trust root"
        )

    env = _execution_env(rpc_url=rpc_url)
    settlement_request = {
        "position": proof["position_address"],
        "sender": proof["executor_wallet_pubkey"],
        "user_token_x": user_x,
        "user_token_y": user_y,
        "reward_token_destinations": reward_destinations,
    }

    proposal = {
        "decision_id": proof["exit_decision_id"],
        "mode": "LIVE",
        "action": "EXIT",
        "pool_address": proof["pool_address"],
        "capital_quote": 0.0,
        "account_equity_quote": 0.0,
        "portfolio_deployed_quote": 0.0,
        "daily_drawdown_pct": 0.0,
        "min_bin_id": 0,
        "max_bin_id": 0,
        "strategy": "PHASE7_EXIT_SETTLEMENT",
        "expected_net_return_pct": 0.0,
        "expected_downside_pct": 0.0,
        "model_version": "human-authorized-exit-settlement-v1",
        "data_age_seconds": 0,
    }

    required_accounts = [
        proof["position_address"],
        user_x,
        user_y,
        *[
            item["user_token_account"]
            for item in reward_destinations
        ],
    ]
    guard_config = {
        "expected_fee_payer": proof["executor_wallet_pubkey"],
        "allowed_program_ids": [DLMM_PROGRAM_ID],
        "max_instructions": 4,
        "max_static_accounts": 96,
        "allow_address_lookup_tables": False,
        "require_unsigned": True,
        "require_proposal_pool_account": True,
        "required_account_pubkeys": required_accounts,
        "require_instruction_policy": True,
        "instruction_policies": [
            {
                "program_id": DLMM_PROGRAM_ID,
                "allowed_actions": ["EXIT"],
                "allowed_data_prefixes_hex": [
                    CLAIM_FEE2_PREFIX,
                    CLAIM_REWARD2_PREFIX,
                    CLOSE_POSITION2_PREFIX,
                ],
            }
        ],
    }

    with tempfile.TemporaryDirectory(prefix="pio-phase7-settlement-") as tmp:
        root = Path(tmp)
        request_path = root / "settlement-request.json"
        proposal_path = root / "proposal.json"
        guard_path = root / "guard.json"
        tx_path = root / "transaction.base64"

        request_path.write_text(
            json.dumps(settlement_request, sort_keys=True),
            encoding="utf-8",
        )
        proposal_path.write_text(
            json.dumps(proposal, sort_keys=True),
            encoding="utf-8",
        )
        guard_path.write_text(
            json.dumps(guard_config, sort_keys=True),
            encoding="utf-8",
        )

        builder = _run_json(
            [str(binary), BUILD_COMMAND, str(request_path)],
            env=env,
        )
        if set(builder) != {"validation", "build"}:
            raise ValueError(
                "Phase 7 EXIT settlement builder schema mismatch"
            )
        validation = builder.get("validation")
        build = builder.get("build")
        if not isinstance(validation, dict) or not isinstance(build, dict):
            raise ValueError(
                "Phase 7 EXIT settlement builder result is invalid"
            )

        if validation.get("position") != proof["position_address"]:
            raise ValueError("settlement builder position mismatch")
        if validation.get("lb_pair") != proof["pool_address"]:
            raise ValueError("settlement builder pool mismatch")
        if validation.get("owner_matches_sender") is not True:
            raise ValueError("settlement builder owner check failed")
        if validation.get("position_has_zero_liquidity") is not True:
            raise ValueError("settlement builder zero-liquidity check failed")

        transaction = build.get("transaction_base64")
        if not isinstance(transaction, str) or not transaction:
            raise ValueError("settlement builder transaction is invalid")
        if build.get("program_id") != DLMM_PROGRAM_ID:
            raise ValueError("settlement builder program mismatch")
        if build.get("position") != proof["position_address"]:
            raise ValueError("settlement build position mismatch")
        if build.get("lb_pair") != proof["pool_address"]:
            raise ValueError("settlement build pool mismatch")
        if build.get("sender") != proof["executor_wallet_pubkey"]:
            raise ValueError("settlement build sender mismatch")
        if build.get("claims_fees") is not True:
            raise ValueError("settlement must claim fees")
        if build.get("closes_position_account") is not True:
            raise ValueError("settlement must close position account")

        reward_indices = build.get("reward_indices_claimed")
        if (
            not isinstance(reward_indices, list)
            or any(index not in {0, 1} for index in reward_indices)
            or len(reward_indices) != len(set(reward_indices))
        ):
            raise ValueError(
                "settlement claimed reward indices are invalid"
            )
        expected_dest_indices = [
            item["reward_index"] for item in reward_destinations
        ]
        if sorted(reward_indices) != expected_dest_indices:
            raise ValueError(
                "settlement claimed rewards differ from supplied destinations"
            )

        instruction_count = build.get("instruction_count")
        if instruction_count != 2 + len(reward_indices):
            raise ValueError(
                "settlement instruction count does not match fee/reward/close sequence"
            )

        tx_path.write_text(transaction, encoding="utf-8")
        guard = _run_json(
            [
                str(binary),
                GUARD_COMMAND,
                str(proposal_path),
                str(tx_path),
                str(guard_path),
            ],
            env=env,
        )
        if guard.get("accepted") is not True:
            raise ValueError("Phase 7 EXIT settlement transaction guard rejected")
        if guard.get("fee_payer") != proof["executor_wallet_pubkey"]:
            raise ValueError("settlement guard fee payer mismatch")
        if guard.get("pool_account_present") is not True:
            raise ValueError("settlement guard lost pool binding")
        if guard.get("required_accounts_present") is not True:
            raise ValueError("settlement guard lost required accounts")
        if guard.get("required_signatures") != 1:
            raise ValueError("settlement must require exactly one signature")
        if guard.get("signatures_all_default") is not True:
            raise ValueError("settlement transaction is already signed")
        if guard.get("address_lookup_table_count") != 0:
            raise ValueError("settlement transaction uses address lookup tables")
        if guard.get("instruction_count") != instruction_count:
            raise ValueError("settlement guard instruction count mismatch")
        if guard.get("program_ids") != [DLMM_PROGRAM_ID]:
            raise ValueError("settlement guard program set mismatch")

        fingerprints = guard.get("instruction_fingerprints")
        expected_prefixes = _expected_instruction_prefixes(reward_indices)
        if (
            not isinstance(fingerprints, list)
            or len(fingerprints) != len(expected_prefixes)
            or any(not isinstance(item, dict) for item in fingerprints)
        ):
            raise ValueError(
                "settlement guard instruction fingerprints are invalid"
            )
        actual_prefixes = [
            item.get("data_prefix_hex") for item in fingerprints
        ]
        if actual_prefixes != expected_prefixes:
            raise ValueError(
                "settlement guard instruction sequence mismatch"
            )
        if any(
            item.get("program_id") != DLMM_PROGRAM_ID
            for item in fingerprints
        ):
            raise ValueError(
                "settlement guard instruction program mismatch"
            )

        simulation = _run_json(
            [str(binary), SIMULATE_COMMAND, str(tx_path)],
            env=env,
        )
        if simulation.get("succeeded") is not True:
            raise ValueError("Phase 7 EXIT settlement simulation failed")
        simulation_slot = simulation.get("rpc_context_slot")
        if (
            not isinstance(simulation_slot, int)
            or isinstance(simulation_slot, bool)
            or simulation_slot < 0
        ):
            raise ValueError(
                "Phase 7 EXIT settlement simulation slot is invalid"
            )

    reward_validation = validation.get("rewards")
    bin_arrays = validation.get("bin_array_accounts")
    if not isinstance(reward_validation, list):
        raise ValueError("settlement reward validation is invalid")
    if not isinstance(bin_arrays, list):
        raise ValueError("settlement bin-array list is invalid")

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "saved_zero_liquidity_proof_sha256": proof["proof_sha256"],
        "expected_zero_liquidity_proof_sha256": (
            expected_zero_liquidity_proof_sha256
        ),
        "opened_decision_id": proof["opened_decision_id"],
        "exit_decision_id": proof["exit_decision_id"],
        "exit_signature": proof["signature"],
        "exit_transaction_slot": proof["transaction_slot"],
        "pool_address": proof["pool_address"],
        "position_address": proof["position_address"],
        "executor_wallet_pubkey": proof["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": proof["rpc_endpoint_sha256"],
        "destination_config_sha256": destination_sha,
        "user_token_x": user_x,
        "user_token_y": user_y,
        "reward_token_destinations": reward_destinations,
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": expected_executor_binary_sha256,
        "builder_report_sha256": _sha256_value(builder),
        "builder_position_has_zero_liquidity": True,
        "builder_owner_matches_sender": True,
        "token_x_program": validation["token_x_program"],
        "token_y_program": validation["token_y_program"],
        "token_x_is_2022": validation["token_x_is_2022"],
        "token_y_is_2022": validation["token_y_is_2022"],
        "position_width": validation["position_width"],
        "fee_transfer_hook_account_count": validation[
            "fee_transfer_hook_account_count"
        ],
        "reward_validation": reward_validation,
        "bin_array_accounts": bin_arrays,
        "settlement_transaction_base64": transaction,
        "settlement_transaction_sha256": _sha256_text(transaction),
        "claims_fees": True,
        "reward_indices_claimed": reward_indices,
        "closes_position_account": True,
        "reward_transfer_hook_account_count": build[
            "reward_transfer_hook_account_count"
        ],
        "instruction_count": instruction_count,
        "guard_report_sha256": _sha256_value(guard),
        "guard_fee_payer_matches_executor": True,
        "guard_pool_account_present": True,
        "guard_required_accounts_present": True,
        "guard_unsigned": True,
        "guard_no_address_lookup_tables": True,
        "guard_instruction_sequence_valid": True,
        "simulation_report_sha256": _sha256_value(simulation),
        "simulation_rpc_context_slot": simulation_slot,
        "simulation_succeeded": True,
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
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "preparation_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_settlement_preparation(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build, strictly guard, and simulate one unsigned token-aware "
            "Phase 7 EXIT settlement transaction after zero liquidity is "
            "proven. The transaction may claim fees/rewards and close the "
            "position account. This tool never signs or submits."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-zero-liquidity-proof", required=True)
    parser.add_argument("--expected-zero-liquidity-proof-sha256", required=True)
    parser.add_argument("--destination-config", required=True)
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    args = parser.parse_args()

    report = build_exit_settlement_preparation(
        source_tree=args.source_tree,
        saved_zero_liquidity_proof_path=args.saved_zero_liquidity_proof,
        expected_zero_liquidity_proof_sha256=(
            args.expected_zero_liquidity_proof_sha256
        ),
        destination_config_path=args.destination_config,
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=(
            args.expected_executor_binary_sha256
        ),
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
