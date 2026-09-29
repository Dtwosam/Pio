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
    "PHASE7_CONTROLLED_LIVE_EXIT_AUTHORIZATION_REQUEST_V1"
)

READINESS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_authorization_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "7e3b9d6e67b634e2a4bad541bad32ba2f01b162a",
}

AUTHORIZATION_SCOPE = (
    "AUTHORIZE_EXIT_TRANSACTION_PREPARATION_FOR_EXACT_PHASE7_OPEN_POSITION_ONLY"
)
EXCLUDED_SCOPES = (
    "NEW_LIVE_ENTRY",
    "DIFFERENT_POSITION",
    "DIFFERENT_POOL",
    "DIFFERENT_OPENING_DECISION",
    "REBALANCE",
    "TRANSACTION_SIGNING",
    "TRANSACTION_SUBMISSION",
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
    "exit_authorization_readiness_sha256",
    "decision_verification_sha256",
    "original_lifecycle_status_sha256",
    "fresh_lifecycle_status_sha256",
    "opened_decision_id",
    "opening_signature",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "executor_binary_sha256",
    "fresh_position_snapshot_sha256",
    "fresh_capture_slot_start",
    "fresh_capture_slot_end",
    "decision_record_id",
    "decider_principal",
    "decision_issued_at",
    "decision_expires_at",
    "request_created_at",
    "human_exit_decision_verified",
    "position_still_open",
    "fresh_lifecycle_verified",
    "exit_authorization_request_ready",
    "explicit_human_exit_authorization_required",
    "authorization_must_not_outlive_decision",
    "fresh_exit_transaction_preparation_recheck_required",
    "separate_exit_transaction_required",
    "post_exit_confirmation_required",
    "post_exit_closure_proof_required",
    "post_exit_state_reconciliation_required",
    "exit_authorization_present",
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


def _load_readiness_module(source: Path) -> Any:
    path = source / READINESS_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "reviewed Phase 7 EXIT authorization-readiness tool is missing"
        )
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[READINESS_TOOL]:
        raise ValueError(
            "reviewed Phase 7 EXIT authorization-readiness blob mismatch"
        )
    return _load_module(
        path,
        "phase7_exit_authorization_request_readiness",
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


def validate_exit_authorization_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError(
            "Phase 7 EXIT authorization request must be a JSON object"
        )
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError(
            "Phase 7 EXIT authorization request schema mismatch"
        )
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT authorization request format"
        )
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT authorization request type"
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
            "Phase 7 EXIT authorization request lineage mismatch"
        )
    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "Phase 7 EXIT authorization request scope mismatch"
        )
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError(
            "Phase 7 EXIT authorization request excluded scopes mismatch"
        )

    for field in (
        "exit_authorization_readiness_sha256",
        "decision_verification_sha256",
        "original_lifecycle_status_sha256",
        "fresh_lifecycle_status_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "fresh_position_snapshot_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT authorization request {field} is invalid"
            )

    for field in (
        "opened_decision_id",
        "opening_signature",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "decision_record_id",
        "decider_principal",
        "decision_issued_at",
        "decision_expires_at",
        "request_created_at",
    ):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(
                f"Phase 7 EXIT authorization request {field} is invalid"
            )

    issued = _canonical_utc(
        request["decision_issued_at"],
        label="Phase 7 EXIT decision issued_at",
    )
    expires = _canonical_utc(
        request["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    created = _canonical_utc(
        request["request_created_at"],
        label="Phase 7 EXIT authorization request created_at",
    )
    if expires <= issued:
        raise ValueError(
            "Phase 7 EXIT authorization request decision lifetime invalid"
        )
    if created < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError(
            "Phase 7 EXIT authorization request predates decision"
        )
    if created > expires:
        raise ValueError(
            "Phase 7 EXIT authorization request decision has expired"
        )

    for field in ("fresh_capture_slot_start", "fresh_capture_slot_end"):
        value = request.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT authorization request {field} is invalid"
            )
    if request["fresh_capture_slot_end"] < request["fresh_capture_slot_start"]:
        raise ValueError(
            "Phase 7 EXIT authorization request slot range invalid"
        )

    for field in (
        "human_exit_decision_verified",
        "position_still_open",
        "fresh_lifecycle_verified",
        "exit_authorization_request_ready",
        "explicit_human_exit_authorization_required",
        "authorization_must_not_outlive_decision",
        "fresh_exit_transaction_preparation_recheck_required",
        "separate_exit_transaction_required",
        "post_exit_confirmation_required",
        "post_exit_closure_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT authorization request requires {field}=true"
            )

    for field in (
        "exit_authorization_present",
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
                f"Phase 7 EXIT authorization request requires {field}=false"
            )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if request["request_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT authorization request digest mismatch"
        )


def build_exit_authorization_request(
    *,
    source_tree: str | Path,
    saved_readiness_path: str | Path,
    expected_readiness_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    readiness_module = _load_readiness_module(source)
    readiness = _load_json(
        saved_readiness_path,
        label="saved Phase 7 EXIT authorization readiness",
    )
    readiness_module.validate_exit_authorization_readiness(readiness)

    if not _is_hex_digest(expected_readiness_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT authorization-readiness digest is invalid"
        )
    if readiness["readiness_sha256"] != expected_readiness_sha256:
        raise ValueError(
            "saved Phase 7 EXIT authorization-readiness digest mismatch"
        )

    for field in (
        "decision_signature_verified",
        "decision_not_expired",
        "fresh_lifecycle_valid",
        "fresh_snapshot_is_newer",
        "position_identity_matches",
        "position_account_present",
        "position_still_open",
        "exit_decision_present",
        "human_exit_decision_verified",
        "exit_authorization_readiness_ready",
        "explicit_human_exit_authorization_required",
    ):
        if readiness.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT authorization readiness lost {field}"
            )
    if readiness.get("position_closed_proven") is not False:
        raise ValueError(
            "Phase 7 EXIT authorization request cannot target a closed position"
        )

    issued = _canonical_utc(
        readiness["decision_issued_at"],
        label="Phase 7 EXIT decision issued_at",
    )
    expires = _canonical_utc(
        readiness["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    now_dt = (
        _canonical_utc(
            now,
            label="Phase 7 EXIT authorization request now",
        )
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    if now_dt < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError("Phase 7 EXIT decision is not yet valid")
    if now_dt > expires:
        raise ValueError("Phase 7 EXIT decision has expired")

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
                f"Phase 7 EXIT authorization readiness unexpectedly authorizes {field}"
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
        "exit_authorization_readiness_sha256": readiness[
            "readiness_sha256"
        ],
        "decision_verification_sha256": readiness[
            "saved_decision_verification_sha256"
        ],
        "original_lifecycle_status_sha256": readiness[
            "original_lifecycle_status_sha256"
        ],
        "fresh_lifecycle_status_sha256": readiness[
            "saved_fresh_lifecycle_status_sha256"
        ],
        "opened_decision_id": readiness["opened_decision_id"],
        "opening_signature": readiness["opening_signature"],
        "pool_address": readiness["pool_address"],
        "position_address": readiness["position_address"],
        "executor_wallet_pubkey": readiness["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": readiness["rpc_endpoint_sha256"],
        "executor_binary_sha256": readiness["executor_binary_sha256"],
        "fresh_position_snapshot_sha256": readiness[
            "fresh_position_snapshot_sha256"
        ],
        "fresh_capture_slot_start": readiness["fresh_capture_slot_start"],
        "fresh_capture_slot_end": readiness["fresh_capture_slot_end"],
        "decision_record_id": readiness["decision_record_id"],
        "decider_principal": readiness["decider_principal"],
        "decision_issued_at": readiness["decision_issued_at"],
        "decision_expires_at": readiness["decision_expires_at"],
        "request_created_at": _format_utc(now_dt),
        "human_exit_decision_verified": True,
        "position_still_open": True,
        "fresh_lifecycle_verified": True,
        "exit_authorization_request_ready": True,
        "explicit_human_exit_authorization_required": True,
        "authorization_must_not_outlive_decision": True,
        "fresh_exit_transaction_preparation_recheck_required": True,
        "separate_exit_transaction_required": True,
        "post_exit_confirmation_required": True,
        "post_exit_closure_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exit_authorization_present": False,
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
    validate_exit_authorization_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing human EXIT authorization request from a "
            "fresh Phase 7 EXIT authorization-readiness proof. This request "
            "does not authorize EXIT, build a transaction, sign/submit, retry, "
            "mutate production state, add capital, or promote Phase 7."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--expected-readiness-sha256", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()

    request = build_exit_authorization_request(
        source_tree=args.source_tree,
        saved_readiness_path=args.saved_readiness,
        expected_readiness_sha256=args.expected_readiness_sha256,
        now=args.now,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
