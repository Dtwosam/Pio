from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_TRANSACTION_PREPARATION_V1"

READINESS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_transaction_preparation_readiness.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_TOKEN_EXIT = Path("rust-executor/src/token_exit.rs")
RUST_TOKEN_EXTENSIONS = Path("rust-executor/src/token_extensions.rs")
RUST_TRANSACTION_GUARD = Path("rust-executor/src/transaction_guard.rs")
RUST_SIMULATION = Path("rust-executor/src/simulation.rs")
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "0e5ef3f98569e197303708430c61371f9cc16703",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    RUST_TOKEN_EXIT: "cd46311c3919e97c80fcf2f6f2801a8cf8792784",
    RUST_TOKEN_EXTENSIONS: "7de5a3b5892f1d69c95d920d3adb3f57ff84148b",
    RUST_TRANSACTION_GUARD: "ca8189735003d4e2f2f97e4c498c4d1d67c0f878",
    RUST_SIMULATION: "d3224a83b463962b21d8671649755afaad70870b",
}

DLMM_PROGRAM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
REMOVE_LIQUIDITY2_PREFIX = "e6d7527ff165e392"
BUILD_COMMAND = "build-token-exit-from-chain"
GUARD_COMMAND = "guard-transaction"
SIMULATION_COMMAND = "simulate-transaction"
KEYPAIR_ENV = "PIO_EXECUTOR_KEYPAIR"
LIVE_SUBMIT_ENV = "PIO_LIVE_SUBMIT_ENABLED"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_readiness_sha256",
    "expected_readiness_sha256",
    "opened_decision_id",
    "opening_signature",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "user_token_x",
    "user_token_y",
    "exit_request_sha256",
    "chain_resolution_sha256",
    "validation_position_matches",
    "validation_pool_matches",
    "validation_owner_matches_sender",
    "validation_user_token_x_valid",
    "validation_user_token_y_valid",
    "token_x_program",
    "token_y_program",
    "token_x_is_2022",
    "token_y_is_2022",
    "removal_bin_count",
    "transfer_hook_account_count",
    "transaction_base64",
    "transaction_sha256",
    "dlmm_program_id",
    "remove_liquidity2_prefix",
    "guard_config_sha256",
    "guard_report_sha256",
    "transaction_guard_accepted",
    "guard_fee_payer_matches_executor",
    "guard_pool_account_present",
    "guard_required_accounts_present",
    "guard_instruction_count",
    "guard_required_signatures",
    "guard_transaction_unsigned",
    "simulation_sha256",
    "simulation_succeeded",
    "simulation_rpc_context_slot",
    "simulation_at_or_after_readiness",
    "exit_transaction_prepared",
    "initial_simulation_complete",
    "exact_exit_transaction_authorization_required",
    "fresh_presubmit_recheck_required",
    "fresh_blockhash_required",
    "final_simulation_required",
    "post_exit_confirmation_required",
    "post_exit_closure_proof_required",
    "post_exit_state_reconciliation_required",
    "exit_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_entry_authorized",
    "new_live_capital_authorized",
    "phase7_promotion_authorized",
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


def _load_reviewed(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT preparation dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT preparation dependency mismatch: {relative}"
            )
    return _load_module(
        source / READINESS_TOOL,
        "phase7_exit_transaction_preparation_readiness",
    )


def _execution_env(*, rpc_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(KEYPAIR_ENV, None)
    env.pop(LIVE_SUBMIT_ENV, None)
    env.pop("RPC_URL", None)
    env["SOLANA_RPC_URL"] = rpc_url
    return env


def _run_json(
    *,
    executor_binary: Path,
    args: list[str],
    env: dict[str, str],
    stdin_text: str | None = None,
) -> dict[str, Any]:
    completed = subprocess.run(
        [str(executor_binary), *args],
        env=env,
        input=stdin_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=45,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"reviewed executor command {args[0]} failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"reviewed executor command {args[0]} returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"reviewed executor command {args[0]} returned invalid result"
        )
    return value


def _nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is invalid")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} is invalid")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    result = _nonnegative_int(value, label=label)
    if result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def validate_exit_transaction_preparation(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT transaction preparation must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"preparation_sha256"}:
        raise ValueError(
            "Phase 7 EXIT transaction preparation schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT transaction preparation format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT transaction preparation type"
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
            "Phase 7 EXIT transaction preparation lineage mismatch"
        )

    for field in (
        "saved_readiness_sha256",
        "expected_readiness_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "exit_request_sha256",
        "chain_resolution_sha256",
        "transaction_sha256",
        "guard_config_sha256",
        "guard_report_sha256",
        "simulation_sha256",
        "preparation_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT transaction preparation {field} is invalid"
            )

    if report["saved_readiness_sha256"] != report["expected_readiness_sha256"]:
        raise ValueError(
            "Phase 7 EXIT transaction preparation readiness digest mismatch"
        )
    if (
        report["executor_binary_sha256"]
        != report["expected_executor_binary_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT transaction preparation binary trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "opening_signature",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "executor_binary_path",
        "user_token_x",
        "user_token_y",
        "token_x_program",
        "token_y_program",
        "transaction_base64",
    ):
        _nonempty_string(
            report.get(field),
            label=f"Phase 7 EXIT transaction preparation {field}",
        )

    if report.get("dlmm_program_id") != DLMM_PROGRAM_ID:
        raise ValueError(
            "Phase 7 EXIT transaction preparation DLMM program mismatch"
        )
    if report.get("remove_liquidity2_prefix") != REMOVE_LIQUIDITY2_PREFIX:
        raise ValueError(
            "Phase 7 EXIT transaction preparation instruction prefix mismatch"
        )

    for field in (
        "removal_bin_count",
        "guard_instruction_count",
        "guard_required_signatures",
    ):
        _positive_int(
            report.get(field),
            label=f"Phase 7 EXIT transaction preparation {field}",
        )
    _nonnegative_int(
        report.get("transfer_hook_account_count"),
        label="Phase 7 EXIT transaction preparation transfer_hook_account_count",
    )
    _nonnegative_int(
        report.get("simulation_rpc_context_slot"),
        label="Phase 7 EXIT transaction preparation simulation_rpc_context_slot",
    )
    if report["guard_instruction_count"] != 1:
        raise ValueError(
            "Phase 7 EXIT transaction preparation requires one instruction"
        )
    if report["guard_required_signatures"] != 1:
        raise ValueError(
            "Phase 7 EXIT transaction preparation requires one signer"
        )

    for field in (
        "validation_position_matches",
        "validation_pool_matches",
        "validation_owner_matches_sender",
        "validation_user_token_x_valid",
        "validation_user_token_y_valid",
        "transaction_guard_accepted",
        "guard_fee_payer_matches_executor",
        "guard_pool_account_present",
        "guard_required_accounts_present",
        "guard_transaction_unsigned",
        "simulation_succeeded",
        "simulation_at_or_after_readiness",
        "exit_transaction_prepared",
        "initial_simulation_complete",
        "exact_exit_transaction_authorization_required",
        "fresh_presubmit_recheck_required",
        "fresh_blockhash_required",
        "final_simulation_required",
        "post_exit_confirmation_required",
        "post_exit_closure_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT transaction preparation requires {field}=true"
            )

    for field in (
        "token_x_is_2022",
        "token_y_is_2022",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(
                f"Phase 7 EXIT transaction preparation {field} is invalid"
            )

    for field in (
        "exit_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT transaction preparation requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["preparation_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT transaction preparation digest mismatch"
        )


def build_exit_transaction_preparation(
    *,
    source_tree: str | Path,
    saved_readiness_path: str | Path,
    expected_readiness_sha256: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
    user_token_x: str,
    user_token_y: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    readiness_module = _load_reviewed(source)

    readiness = _load_json(
        saved_readiness_path,
        label="saved Phase 7 EXIT transaction-preparation readiness",
    )
    readiness_module.validate_exit_transaction_preparation_readiness(
        readiness
    )
    if not _is_hex_digest(expected_readiness_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT preparation readiness digest is invalid"
        )
    if readiness["readiness_sha256"] != expected_readiness_sha256:
        raise ValueError(
            "saved Phase 7 EXIT preparation readiness digest mismatch"
        )

    for field in (
        "human_exit_authorization_verified",
        "position_still_open",
        "exit_transaction_preparation_readiness_ready",
        "unsigned_exit_transaction_construction_required",
        "fresh_chain_resolution_required",
        "fresh_simulation_required",
        "separate_exit_transaction_authorization_required",
    ):
        if readiness.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT preparation readiness lost {field}"
            )
    if readiness.get("position_closed_proven") is not False:
        raise ValueError(
            "Phase 7 EXIT transaction preparation cannot target a closed position"
        )

    for field in (
        "exit_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if readiness.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT preparation readiness unexpectedly authorizes {field}"
            )

    if _sha256_text(rpc_url) != readiness["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT transaction preparation RPC endpoint mismatch"
        )

    binary = Path(executor_binary_path).resolve(strict=True)
    if not binary.is_file():
        raise ValueError("executor binary must be a regular file")
    binary_sha = _sha256_bytes(binary.read_bytes())
    if not _is_hex_digest(expected_executor_binary_sha256, 64):
        raise ValueError(
            "expected executor binary SHA-256 is invalid"
        )
    if binary_sha != expected_executor_binary_sha256:
        raise ValueError(
            "executor binary differs from external trust root"
        )
    if binary_sha != readiness["executor_binary_sha256"]:
        raise ValueError(
            "executor binary differs from EXIT readiness identity"
        )

    user_token_x = _nonempty_string(
        user_token_x,
        label="user_token_x",
    )
    user_token_y = _nonempty_string(
        user_token_y,
        label="user_token_y",
    )
    if user_token_x == user_token_y:
        raise ValueError(
            "EXIT destination token accounts must be distinct"
        )

    exit_request = {
        "position": readiness["position_address"],
        "user_token_x": user_token_x,
        "user_token_y": user_token_y,
        "sender": readiness["executor_wallet_pubkey"],
    }
    env = _execution_env(rpc_url=rpc_url)
    resolution = _run_json(
        executor_binary=binary,
        args=[BUILD_COMMAND, "-"],
        env=env,
        stdin_text=json.dumps(exit_request, sort_keys=True),
    )
    validation = resolution.get("validation")
    build = resolution.get("build")
    if not isinstance(validation, dict) or not isinstance(build, dict):
        raise ValueError(
            "token-aware EXIT construction returned invalid structure"
        )

    if validation.get("position") != readiness["position_address"]:
        raise ValueError(
            "token-aware EXIT validation position mismatch"
        )
    if validation.get("lb_pair") != readiness["pool_address"]:
        raise ValueError(
            "token-aware EXIT validation pool mismatch"
        )
    if validation.get("owner_matches_sender") is not True:
        raise ValueError(
            "token-aware EXIT validation owner mismatch"
        )
    if validation.get("user_token_x_valid") is not True:
        raise ValueError(
            "token-aware EXIT user_token_x validation failed"
        )
    if validation.get("user_token_y_valid") is not True:
        raise ValueError(
            "token-aware EXIT user_token_y validation failed"
        )
    removal_bin_count = _positive_int(
        validation.get("removal_bin_count"),
        label="token-aware EXIT removal_bin_count",
    )
    transfer_hook_count = _nonnegative_int(
        validation.get("transfer_hook_account_count"),
        label="token-aware EXIT transfer_hook_account_count",
    )
    token_x_program = _nonempty_string(
        validation.get("token_x_program"),
        label="token-aware EXIT token_x_program",
    )
    token_y_program = _nonempty_string(
        validation.get("token_y_program"),
        label="token-aware EXIT token_y_program",
    )
    if not isinstance(validation.get("token_x_is_2022"), bool):
        raise ValueError("token-aware EXIT token_x_is_2022 is invalid")
    if not isinstance(validation.get("token_y_is_2022"), bool):
        raise ValueError("token-aware EXIT token_y_is_2022 is invalid")

    if build.get("position") != readiness["position_address"]:
        raise ValueError("token-aware EXIT build position mismatch")
    if build.get("lb_pair") != readiness["pool_address"]:
        raise ValueError("token-aware EXIT build pool mismatch")
    if build.get("sender") != readiness["executor_wallet_pubkey"]:
        raise ValueError("token-aware EXIT build sender mismatch")
    if build.get("program_id") != DLMM_PROGRAM_ID:
        raise ValueError("token-aware EXIT build program mismatch")
    if build.get("removes_all_position_liquidity") is not True:
        raise ValueError(
            "token-aware EXIT build does not remove all liquidity"
        )
    if build.get("claims_fees_or_rewards") is not False:
        raise ValueError(
            "token-aware EXIT build unexpectedly claims fees or rewards"
        )
    if build.get("closes_position_account") is not False:
        raise ValueError(
            "token-aware EXIT build unexpectedly closes position account"
        )
    if build.get("removal_bin_count") != removal_bin_count:
        raise ValueError(
            "token-aware EXIT build removal-bin count mismatch"
        )
    if build.get("transfer_hook_account_count") != transfer_hook_count:
        raise ValueError(
            "token-aware EXIT build transfer-hook count mismatch"
        )
    transaction_base64 = _nonempty_string(
        build.get("transaction_base64"),
        label="token-aware EXIT transaction_base64",
    )

    proposal = {
        "decision_id": readiness["decision_record_id"],
        "mode": "LIVE",
        "action": "EXIT",
        "pool_address": readiness["pool_address"],
        "capital_quote": 0.0,
        "account_equity_quote": 0.0,
        "portfolio_deployed_quote": 0.0,
        "daily_drawdown_pct": 0.0,
        "min_bin_id": 0,
        "max_bin_id": 0,
        "strategy": "PHASE7_CONTROLLED_LIVE_EXIT",
        "expected_net_return_pct": 0.0,
        "expected_downside_pct": 0.0,
        "model_version": "human-authorized-exit-v1",
        "data_age_seconds": 0,
    }
    guard_config = {
        "expected_fee_payer": readiness["executor_wallet_pubkey"],
        "allowed_program_ids": [DLMM_PROGRAM_ID],
        "max_instructions": 1,
        "max_static_accounts": 64,
        "allow_address_lookup_tables": False,
        "require_unsigned": True,
        "require_proposal_pool_account": True,
        "required_account_pubkeys": [
            readiness["position_address"],
            user_token_x,
            user_token_y,
        ],
        "require_instruction_policy": True,
        "instruction_policies": [
            {
                "program_id": DLMM_PROGRAM_ID,
                "allowed_actions": ["EXIT"],
                "allowed_data_prefixes_hex": [
                    REMOVE_LIQUIDITY2_PREFIX
                ],
            }
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        proposal_path = root / "proposal.json"
        transaction_path = root / "transaction.base64"
        config_path = root / "guard.json"
        proposal_path.write_text(
            json.dumps(proposal, sort_keys=True),
            encoding="utf-8",
        )
        transaction_path.write_text(
            transaction_base64,
            encoding="utf-8",
        )
        config_path.write_text(
            json.dumps(guard_config, sort_keys=True),
            encoding="utf-8",
        )
        guard = _run_json(
            executor_binary=binary,
            args=[
                GUARD_COMMAND,
                str(proposal_path),
                str(transaction_path),
                str(config_path),
            ],
            env=env,
        )

    if guard.get("accepted") is not True:
        raise ValueError("EXIT transaction guard rejected transaction")
    if guard.get("fee_payer") != readiness["executor_wallet_pubkey"]:
        raise ValueError("EXIT transaction guard fee payer mismatch")
    if guard.get("pool_account_present") is not True:
        raise ValueError("EXIT transaction guard lost pool binding")
    if guard.get("required_accounts_present") is not True:
        raise ValueError(
            "EXIT transaction guard lost required account binding"
        )
    if guard.get("instruction_count") != 1:
        raise ValueError(
            "EXIT transaction guard requires exactly one instruction"
        )
    if guard.get("required_signatures") != 1:
        raise ValueError(
            "EXIT transaction guard requires exactly one signer"
        )
    if guard.get("signatures_all_default") is not True:
        raise ValueError(
            "EXIT transaction guard found a signed transaction"
        )
    if guard.get("address_lookup_table_count") != 0:
        raise ValueError(
            "EXIT transaction guard found address lookup tables"
        )
    if guard.get("program_ids") != [DLMM_PROGRAM_ID]:
        raise ValueError(
            "EXIT transaction guard program set mismatch"
        )
    fingerprints = guard.get("instruction_fingerprints")
    if (
        not isinstance(fingerprints, list)
        or len(fingerprints) != 1
        or not isinstance(fingerprints[0], dict)
        or fingerprints[0].get("program_id") != DLMM_PROGRAM_ID
        or fingerprints[0].get("data_prefix_hex")
        != REMOVE_LIQUIDITY2_PREFIX
    ):
        raise ValueError(
            "EXIT transaction guard instruction fingerprint mismatch"
        )

    simulation = _run_json(
        executor_binary=binary,
        args=[SIMULATION_COMMAND, "-"],
        env=env,
        stdin_text=transaction_base64,
    )
    if simulation.get("succeeded") is not True:
        raise ValueError(
            "EXIT transaction initial simulation did not succeed"
        )
    simulation_slot = _nonnegative_int(
        simulation.get("rpc_context_slot"),
        label="EXIT transaction simulation rpc_context_slot",
    )
    if simulation_slot < readiness["fresh_capture_slot_start"]:
        raise ValueError(
            "EXIT transaction simulation predates readiness snapshot"
        )

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
        "saved_readiness_sha256": readiness["readiness_sha256"],
        "expected_readiness_sha256": expected_readiness_sha256,
        "opened_decision_id": readiness["opened_decision_id"],
        "opening_signature": readiness["opening_signature"],
        "exit_decision_id": readiness["decision_record_id"],
        "pool_address": readiness["pool_address"],
        "position_address": readiness["position_address"],
        "executor_wallet_pubkey": readiness["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": readiness["rpc_endpoint_sha256"],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": expected_executor_binary_sha256,
        "user_token_x": user_token_x,
        "user_token_y": user_token_y,
        "exit_request_sha256": _sha256_value(exit_request),
        "chain_resolution_sha256": _sha256_value(resolution),
        "validation_position_matches": True,
        "validation_pool_matches": True,
        "validation_owner_matches_sender": True,
        "validation_user_token_x_valid": True,
        "validation_user_token_y_valid": True,
        "token_x_program": token_x_program,
        "token_y_program": token_y_program,
        "token_x_is_2022": validation["token_x_is_2022"],
        "token_y_is_2022": validation["token_y_is_2022"],
        "removal_bin_count": removal_bin_count,
        "transfer_hook_account_count": transfer_hook_count,
        "transaction_base64": transaction_base64,
        "transaction_sha256": _sha256_text(transaction_base64),
        "dlmm_program_id": DLMM_PROGRAM_ID,
        "remove_liquidity2_prefix": REMOVE_LIQUIDITY2_PREFIX,
        "guard_config_sha256": _sha256_value(guard_config),
        "guard_report_sha256": _sha256_value(guard),
        "transaction_guard_accepted": True,
        "guard_fee_payer_matches_executor": True,
        "guard_pool_account_present": True,
        "guard_required_accounts_present": True,
        "guard_instruction_count": guard["instruction_count"],
        "guard_required_signatures": guard["required_signatures"],
        "guard_transaction_unsigned": True,
        "simulation_sha256": _sha256_value(simulation),
        "simulation_succeeded": True,
        "simulation_rpc_context_slot": simulation_slot,
        "simulation_at_or_after_readiness": True,
        "exit_transaction_prepared": True,
        "initial_simulation_complete": True,
        "exact_exit_transaction_authorization_required": True,
        "fresh_presubmit_recheck_required": True,
        "fresh_blockhash_required": True,
        "final_simulation_required": True,
        "post_exit_confirmation_required": True,
        "post_exit_closure_proof_required": True,
        "post_exit_state_reconciliation_required": True,
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
    report = {
        **identity,
        "preparation_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_transaction_preparation(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Construct, strictly guard and simulate one unsigned token-aware "
            "Phase 7 EXIT transaction for the exact authorized open position. "
            "This tool strips signing/live-submit environment, performs no "
            "submission or retry, and requires a separate exact transaction "
            "authorization before any signing path."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--expected-readiness-sha256", required=True)
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--user-token-x", required=True)
    parser.add_argument("--user-token-y", required=True)
    args = parser.parse_args()

    report = build_exit_transaction_preparation(
        source_tree=args.source_tree,
        saved_readiness_path=args.saved_readiness,
        expected_readiness_sha256=args.expected_readiness_sha256,
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=args.expected_executor_binary_sha256,
        rpc_url=args.rpc_url,
        user_token_x=args.user_token_x,
        user_token_y=args.user_token_y,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
