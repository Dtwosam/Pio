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
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_TRANSACTION_PREPARATION_V1"
)

ZERO_PROOF_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_zero_liquidity_v2.py"
)
RUST_MAIN = Path("rust-executor/src/main.rs")
RUST_SETTLEMENT = Path("rust-executor/src/settlement.rs")
RUST_TOKEN_SETTLEMENT = Path("rust-executor/src/token_settlement.rs")
RUST_TOKEN_EXTENSIONS = Path("rust-executor/src/token_extensions.rs")
RUST_TRANSACTION_GUARD = Path("rust-executor/src/transaction_guard.rs")
RUST_SIMULATION = Path("rust-executor/src/simulation.rs")
REVIEWED_SOURCE_BLOBS = {
    ZERO_PROOF_TOOL: "b40f7671c245238e0d7743f760e4e66233c02b17",
    RUST_MAIN: "96ecb4479482d146abbecafc53466b93fa452d80",
    RUST_SETTLEMENT: "9fb57245eeac79138fa9df172daad6f494ceb330",
    RUST_TOKEN_SETTLEMENT: "6607d5e95024d4f9869181656b903a68f71dc34a",
    RUST_TOKEN_EXTENSIONS: "7de5a3b5892f1d69c95d920d3adb3f57ff84148b",
    RUST_TRANSACTION_GUARD: "ca8189735003d4e2f2f97e4c498c4d1d67c0f878",
    RUST_SIMULATION: "d3224a83b463962b21d8671649755afaad70870b",
}

DLMM_PROGRAM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
CLAIM_FEE2_PREFIX = "70bf65ab1c907fbb"
CLAIM_REWARD2_PREFIX = "be037f77b2579db7"
CLOSE_POSITION2_PREFIX = "ae5a2373ba2893e2"
BUILD_COMMAND = "build-token-settlement-from-chain"
GUARD_COMMAND = "guard-transaction"
SIMULATION_COMMAND = "simulate-transaction"
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
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "zero_liquidity_snapshot_sha256",
    "zero_liquidity_capture_slot_start",
    "zero_liquidity_capture_slot_end",
    "executor_binary_path",
    "executor_binary_sha256",
    "expected_executor_binary_sha256",
    "user_token_x",
    "user_token_y",
    "reward_token_destinations",
    "reward_destinations_sha256",
    "settlement_request_sha256",
    "chain_resolution_sha256",
    "validation_position_matches",
    "validation_pool_matches",
    "validation_owner_matches_sender",
    "validation_zero_liquidity",
    "token_x_program",
    "token_y_program",
    "token_x_is_2022",
    "token_y_is_2022",
    "position_width",
    "fee_transfer_hook_account_count",
    "reward_validations",
    "bin_array_accounts",
    "transaction_base64",
    "transaction_sha256",
    "dlmm_program_id",
    "claims_fees",
    "reward_indices_claimed",
    "closes_position_account",
    "reward_transfer_hook_account_count",
    "instruction_count",
    "claim_fee2_prefix",
    "claim_reward2_prefix",
    "close_position2_prefix",
    "guard_config_sha256",
    "guard_report_sha256",
    "transaction_guard_accepted",
    "guard_fee_payer_matches_executor",
    "guard_pool_account_present",
    "guard_required_accounts_present",
    "guard_transaction_unsigned",
    "guard_instruction_count_matches_build",
    "guard_single_required_signature",
    "guard_no_address_lookup_tables",
    "guard_instruction_fingerprints_valid",
    "simulation_sha256",
    "simulation_succeeded",
    "simulation_rpc_context_slot",
    "simulation_at_or_after_zero_liquidity_snapshot",
    "settlement_transaction_prepared",
    "exact_settlement_transaction_authorization_required",
    "fresh_blockhash_required",
    "final_simulation_required",
    "post_settlement_confirmation_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "settlement_authorized",
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


def _load_json(path: str | Path, *, label: str) -> Any:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return json.loads(resolved.read_text(encoding="utf-8"))


def _load_zero_proof_module(source: Path) -> Any:
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
        source / ZERO_PROOF_TOOL,
        "phase7_exit_settlement_zero_proof",
    )


def _nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is invalid")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} is invalid")
    return value


def _validate_reward_destinations_value(
    value: Any,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(
            "Phase 7 EXIT settlement reward destinations must be a JSON list"
        )

    destinations: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {
            "reward_index",
            "user_token_account",
        }:
            raise ValueError(
                f"Phase 7 EXIT settlement reward destination {index} schema invalid"
            )
        reward_index = item.get("reward_index")
        if (
            not isinstance(reward_index, int)
            or isinstance(reward_index, bool)
            or reward_index not in {0, 1}
        ):
            raise ValueError(
                f"Phase 7 EXIT settlement reward destination {index} index invalid"
            )
        if reward_index in seen:
            raise ValueError(
                "Phase 7 EXIT settlement reward destination index duplicated"
            )
        seen.add(reward_index)
        account = _nonempty_string(
            item.get("user_token_account"),
            label=(
                f"Phase 7 EXIT settlement reward destination "
                f"{index} token account"
            ),
        )
        destinations.append(
            {
                "reward_index": reward_index,
                "user_token_account": account,
            }
        )
    return sorted(destinations, key=lambda item: item["reward_index"])


def _load_reward_destinations(path: str | Path) -> list[dict[str, Any]]:
    value = _load_json(
        path,
        label="Phase 7 EXIT settlement reward destinations",
    )
    return _validate_reward_destinations_value(value)


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
        timeout=60,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"reviewed settlement command {args[0]} failed with return code "
            f"{completed.returncode}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"reviewed settlement command {args[0]} returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"reviewed settlement command {args[0]} returned invalid result"
        )
    return value


def _validate_reward_validations(
    rewards: Any,
) -> list[dict[str, Any]]:
    if not isinstance(rewards, list):
        raise ValueError(
            "Phase 7 EXIT settlement reward validations are invalid"
        )
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item in enumerate(rewards):
        if not isinstance(item, dict) or set(item) != {
            "reward_index",
            "mint",
            "token_program",
            "is_token_2022",
            "transfer_hook_account_count",
        }:
            raise ValueError(
                f"Phase 7 EXIT settlement reward validation {index} schema invalid"
            )
        reward_index = item.get("reward_index")
        if (
            not isinstance(reward_index, int)
            or isinstance(reward_index, bool)
            or reward_index not in {0, 1}
            or reward_index in seen
        ):
            raise ValueError(
                f"Phase 7 EXIT settlement reward validation {index} index invalid"
            )
        seen.add(reward_index)
        _nonempty_string(
            item.get("mint"),
            label=f"Phase 7 EXIT settlement reward {index} mint",
        )
        _nonempty_string(
            item.get("token_program"),
            label=f"Phase 7 EXIT settlement reward {index} token program",
        )
        if not isinstance(item.get("is_token_2022"), bool):
            raise ValueError(
                f"Phase 7 EXIT settlement reward {index} token-2022 flag invalid"
            )
        _nonnegative_int(
            item.get("transfer_hook_account_count"),
            label=f"Phase 7 EXIT settlement reward {index} hook count",
        )
        out.append(dict(item))
    return sorted(out, key=lambda item: item["reward_index"])


def validate_settlement_transaction_preparation(
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
        "zero_liquidity_snapshot_sha256",
        "executor_binary_sha256",
        "expected_executor_binary_sha256",
        "reward_destinations_sha256",
        "settlement_request_sha256",
        "chain_resolution_sha256",
        "transaction_sha256",
        "guard_config_sha256",
        "guard_report_sha256",
        "simulation_sha256",
        "preparation_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement preparation {field} is invalid"
            )

    if report["saved_zero_liquidity_proof_sha256"] != report[
        "expected_zero_liquidity_proof_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement preparation zero-liquidity digest mismatch"
        )
    if report["executor_binary_sha256"] != report[
        "expected_executor_binary_sha256"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement preparation binary trust-root mismatch"
        )

    for field in (
        "opened_decision_id",
        "exit_decision_id",
        "exit_signature",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "executor_binary_path",
        "user_token_x",
        "user_token_y",
        "token_x_program",
        "token_y_program",
        "transaction_base64",
        "dlmm_program_id",
        "claim_fee2_prefix",
        "claim_reward2_prefix",
        "close_position2_prefix",
    ):
        _nonempty_string(
            report.get(field),
            label=f"Phase 7 EXIT settlement preparation {field}",
        )

    if report["dlmm_program_id"] != DLMM_PROGRAM_ID:
        raise ValueError(
            "Phase 7 EXIT settlement preparation DLMM program mismatch"
        )
    if report["claim_fee2_prefix"] != CLAIM_FEE2_PREFIX:
        raise ValueError(
            "Phase 7 EXIT settlement preparation claim-fee prefix mismatch"
        )
    if report["claim_reward2_prefix"] != CLAIM_REWARD2_PREFIX:
        raise ValueError(
            "Phase 7 EXIT settlement preparation claim-reward prefix mismatch"
        )
    if report["close_position2_prefix"] != CLOSE_POSITION2_PREFIX:
        raise ValueError(
            "Phase 7 EXIT settlement preparation close-position prefix mismatch"
        )

    for field in (
        "zero_liquidity_capture_slot_start",
        "zero_liquidity_capture_slot_end",
        "position_width",
        "fee_transfer_hook_account_count",
        "reward_transfer_hook_account_count",
        "instruction_count",
        "simulation_rpc_context_slot",
    ):
        _nonnegative_int(
            report.get(field),
            label=f"Phase 7 EXIT settlement preparation {field}",
        )
    if report["position_width"] <= 0 or report["position_width"] > 70:
        raise ValueError(
            "Phase 7 EXIT settlement preparation position width invalid"
        )
    if report["instruction_count"] < 2 or report["instruction_count"] > 4:
        raise ValueError(
            "Phase 7 EXIT settlement preparation instruction count invalid"
        )
    if report["simulation_rpc_context_slot"] < report[
        "zero_liquidity_capture_slot_start"
    ]:
        raise ValueError(
            "Phase 7 EXIT settlement simulation predates zero-liquidity snapshot"
        )

    if not isinstance(report.get("token_x_is_2022"), bool):
        raise ValueError(
            "Phase 7 EXIT settlement preparation token_x_is_2022 invalid"
        )
    if not isinstance(report.get("token_y_is_2022"), bool):
        raise ValueError(
            "Phase 7 EXIT settlement preparation token_y_is_2022 invalid"
        )

    rewards = _validate_reward_validations(report.get("reward_validations"))
    destinations = _validate_reward_destinations_value(
        report.get("reward_token_destinations")
    )
    if report["reward_destinations_sha256"] != _sha256_value(destinations):
        raise ValueError(
            "Phase 7 EXIT settlement preparation reward-destination digest mismatch"
        )
    if report["transaction_sha256"] != _sha256_text(
        report["transaction_base64"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement preparation transaction digest mismatch"
        )
    destination_indices = [
        item["reward_index"]
        for item in destinations
    ]
    reward_indices = [item["reward_index"] for item in rewards]
    if destination_indices != reward_indices:
        raise ValueError(
            "Phase 7 EXIT settlement preparation reward destination indices mismatch"
        )
    if report.get("reward_indices_claimed") != reward_indices:
        raise ValueError(
            "Phase 7 EXIT settlement preparation claimed reward indices mismatch"
        )
    if report["instruction_count"] != 2 + len(reward_indices):
        raise ValueError(
            "Phase 7 EXIT settlement preparation instruction count/reward mismatch"
        )
    expected_reward_hooks = sum(
        item["transfer_hook_account_count"] for item in rewards
    )
    if (
        report["reward_transfer_hook_account_count"]
        != expected_reward_hooks
    ):
        raise ValueError(
            "Phase 7 EXIT settlement preparation reward hook count mismatch"
        )

    bin_arrays = report.get("bin_array_accounts")
    if not isinstance(bin_arrays, list) or not bin_arrays:
        raise ValueError(
            "Phase 7 EXIT settlement preparation bin-array accounts invalid"
        )
    if any(not isinstance(item, str) or not item for item in bin_arrays):
        raise ValueError(
            "Phase 7 EXIT settlement preparation bin-array account invalid"
        )

    for field in (
        "validation_position_matches",
        "validation_pool_matches",
        "validation_owner_matches_sender",
        "validation_zero_liquidity",
        "claims_fees",
        "closes_position_account",
        "transaction_guard_accepted",
        "guard_fee_payer_matches_executor",
        "guard_pool_account_present",
        "guard_required_accounts_present",
        "guard_transaction_unsigned",
        "guard_instruction_count_matches_build",
        "guard_single_required_signature",
        "guard_no_address_lookup_tables",
        "guard_instruction_fingerprints_valid",
        "simulation_succeeded",
        "simulation_at_or_after_zero_liquidity_snapshot",
        "settlement_transaction_prepared",
        "exact_settlement_transaction_authorization_required",
        "fresh_blockhash_required",
        "final_simulation_required",
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
        "phase7_promotion_authorized",
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


def build_settlement_transaction_preparation(
    *,
    source_tree: str | Path,
    saved_zero_liquidity_proof_path: str | Path,
    expected_zero_liquidity_proof_sha256: str,
    executor_binary_path: str | Path,
    expected_executor_binary_sha256: str,
    rpc_url: str,
    user_token_x: str,
    user_token_y: str,
    reward_destinations_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    zero_module = _load_zero_proof_module(source)

    proof = _load_json(
        saved_zero_liquidity_proof_path,
        label="saved Phase 7 EXIT zero-liquidity proof",
    )
    if not isinstance(proof, dict):
        raise ValueError(
            "saved Phase 7 EXIT zero-liquidity proof must be a JSON object"
        )
    zero_module.validate_exit_zero_liquidity_proof(proof)
    if not _is_hex_digest(expected_zero_liquidity_proof_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT zero-liquidity proof digest is invalid"
        )
    if proof["proof_sha256"] != expected_zero_liquidity_proof_sha256:
        raise ValueError(
            "saved Phase 7 EXIT zero-liquidity proof digest mismatch"
        )

    for field in (
        "post_transaction_snapshot_order_valid",
        "position_account_present",
        "pool_identity_matches",
        "owner_identity_matches",
        "fee_owner_supported_for_settlement",
        "principal_x_zero",
        "principal_y_zero",
        "all_position_liquidity_zero",
        "all_position_x_zero",
        "all_position_y_zero",
        "zero_liquidity_proven",
        "settlement_preparation_required",
        "position_account_close_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if proof.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement preparation lost zero-proof boundary {field}"
            )

    for field in (
        "settlement_authorized",
        "transaction_signing_performed",
        "transaction_submission_attempted",
        "automatic_resubmission_performed",
        "live_capital_effect_reconciled",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if proof.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT zero-liquidity proof unexpectedly authorizes {field}"
            )

    if _sha256_text(rpc_url) != proof["rpc_endpoint_sha256"]:
        raise ValueError(
            "Phase 7 EXIT settlement preparation RPC endpoint mismatch"
        )

    binary = Path(executor_binary_path).resolve(strict=True)
    if not binary.is_file():
        raise ValueError("Phase 7 executor binary must be a regular file")
    binary_sha = _sha256_bytes(binary.read_bytes())
    if not _is_hex_digest(expected_executor_binary_sha256, 64):
        raise ValueError(
            "expected Phase 7 executor binary SHA-256 is invalid"
        )
    if binary_sha != expected_executor_binary_sha256:
        raise ValueError(
            "Phase 7 executor binary differs from external trust root"
        )

    user_token_x = _nonempty_string(
        user_token_x,
        label="Phase 7 EXIT settlement user_token_x",
    )
    user_token_y = _nonempty_string(
        user_token_y,
        label="Phase 7 EXIT settlement user_token_y",
    )
    if user_token_x == user_token_y:
        raise ValueError(
            "Phase 7 EXIT settlement token destinations must be distinct"
        )
    reward_destinations = _load_reward_destinations(
        reward_destinations_path
    )

    settlement_request = {
        "position": proof["position_address"],
        "sender": proof["executor_wallet_pubkey"],
        "user_token_x": user_token_x,
        "user_token_y": user_token_y,
        "reward_token_destinations": reward_destinations,
    }
    env = _execution_env(rpc_url=rpc_url)
    resolution = _run_json(
        executor_binary=binary,
        args=[BUILD_COMMAND, "-"],
        env=env,
        stdin_text=json.dumps(settlement_request, sort_keys=True),
    )
    validation = resolution.get("validation")
    build = resolution.get("build")
    if not isinstance(validation, dict) or not isinstance(build, dict):
        raise ValueError(
            "Phase 7 EXIT settlement builder returned invalid structure"
        )

    if validation.get("position") != proof["position_address"]:
        raise ValueError(
            "Phase 7 EXIT settlement validation position mismatch"
        )
    if validation.get("lb_pair") != proof["pool_address"]:
        raise ValueError(
            "Phase 7 EXIT settlement validation pool mismatch"
        )
    if validation.get("owner_matches_sender") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement validation owner mismatch"
        )
    if validation.get("position_has_zero_liquidity") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement validation lost zero liquidity"
        )

    token_x_program = _nonempty_string(
        validation.get("token_x_program"),
        label="Phase 7 EXIT settlement token_x_program",
    )
    token_y_program = _nonempty_string(
        validation.get("token_y_program"),
        label="Phase 7 EXIT settlement token_y_program",
    )
    if not isinstance(validation.get("token_x_is_2022"), bool):
        raise ValueError(
            "Phase 7 EXIT settlement token_x_is_2022 invalid"
        )
    if not isinstance(validation.get("token_y_is_2022"), bool):
        raise ValueError(
            "Phase 7 EXIT settlement token_y_is_2022 invalid"
        )
    position_width = _nonnegative_int(
        validation.get("position_width"),
        label="Phase 7 EXIT settlement position_width",
    )
    if position_width <= 0 or position_width > 70:
        raise ValueError(
            "Phase 7 EXIT settlement position width invalid"
        )
    fee_hook_count = _nonnegative_int(
        validation.get("fee_transfer_hook_account_count"),
        label="Phase 7 EXIT settlement fee hook count",
    )
    reward_validations = _validate_reward_validations(
        validation.get("rewards")
    )
    reward_indices = [
        item["reward_index"] for item in reward_validations
    ]
    destination_indices = [
        item["reward_index"] for item in reward_destinations
    ]
    if reward_indices != destination_indices:
        raise ValueError(
            "Phase 7 EXIT settlement active reward destinations mismatch"
        )
    bin_arrays = validation.get("bin_array_accounts")
    if (
        not isinstance(bin_arrays, list)
        or not bin_arrays
        or any(not isinstance(item, str) or not item for item in bin_arrays)
    ):
        raise ValueError(
            "Phase 7 EXIT settlement bin-array coverage invalid"
        )

    if build.get("program_id") != DLMM_PROGRAM_ID:
        raise ValueError(
            "Phase 7 EXIT settlement build program mismatch"
        )
    if build.get("position") != proof["position_address"]:
        raise ValueError(
            "Phase 7 EXIT settlement build position mismatch"
        )
    if build.get("lb_pair") != proof["pool_address"]:
        raise ValueError(
            "Phase 7 EXIT settlement build pool mismatch"
        )
    if build.get("sender") != proof["executor_wallet_pubkey"]:
        raise ValueError(
            "Phase 7 EXIT settlement build sender mismatch"
        )
    if build.get("claims_fees") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement build must claim fees"
        )
    if build.get("closes_position_account") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement build must close position account"
        )
    if build.get("reward_indices_claimed") != reward_indices:
        raise ValueError(
            "Phase 7 EXIT settlement claimed reward indices mismatch"
        )
    if build.get("fee_transfer_hook_account_count") != fee_hook_count:
        raise ValueError(
            "Phase 7 EXIT settlement fee hook count mismatch"
        )
    reward_hook_total = _nonnegative_int(
        build.get("reward_transfer_hook_account_count"),
        label="Phase 7 EXIT settlement reward hook total",
    )
    if reward_hook_total != sum(
        item["transfer_hook_account_count"]
        for item in reward_validations
    ):
        raise ValueError(
            "Phase 7 EXIT settlement reward hook count mismatch"
        )
    instruction_count = _nonnegative_int(
        build.get("instruction_count"),
        label="Phase 7 EXIT settlement instruction count",
    )
    if instruction_count != 2 + len(reward_indices):
        raise ValueError(
            "Phase 7 EXIT settlement instruction count mismatch"
        )
    transaction_base64 = _nonempty_string(
        build.get("transaction_base64"),
        label="Phase 7 EXIT settlement transaction_base64",
    )

    required_accounts = [
        proof["position_address"],
        user_token_x,
        user_token_y,
        *[
            item["user_token_account"]
            for item in reward_destinations
        ],
    ]
    guard_config = {
        "expected_fee_payer": proof["executor_wallet_pubkey"],
        "allowed_program_ids": [DLMM_PROGRAM_ID],
        "max_instructions": instruction_count,
        "max_static_accounts": 128,
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
        "strategy": "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT",
        "expected_net_return_pct": 0.0,
        "expected_downside_pct": 0.0,
        "model_version": "human-authorized-exit-settlement-v1",
        "data_age_seconds": 0,
    }

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        proposal_path = root / "proposal.json"
        transaction_path = root / "settlement.base64"
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
        raise ValueError(
            "Phase 7 EXIT settlement transaction guard rejected transaction"
        )
    if guard.get("fee_payer") != proof["executor_wallet_pubkey"]:
        raise ValueError(
            "Phase 7 EXIT settlement guard fee payer mismatch"
        )
    if guard.get("pool_account_present") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement guard lost pool binding"
        )
    if guard.get("required_accounts_present") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement guard lost required account binding"
        )
    if guard.get("instruction_count") != instruction_count:
        raise ValueError(
            "Phase 7 EXIT settlement guard instruction count mismatch"
        )
    if guard.get("required_signatures") != 1:
        raise ValueError(
            "Phase 7 EXIT settlement guard requires one signer"
        )
    if guard.get("signatures_all_default") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement guard found signed transaction"
        )
    if guard.get("address_lookup_table_count") != 0:
        raise ValueError(
            "Phase 7 EXIT settlement guard found address lookup table"
        )
    if guard.get("program_ids") != [DLMM_PROGRAM_ID]:
        raise ValueError(
            "Phase 7 EXIT settlement guard program set mismatch"
        )

    fingerprints = guard.get("instruction_fingerprints")
    if not isinstance(fingerprints, list) or len(fingerprints) != instruction_count:
        raise ValueError(
            "Phase 7 EXIT settlement guard fingerprints invalid"
        )
    prefixes = []
    for item in fingerprints:
        if not isinstance(item, dict):
            raise ValueError(
                "Phase 7 EXIT settlement guard fingerprint invalid"
            )
        if item.get("program_id") != DLMM_PROGRAM_ID:
            raise ValueError(
                "Phase 7 EXIT settlement guard fingerprint program mismatch"
            )
        prefix = item.get("data_prefix_hex")
        if prefix not in {
            CLAIM_FEE2_PREFIX,
            CLAIM_REWARD2_PREFIX,
            CLOSE_POSITION2_PREFIX,
        }:
            raise ValueError(
                "Phase 7 EXIT settlement guard fingerprint prefix invalid"
            )
        prefixes.append(prefix)
    if prefixes.count(CLAIM_FEE2_PREFIX) != 1:
        raise ValueError(
            "Phase 7 EXIT settlement guard requires one claim-fee instruction"
        )
    if prefixes.count(CLOSE_POSITION2_PREFIX) != 1:
        raise ValueError(
            "Phase 7 EXIT settlement guard requires one close-position instruction"
        )
    if prefixes.count(CLAIM_REWARD2_PREFIX) != len(reward_indices):
        raise ValueError(
            "Phase 7 EXIT settlement guard reward instruction count mismatch"
        )

    simulation = _run_json(
        executor_binary=binary,
        args=[SIMULATION_COMMAND, "-"],
        env=env,
        stdin_text=transaction_base64,
    )
    if simulation.get("succeeded") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement simulation did not succeed"
        )
    simulation_slot = _nonnegative_int(
        simulation.get("rpc_context_slot"),
        label="Phase 7 EXIT settlement simulation slot",
    )
    if simulation_slot < proof["capture_slot_start"]:
        raise ValueError(
            "Phase 7 EXIT settlement simulation predates zero-liquidity snapshot"
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
        "saved_zero_liquidity_proof_sha256": proof["proof_sha256"],
        "expected_zero_liquidity_proof_sha256": (
            expected_zero_liquidity_proof_sha256
        ),
        "opened_decision_id": proof["opened_decision_id"],
        "exit_decision_id": proof["exit_decision_id"],
        "exit_signature": proof["signature"],
        "pool_address": proof["pool_address"],
        "position_address": proof["position_address"],
        "executor_wallet_pubkey": proof["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": proof["rpc_endpoint_sha256"],
        "zero_liquidity_snapshot_sha256": proof[
            "position_snapshot_sha256"
        ],
        "zero_liquidity_capture_slot_start": proof["capture_slot_start"],
        "zero_liquidity_capture_slot_end": proof["capture_slot_end"],
        "executor_binary_path": str(binary),
        "executor_binary_sha256": binary_sha,
        "expected_executor_binary_sha256": (
            expected_executor_binary_sha256
        ),
        "user_token_x": user_token_x,
        "user_token_y": user_token_y,
        "reward_token_destinations": reward_destinations,
        "reward_destinations_sha256": _sha256_value(
            reward_destinations
        ),
        "settlement_request_sha256": _sha256_value(
            settlement_request
        ),
        "chain_resolution_sha256": _sha256_value(resolution),
        "validation_position_matches": True,
        "validation_pool_matches": True,
        "validation_owner_matches_sender": True,
        "validation_zero_liquidity": True,
        "token_x_program": token_x_program,
        "token_y_program": token_y_program,
        "token_x_is_2022": validation["token_x_is_2022"],
        "token_y_is_2022": validation["token_y_is_2022"],
        "position_width": position_width,
        "fee_transfer_hook_account_count": fee_hook_count,
        "reward_validations": reward_validations,
        "bin_array_accounts": list(bin_arrays),
        "transaction_base64": transaction_base64,
        "transaction_sha256": _sha256_text(transaction_base64),
        "dlmm_program_id": DLMM_PROGRAM_ID,
        "claims_fees": True,
        "reward_indices_claimed": reward_indices,
        "closes_position_account": True,
        "reward_transfer_hook_account_count": reward_hook_total,
        "instruction_count": instruction_count,
        "claim_fee2_prefix": CLAIM_FEE2_PREFIX,
        "claim_reward2_prefix": CLAIM_REWARD2_PREFIX,
        "close_position2_prefix": CLOSE_POSITION2_PREFIX,
        "guard_config_sha256": _sha256_value(guard_config),
        "guard_report_sha256": _sha256_value(guard),
        "transaction_guard_accepted": True,
        "guard_fee_payer_matches_executor": True,
        "guard_pool_account_present": True,
        "guard_required_accounts_present": True,
        "guard_transaction_unsigned": True,
        "guard_instruction_count_matches_build": True,
        "guard_single_required_signature": True,
        "guard_no_address_lookup_tables": True,
        "guard_instruction_fingerprints_valid": True,
        "simulation_sha256": _sha256_value(simulation),
        "simulation_succeeded": True,
        "simulation_rpc_context_slot": simulation_slot,
        "simulation_at_or_after_zero_liquidity_snapshot": True,
        "settlement_transaction_prepared": True,
        "exact_settlement_transaction_authorization_required": True,
        "fresh_blockhash_required": True,
        "final_simulation_required": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "settlement_authorized": False,
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
    validate_settlement_transaction_preparation(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build, strictly guard and simulate one unsigned token-aware "
            "Phase 7 EXIT settlement transaction after zero liquidity has "
            "been proven. The transaction claims fees/rewards and closes the "
            "position account, but this tool grants no settlement authority "
            "and performs no signing or submission."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-zero-liquidity-proof", required=True)
    parser.add_argument(
        "--expected-zero-liquidity-proof-sha256",
        required=True,
    )
    parser.add_argument("--executor-binary", required=True)
    parser.add_argument("--expected-executor-binary-sha256", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--user-token-x", required=True)
    parser.add_argument("--user-token-y", required=True)
    parser.add_argument("--reward-destinations", required=True)
    args = parser.parse_args()

    report = build_settlement_transaction_preparation(
        source_tree=args.source_tree,
        saved_zero_liquidity_proof_path=args.saved_zero_liquidity_proof,
        expected_zero_liquidity_proof_sha256=(
            args.expected_zero_liquidity_proof_sha256
        ),
        executor_binary_path=args.executor_binary,
        expected_executor_binary_sha256=(
            args.expected_executor_binary_sha256
        ),
        rpc_url=args.rpc_url,
        user_token_x=args.user_token_x,
        user_token_y=args.user_token_y,
        reward_destinations_path=args.reward_destinations,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
