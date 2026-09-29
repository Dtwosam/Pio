from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_SINGLE_EXECUTION_REQUEST_V1"
)

ADMISSION_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_settlement_execution_admission.py"
)
FINALIZATION_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_settlement_finalization.py"
)
REVIEWED_SOURCE_BLOBS = {
    ADMISSION_TOOL: "a86978eed31d82daab759e46a3fbf96cd7c02809",
    FINALIZATION_TOOL: "91cf46a93eb5f03443bf3ddd191cb8a9d1250f32",
}

EXECUTION_SCOPE = (
    "SUBMIT_EXACT_VERIFIED_PHASE7_EXIT_SETTLEMENT_TRANSACTION_ONCE_ONLY"
)
EXCLUDED_SCOPES = (
    "DIFFERENT_TRANSACTION",
    "DIFFERENT_POSITION",
    "DIFFERENT_POOL",
    "DIFFERENT_EXECUTOR_WALLET",
    "DIFFERENT_DESTINATION_CONFIG",
    "NEW_LIVE_ENTRY",
    "REBALANCE",
    "AUTOMATIC_RESUBMISSION",
    "UNATTENDED_RETRY",
    "NEW_LIVE_CAPITAL",
    "PHASE7_PROMOTION",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "execution_scope",
    "excluded_scopes",
    "admission_sha256",
    "finalization_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "exit_signature",
    "zero_liquidity_snapshot_sha256",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "destination_config_sha256",
    "user_token_x",
    "user_token_y",
    "reward_token_destinations",
    "rpc_endpoint_sha256",
    "final_settlement_transaction_base64",
    "final_settlement_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "fresh_current_block_height",
    "fresh_block_height_remaining",
    "executor_binary_sha256",
    "exact_settlement_authorization_verified",
    "keypair_identity_verified",
    "blockhash_not_expired",
    "final_transaction_unsigned",
    "exact_simulation_succeeded",
    "single_execution_request_ready",
    "runtime_live_submit_opt_in_required",
    "dedicated_submission_journal_required",
    "atomic_first_submission_claim_required",
    "signature_persist_before_rpc_send_required",
    "rpc_max_retries_zero_required",
    "automatic_retry_prohibited",
    "uncertain_rpc_requires_recovery",
    "post_settlement_confirmation_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "separate_phase7_promotion_required",
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


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT settlement single-execution dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT settlement single-execution dependency mismatch: {relative}"
            )
    admission = _load_module(
        source / ADMISSION_TOOL,
        "phase7_exit_settlement_single_execution_admission",
    )
    finalization = _load_module(
        source / FINALIZATION_TOOL,
        "phase7_exit_settlement_single_execution_finalization",
    )
    return admission, finalization


def validate_exit_settlement_single_execution_request(
    request: dict[str, Any],
) -> None:
    if not isinstance(request, dict):
        raise ValueError(
            "Phase 7 EXIT settlement single-execution request must be an object"
        )
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement single-execution request schema mismatch"
        )
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement single-execution request format"
        )
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement single-execution request type"
        )
    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "Phase 7 EXIT settlement single-execution lineage mismatch"
        )
    if request.get("execution_scope") != EXECUTION_SCOPE:
        raise ValueError(
            "Phase 7 EXIT settlement single-execution scope mismatch"
        )
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError(
            "Phase 7 EXIT settlement single-execution excluded scopes mismatch"
        )

    for field in (
        "admission_sha256",
        "finalization_sha256",
        "zero_liquidity_snapshot_sha256",
        "destination_config_sha256",
        "rpc_endpoint_sha256",
        "final_settlement_transaction_sha256",
        "executor_binary_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement single-execution {field} is invalid"
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
        "final_settlement_transaction_base64",
        "recent_blockhash",
    ):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(
                f"Phase 7 EXIT settlement single-execution {field} is invalid"
            )
    if not isinstance(request.get("reward_token_destinations"), list):
        raise ValueError(
            "Phase 7 EXIT settlement single-execution reward destinations invalid"
        )

    for field in (
        "last_valid_block_height",
        "fresh_current_block_height",
        "fresh_block_height_remaining",
    ):
        value = request.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT settlement single-execution {field} is invalid"
            )
    if request["last_valid_block_height"] <= 0:
        raise ValueError(
            "Phase 7 EXIT settlement single-execution block-height ceiling invalid"
        )
    if request["fresh_current_block_height"] > request["last_valid_block_height"]:
        raise ValueError(
            "Phase 7 EXIT settlement single-execution blockhash expired"
        )
    if (
        request["fresh_block_height_remaining"]
        != request["last_valid_block_height"]
        - request["fresh_current_block_height"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement single-execution block-height remainder mismatch"
        )

    for field in (
        "exact_settlement_authorization_verified",
        "keypair_identity_verified",
        "blockhash_not_expired",
        "final_transaction_unsigned",
        "exact_simulation_succeeded",
        "single_execution_request_ready",
        "runtime_live_submit_opt_in_required",
        "dedicated_submission_journal_required",
        "atomic_first_submission_claim_required",
        "signature_persist_before_rpc_send_required",
        "rpc_max_retries_zero_required",
        "automatic_retry_prohibited",
        "uncertain_rpc_requires_recovery",
        "post_settlement_confirmation_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
        "separate_phase7_promotion_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement single-execution requires {field}=true"
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
        if request.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement single-execution requires {field}=false"
            )

    if _sha256_text(request["final_settlement_transaction_base64"]) != (
        request["final_settlement_transaction_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement single-execution transaction digest mismatch"
        )

    destination_config = {
        "user_token_x": request["user_token_x"],
        "user_token_y": request["user_token_y"],
        "reward_token_destinations": request["reward_token_destinations"],
    }
    if request["destination_config_sha256"] != _sha256_bytes(
        _canonical_bytes(destination_config)
    ):
        raise ValueError(
            "Phase 7 EXIT settlement single-execution destination digest mismatch"
        )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    if request["request_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError(
            "Phase 7 EXIT settlement single-execution digest mismatch"
        )


def build_exit_settlement_single_execution_request(
    *,
    source_tree: str | Path,
    saved_admission_path: str | Path,
    expected_admission_sha256: str,
    saved_finalization_path: str | Path,
    expected_finalization_sha256: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    admission_module, finalization_module = _load_reviewed(source)

    admission = _load_json(
        saved_admission_path,
        label="saved Phase 7 EXIT settlement execution admission",
    )
    admission_module.validate_exit_settlement_execution_admission(admission)
    if not _is_hex_digest(expected_admission_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT settlement admission digest is invalid"
        )
    if admission["admission_sha256"] != expected_admission_sha256:
        raise ValueError(
            "saved Phase 7 EXIT settlement admission digest mismatch"
        )

    finalization = _load_json(
        saved_finalization_path,
        label="saved Phase 7 EXIT settlement finalization",
    )
    finalization_module.validate_exit_settlement_finalization(finalization)
    if not _is_hex_digest(expected_finalization_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT settlement finalization digest is invalid"
        )
    if finalization["finalization_sha256"] != expected_finalization_sha256:
        raise ValueError(
            "saved Phase 7 EXIT settlement finalization digest mismatch"
        )

    for admission_field, finalization_field in (
        ("opened_decision_id", "opened_decision_id"),
        ("exit_decision_id", "exit_decision_id"),
        ("exit_signature", "exit_signature"),
        ("pool_address", "pool_address"),
        ("position_address", "position_address"),
        ("executor_wallet_pubkey", "executor_wallet_pubkey"),
        ("destination_config_sha256", "destination_config_sha256"),
        (
            "final_settlement_transaction_sha256",
            "final_settlement_transaction_sha256",
        ),
        ("recent_blockhash", "recent_blockhash"),
        ("last_valid_block_height", "last_valid_block_height"),
        ("rpc_endpoint_sha256", "rpc_endpoint_sha256"),
    ):
        if admission.get(admission_field) != finalization.get(
            finalization_field
        ):
            raise ValueError(
                f"Phase 7 EXIT settlement single-execution {admission_field} binding mismatch"
            )

    for field in (
        "executor_keypair_identity_verified",
        "blockhash_not_expired",
        "fresh_readiness_valid",
        "human_exact_settlement_transaction_authorization_verified",
        "transaction_authorization_not_expired",
        "final_transaction_still_unsigned",
        "final_exact_simulation_still_succeeded",
        "settlement_execution_admission_ready",
        "requires_immediate_single_execution",
        "requires_fresh_blockhash_recheck_at_execution",
        "requires_separate_single_shot_submitter",
        "requires_post_settlement_confirmation",
        "requires_post_close_account_absence_proof",
        "requires_post_exit_state_reconciliation",
    ):
        if admission.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement admission lost {field}"
            )
    if finalization.get("final_transaction_unsigned") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement finalized transaction is no longer unsigned"
        )
    if finalization.get("exact_simulation_succeeded") is not True:
        raise ValueError(
            "Phase 7 EXIT settlement exact simulation is no longer successful"
        )

    for artifact, label in (
        (admission, "settlement execution admission"),
        (finalization, "settlement finalization"),
    ):
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
            if artifact.get(field) is not False:
                raise ValueError(
                    f"{label} unexpectedly authorizes {field}"
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
        "execution_scope": EXECUTION_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "admission_sha256": admission["admission_sha256"],
        "finalization_sha256": finalization["finalization_sha256"],
        "opened_decision_id": finalization["opened_decision_id"],
        "exit_decision_id": finalization["exit_decision_id"],
        "exit_signature": finalization["exit_signature"],
        "zero_liquidity_snapshot_sha256": finalization[
            "zero_liquidity_snapshot_sha256"
        ],
        "pool_address": finalization["pool_address"],
        "position_address": finalization["position_address"],
        "executor_wallet_pubkey": finalization["executor_wallet_pubkey"],
        "destination_config_sha256": finalization[
            "destination_config_sha256"
        ],
        "user_token_x": finalization["user_token_x"],
        "user_token_y": finalization["user_token_y"],
        "reward_token_destinations": finalization[
            "reward_token_destinations"
        ],
        "rpc_endpoint_sha256": finalization["rpc_endpoint_sha256"],
        "final_settlement_transaction_base64": finalization[
            "final_settlement_transaction_base64"
        ],
        "final_settlement_transaction_sha256": finalization[
            "final_settlement_transaction_sha256"
        ],
        "recent_blockhash": finalization["recent_blockhash"],
        "last_valid_block_height": finalization[
            "last_valid_block_height"
        ],
        "fresh_current_block_height": admission[
            "fresh_current_block_height"
        ],
        "fresh_block_height_remaining": admission[
            "fresh_block_height_remaining"
        ],
        "executor_binary_sha256": admission["executor_binary_sha256"],
        "exact_settlement_authorization_verified": True,
        "keypair_identity_verified": True,
        "blockhash_not_expired": True,
        "final_transaction_unsigned": True,
        "exact_simulation_succeeded": True,
        "single_execution_request_ready": True,
        "runtime_live_submit_opt_in_required": True,
        "dedicated_submission_journal_required": True,
        "atomic_first_submission_claim_required": True,
        "signature_persist_before_rpc_send_required": True,
        "rpc_max_retries_zero_required": True,
        "automatic_retry_prohibited": True,
        "uncertain_rpc_requires_recovery": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "separate_phase7_promotion_required": True,
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
    request = {
        **identity,
        "request_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_settlement_single_execution_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Seal one exact Phase 7 EXIT settlement transaction and its "
            "keypair-identity admission into a settlement-specific single-shot "
            "execution request. No signing or submission occurs."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-admission", required=True)
    parser.add_argument("--expected-admission-sha256", required=True)
    parser.add_argument("--saved-finalization", required=True)
    parser.add_argument("--expected-finalization-sha256", required=True)
    args = parser.parse_args()

    request = build_exit_settlement_single_execution_request(
        source_tree=args.source_tree,
        saved_admission_path=args.saved_admission,
        expected_admission_sha256=args.expected_admission_sha256,
        saved_finalization_path=args.saved_finalization,
        expected_finalization_sha256=args.expected_finalization_sha256,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
