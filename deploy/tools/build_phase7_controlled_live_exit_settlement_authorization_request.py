from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_SETTLEMENT_AUTHORIZATION_REQUEST_V1"
)
FINALIZATION_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_settlement_finalization.py"
)
REVIEWED_SOURCE_BLOBS = {
    FINALIZATION_TOOL: "91cf46a93eb5f03443bf3ddd191cb8a9d1250f32",
}

AUTHORIZATION_SCOPE = (
    "AUTHORIZE_SIGN_AND_SUBMIT_EXACT_PHASE7_EXIT_SETTLEMENT_TRANSACTION_ONLY"
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
    "NEW_LIVE_CAPITAL",
    "PHASE7_PROMOTION",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "excluded_scopes",
    "finalization_sha256",
    "preparation_sha256",
    "opened_decision_id",
    "exit_decision_id",
    "exit_signature",
    "zero_liquidity_snapshot_sha256",
    "zero_liquidity_capture_slot_start",
    "zero_liquidity_capture_slot_end",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "destination_config_sha256",
    "user_token_x",
    "user_token_y",
    "reward_token_destinations",
    "final_settlement_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "prepared_rpc_context_slot",
    "exact_simulation_sha256",
    "exact_simulation_rpc_context_slot",
    "final_guard_sha256",
    "final_wallet_authorization_sha256",
    "finalizer_binary_sha256",
    "request_created_at",
    "settlement_finalization_verified",
    "transaction_unsigned",
    "exact_simulation_verified",
    "zero_liquidity_lineage_verified",
    "exact_settlement_transaction_authorization_request_ready",
    "explicit_human_exact_transaction_authorization_required",
    "fresh_blockhash_expiry_recheck_required",
    "single_submission_only_required",
    "post_settlement_confirmation_required",
    "post_close_account_absence_proof_required",
    "post_exit_state_reconciliation_required",
    "exact_transaction_authorization_present",
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


def _load_finalization_module(source: Path) -> Any:
    path = source / FINALIZATION_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 7 EXIT settlement finalization tool is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[FINALIZATION_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT settlement finalization blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_settlement_authorization_request_finalization",
    )


def _canonical_utc(raw: Any, *, label: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError(f"{label} must use UTC Z form")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            f"{label} must use YYYY-MM-DDTHH:MM:SSZ"
        ) from exc


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_exit_settlement_authorization_request(
    request: dict[str, Any],
) -> None:
    if not isinstance(request, dict):
        raise ValueError(
            "Phase 7 EXIT settlement authorization request must be an object"
        )
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError(
            "Phase 7 EXIT settlement authorization request schema mismatch"
        )
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT settlement authorization request format"
        )
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT settlement authorization request type"
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
            "Phase 7 EXIT settlement authorization request lineage mismatch"
        )
    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "Phase 7 EXIT settlement authorization request scope mismatch"
        )
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError(
            "Phase 7 EXIT settlement authorization request excluded scopes mismatch"
        )

    for field in (
        "finalization_sha256",
        "preparation_sha256",
        "zero_liquidity_snapshot_sha256",
        "rpc_endpoint_sha256",
        "destination_config_sha256",
        "final_settlement_transaction_sha256",
        "exact_simulation_sha256",
        "final_guard_sha256",
        "final_wallet_authorization_sha256",
        "finalizer_binary_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT settlement authorization request {field} is invalid"
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
        "recent_blockhash",
        "request_created_at",
    ):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(
                f"Phase 7 EXIT settlement authorization request {field} is invalid"
            )

    _canonical_utc(
        request["request_created_at"],
        label="Phase 7 EXIT settlement authorization request created_at",
    )

    for field in (
        "zero_liquidity_capture_slot_start",
        "zero_liquidity_capture_slot_end",
        "last_valid_block_height",
        "prepared_rpc_context_slot",
        "exact_simulation_rpc_context_slot",
    ):
        value = request.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT settlement authorization request {field} is invalid"
            )
    if request["last_valid_block_height"] <= 0:
        raise ValueError(
            "Phase 7 EXIT settlement authorization request block-height ceiling invalid"
        )
    if (
        request["zero_liquidity_capture_slot_end"]
        < request["zero_liquidity_capture_slot_start"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement authorization request zero-liquidity slot range invalid"
        )
    if (
        request["prepared_rpc_context_slot"]
        < request["zero_liquidity_capture_slot_start"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement authorization request finalization predates zero-liquidity proof"
        )
    if (
        request["exact_simulation_rpc_context_slot"]
        < request["prepared_rpc_context_slot"]
    ):
        raise ValueError(
            "Phase 7 EXIT settlement authorization request simulation slot invalid"
        )
    if not isinstance(request.get("reward_token_destinations"), list):
        raise ValueError(
            "Phase 7 EXIT settlement authorization reward destinations invalid"
        )

    for field in (
        "settlement_finalization_verified",
        "transaction_unsigned",
        "exact_simulation_verified",
        "zero_liquidity_lineage_verified",
        "exact_settlement_transaction_authorization_request_ready",
        "explicit_human_exact_transaction_authorization_required",
        "fresh_blockhash_expiry_recheck_required",
        "single_submission_only_required",
        "post_settlement_confirmation_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement authorization request requires {field}=true"
            )

    for field in (
        "exact_transaction_authorization_present",
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
                f"Phase 7 EXIT settlement authorization request requires {field}=false"
            )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if request["request_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT settlement authorization request digest mismatch"
        )


def build_exit_settlement_authorization_request(
    *,
    source_tree: str | Path,
    saved_finalization_path: str | Path,
    expected_finalization_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    finalization_module = _load_finalization_module(source)

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

    for field in (
        "final_risk_accepted",
        "final_transaction_guard_accepted",
        "final_guard_instruction_sequence_valid",
        "final_transaction_unsigned",
        "expected_wallet_verified",
        "exact_simulation_succeeded",
        "settlement_transaction_finalized",
        "exact_settlement_transaction_authorization_required",
        "fresh_blockhash_expiry_recheck_required",
        "single_submission_only_required",
        "post_settlement_confirmation_required",
        "post_close_account_absence_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if finalization.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT settlement finalization lost {field}"
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
        if finalization.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT settlement finalization unexpectedly authorizes {field}"
            )

    now_dt = (
        _canonical_utc(
            now,
            label="Phase 7 EXIT settlement authorization request now",
        )
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
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
        "authorization_scope": AUTHORIZATION_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "finalization_sha256": finalization["finalization_sha256"],
        "preparation_sha256": finalization["saved_preparation_sha256"],
        "opened_decision_id": finalization["opened_decision_id"],
        "exit_decision_id": finalization["exit_decision_id"],
        "exit_signature": finalization["exit_signature"],
        "zero_liquidity_snapshot_sha256": finalization[
            "zero_liquidity_snapshot_sha256"
        ],
        "zero_liquidity_capture_slot_start": finalization[
            "zero_liquidity_capture_slot_start"
        ],
        "zero_liquidity_capture_slot_end": finalization[
            "zero_liquidity_capture_slot_end"
        ],
        "pool_address": finalization["pool_address"],
        "position_address": finalization["position_address"],
        "executor_wallet_pubkey": finalization["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": finalization["rpc_endpoint_sha256"],
        "destination_config_sha256": finalization[
            "destination_config_sha256"
        ],
        "user_token_x": finalization["user_token_x"],
        "user_token_y": finalization["user_token_y"],
        "reward_token_destinations": finalization[
            "reward_token_destinations"
        ],
        "final_settlement_transaction_sha256": finalization[
            "final_settlement_transaction_sha256"
        ],
        "recent_blockhash": finalization["recent_blockhash"],
        "last_valid_block_height": finalization[
            "last_valid_block_height"
        ],
        "prepared_rpc_context_slot": finalization[
            "prepared_rpc_context_slot"
        ],
        "exact_simulation_sha256": finalization[
            "exact_simulation_sha256"
        ],
        "exact_simulation_rpc_context_slot": finalization[
            "exact_simulation_rpc_context_slot"
        ],
        "final_guard_sha256": finalization["final_guard_sha256"],
        "final_wallet_authorization_sha256": finalization[
            "final_wallet_authorization_sha256"
        ],
        "finalizer_binary_sha256": finalization[
            "finalizer_binary_sha256"
        ],
        "request_created_at": _format_utc(now_dt),
        "settlement_finalization_verified": True,
        "transaction_unsigned": True,
        "exact_simulation_verified": True,
        "zero_liquidity_lineage_verified": True,
        "exact_settlement_transaction_authorization_request_ready": True,
        "explicit_human_exact_transaction_authorization_required": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "single_submission_only_required": True,
        "post_settlement_confirmation_required": True,
        "post_close_account_absence_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exact_transaction_authorization_present": False,
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
    validate_exit_settlement_authorization_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing human request to approve signing and "
            "single-shot submission of the exact finalized Phase 7 EXIT "
            "settlement transaction. No signing or submission occurs."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-finalization", required=True)
    parser.add_argument("--expected-finalization-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    request = build_exit_settlement_authorization_request(
        source_tree=args.source_tree,
        saved_finalization_path=args.saved_finalization,
        expected_finalization_sha256=args.expected_finalization_sha256,
        now=args.now,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
