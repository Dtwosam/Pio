from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_TRANSACTION_AUTHORIZATION_REQUEST_V1"
)

FINALIZATION_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_transaction_finalization.py"
)
REVIEWED_SOURCE_BLOBS = {
    FINALIZATION_TOOL: "997ef77167b8b3443a7923083905157814cc91a7",
}

AUTHORIZATION_SCOPE = (
    "AUTHORIZE_SIGN_AND_SUBMIT_EXACT_PHASE7_EXIT_TRANSACTION_ONLY"
)
EXCLUDED_SCOPES = (
    "DIFFERENT_TRANSACTION",
    "DIFFERENT_POSITION",
    "DIFFERENT_POOL",
    "DIFFERENT_EXECUTOR_WALLET",
    "DIFFERENT_EXIT_DECISION",
    "NEW_LIVE_ENTRY",
    "REBALANCE",
    "AUTOMATIC_RESUBMISSION",
    "NEW_LIVE_CAPITAL",
    "PHASE7_PROMOTION",
)
MAX_CLOCK_SKEW_SECONDS = 30

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "excluded_scopes",
    "finalization_sha256",
    "preparation_sha256",
    "readiness_sha256",
    "opened_decision_id",
    "opening_signature",
    "exit_decision_id",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "user_token_x",
    "user_token_y",
    "final_transaction_sha256",
    "recent_blockhash",
    "last_valid_block_height",
    "prepared_rpc_context_slot",
    "exact_simulation_sha256",
    "exact_simulation_rpc_context_slot",
    "final_guard_sha256",
    "final_wallet_authorization_sha256",
    "finalizer_binary_sha256",
    "decision_expires_at",
    "preparation_authorization_expires_at",
    "request_created_at",
    "transaction_finalization_verified",
    "transaction_unsigned",
    "exact_simulation_verified",
    "preparation_authorization_not_expired",
    "decision_not_expired",
    "exact_exit_transaction_authorization_request_ready",
    "explicit_human_exact_transaction_authorization_required",
    "authorization_must_not_outlive_preparation_authorization",
    "authorization_must_not_outlive_decision",
    "fresh_blockhash_expiry_recheck_required",
    "fresh_presubmit_recheck_required",
    "separate_exit_execution_gate_required",
    "single_submission_only_required",
    "post_exit_confirmation_required",
    "post_exit_closure_proof_required",
    "post_exit_state_reconciliation_required",
    "exact_transaction_authorization_present",
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
            "reviewed Phase 7 EXIT transaction finalization tool is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[FINALIZATION_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT transaction finalization blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_transaction_authorization_request_finalization",
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


def validate_exit_transaction_authorization_request(
    request: dict[str, Any],
) -> None:
    if not isinstance(request, dict):
        raise ValueError(
            "Phase 7 EXIT transaction authorization request must be an object"
        )
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError(
            "Phase 7 EXIT transaction authorization request schema mismatch"
        )
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT transaction authorization request format"
        )
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT transaction authorization request type"
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
            "Phase 7 EXIT transaction authorization request lineage mismatch"
        )
    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "Phase 7 EXIT transaction authorization request scope mismatch"
        )
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError(
            "Phase 7 EXIT transaction authorization request excluded scopes mismatch"
        )

    for field in (
        "finalization_sha256",
        "preparation_sha256",
        "readiness_sha256",
        "final_transaction_sha256",
        "exact_simulation_sha256",
        "final_guard_sha256",
        "final_wallet_authorization_sha256",
        "finalizer_binary_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT transaction authorization request {field} is invalid"
            )

    for field in (
        "opened_decision_id",
        "opening_signature",
        "exit_decision_id",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "user_token_x",
        "user_token_y",
        "recent_blockhash",
        "decision_expires_at",
        "preparation_authorization_expires_at",
        "request_created_at",
    ):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(
                f"Phase 7 EXIT transaction authorization request {field} is invalid"
            )

    decision_expires = _canonical_utc(
        request["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    preparation_expires = _canonical_utc(
        request["preparation_authorization_expires_at"],
        label="Phase 7 EXIT preparation authorization expires_at",
    )
    created = _canonical_utc(
        request["request_created_at"],
        label="Phase 7 EXIT transaction authorization request created_at",
    )
    if preparation_expires > decision_expires:
        raise ValueError(
            "Phase 7 EXIT preparation authorization outlives decision"
        )
    if created > preparation_expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization request preparation authorization expired"
        )
    if created > decision_expires:
        raise ValueError(
            "Phase 7 EXIT transaction authorization request decision expired"
        )

    for field in (
        "last_valid_block_height",
        "prepared_rpc_context_slot",
        "exact_simulation_rpc_context_slot",
    ):
        value = request.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT transaction authorization request {field} is invalid"
            )
    if request["last_valid_block_height"] <= 0:
        raise ValueError(
            "Phase 7 EXIT transaction authorization request block-height ceiling invalid"
        )
    if (
        request["exact_simulation_rpc_context_slot"]
        < request["prepared_rpc_context_slot"]
    ):
        raise ValueError(
            "Phase 7 EXIT transaction authorization request simulation slot invalid"
        )

    for field in (
        "transaction_finalization_verified",
        "transaction_unsigned",
        "exact_simulation_verified",
        "preparation_authorization_not_expired",
        "decision_not_expired",
        "exact_exit_transaction_authorization_request_ready",
        "explicit_human_exact_transaction_authorization_required",
        "authorization_must_not_outlive_preparation_authorization",
        "authorization_must_not_outlive_decision",
        "fresh_blockhash_expiry_recheck_required",
        "fresh_presubmit_recheck_required",
        "separate_exit_execution_gate_required",
        "single_submission_only_required",
        "post_exit_confirmation_required",
        "post_exit_closure_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT transaction authorization request requires {field}=true"
            )

    for field in (
        "exact_transaction_authorization_present",
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
        if request.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT transaction authorization request requires {field}=false"
            )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if request["request_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT transaction authorization request digest mismatch"
        )


def build_exit_transaction_authorization_request(
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
        label="saved Phase 7 EXIT transaction finalization",
    )
    finalization_module.validate_exit_transaction_finalization(
        finalization
    )
    if not _is_hex_digest(expected_finalization_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT transaction finalization digest is invalid"
        )
    if finalization["finalization_sha256"] != expected_finalization_sha256:
        raise ValueError(
            "saved Phase 7 EXIT transaction finalization digest mismatch"
        )

    for field in (
        "authorization_not_expired",
        "decision_not_expired",
        "final_risk_accepted",
        "final_transaction_guard_accepted",
        "final_transaction_unsigned",
        "expected_wallet_verified",
        "exact_simulation_succeeded",
        "exit_transaction_finalized",
        "exact_exit_transaction_authorization_required",
        "fresh_blockhash_expiry_recheck_required",
        "separate_exit_execution_gate_required",
    ):
        if finalization.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT transaction finalization lost {field}"
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
        if finalization.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT transaction finalization unexpectedly authorizes {field}"
            )

    finalized = _canonical_utc(
        finalization["finalized_at"],
        label="Phase 7 EXIT transaction finalized_at",
    )
    decision_expires = _canonical_utc(
        finalization["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    preparation_expires = _canonical_utc(
        finalization["authorization_expires_at"],
        label="Phase 7 EXIT preparation authorization expires_at",
    )
    now_dt = (
        _canonical_utc(
            now,
            label="Phase 7 EXIT transaction authorization request now",
        )
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    if now_dt < finalized - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError(
            "Phase 7 EXIT transaction authorization request predates finalization"
        )
    if preparation_expires > decision_expires:
        raise ValueError(
            "Phase 7 EXIT preparation authorization outlives signed decision"
        )
    if now_dt > preparation_expires:
        raise ValueError(
            "Phase 7 EXIT preparation authorization has expired"
        )
    if now_dt > decision_expires:
        raise ValueError("Phase 7 EXIT decision has expired")

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
        "readiness_sha256": finalization["saved_readiness_sha256"],
        "opened_decision_id": finalization["opened_decision_id"],
        "opening_signature": finalization["opening_signature"],
        "exit_decision_id": finalization["exit_decision_id"],
        "pool_address": finalization["pool_address"],
        "position_address": finalization["position_address"],
        "executor_wallet_pubkey": finalization["executor_wallet_pubkey"],
        "user_token_x": finalization["user_token_x"],
        "user_token_y": finalization["user_token_y"],
        "final_transaction_sha256": finalization[
            "final_transaction_sha256"
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
        "decision_expires_at": finalization["decision_expires_at"],
        "preparation_authorization_expires_at": finalization[
            "authorization_expires_at"
        ],
        "request_created_at": _format_utc(now_dt),
        "transaction_finalization_verified": True,
        "transaction_unsigned": True,
        "exact_simulation_verified": True,
        "preparation_authorization_not_expired": True,
        "decision_not_expired": True,
        "exact_exit_transaction_authorization_request_ready": True,
        "explicit_human_exact_transaction_authorization_required": True,
        "authorization_must_not_outlive_preparation_authorization": True,
        "authorization_must_not_outlive_decision": True,
        "fresh_blockhash_expiry_recheck_required": True,
        "fresh_presubmit_recheck_required": True,
        "separate_exit_execution_gate_required": True,
        "single_submission_only_required": True,
        "post_exit_confirmation_required": True,
        "post_exit_closure_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exact_transaction_authorization_present": False,
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
    request = {
        **identity,
        "request_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_transaction_authorization_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing human request to approve signing and "
            "single-shot submission of the exact finalized Phase 7 EXIT "
            "transaction. The request performs no signing or submission."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-finalization", required=True)
    parser.add_argument("--expected-finalization-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    request = build_exit_transaction_authorization_request(
        source_tree=args.source_tree,
        saved_finalization_path=args.saved_finalization,
        expected_finalization_sha256=args.expected_finalization_sha256,
        now=args.now,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
