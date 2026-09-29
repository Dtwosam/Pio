from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_DECISION_REQUEST_V1"

LIFECYCLE_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_open_position_lifecycle.py"
)
REVIEWED_SOURCE_BLOBS = {
    LIFECYCLE_TOOL: "5149cc8707479a19b8331a8a13b14344fdad9b84",
}

DECISION_SCOPE = "DECIDE_EXIT_FOR_EXACT_PHASE7_OPEN_POSITION_ONLY"
EXCLUDED_SCOPES = (
    "NEW_LIVE_ENTRY",
    "DIFFERENT_POSITION",
    "DIFFERENT_POOL",
    "DIFFERENT_OPENING_DECISION",
    "REBALANCE",
    "TRANSACTION_CONSTRUCTION",
    "TRANSACTION_SIGNING",
    "TRANSACTION_SUBMISSION",
    "AUTOMATIC_RESUBMISSION",
    "NEW_LIVE_CAPITAL",
    "PHASE7_PROMOTION",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "decision_scope",
    "excluded_scopes",
    "lifecycle_status_sha256",
    "opened_decision_id",
    "opening_signature",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "executor_binary_path",
    "executor_binary_sha256",
    "position_snapshot_sha256",
    "capture_slot_start",
    "capture_slot_end",
    "lower_bin_id",
    "upper_bin_id",
    "total_x_amount",
    "total_y_amount",
    "fee_x",
    "fee_y",
    "reward_one",
    "reward_two",
    "bin_count",
    "position_account_present",
    "position_closed_proven",
    "open_position_snapshot_ready",
    "lifecycle_route",
    "exit_decision_request_ready",
    "explicit_human_exit_decision_required",
    "fresh_lifecycle_recheck_required",
    "separate_exit_authorization_required",
    "separate_exit_transaction_required",
    "post_exit_confirmation_required",
    "post_exit_closure_proof_required",
    "post_exit_state_reconciliation_required",
    "exit_decision_present",
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


def _load_lifecycle_module(source: Path) -> Any:
    path = source / LIFECYCLE_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 7 lifecycle tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[LIFECYCLE_TOOL]:
        raise ValueError("reviewed Phase 7 lifecycle tool blob mismatch")
    return _load_module(path, "phase7_exit_decision_request_lifecycle")


def validate_exit_decision_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError("Phase 7 EXIT decision request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("Phase 7 EXIT decision request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 EXIT decision request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 EXIT decision request type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 EXIT decision request lineage mismatch")
    if request.get("decision_scope") != DECISION_SCOPE:
        raise ValueError("Phase 7 EXIT decision request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError("Phase 7 EXIT decision request excluded scopes mismatch")

    for field in (
        "lifecycle_status_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "position_snapshot_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT decision request {field} is invalid"
            )

    for field in (
        "opened_decision_id",
        "opening_signature",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "executor_binary_path",
        "total_x_amount",
        "total_y_amount",
        "fee_x",
        "fee_y",
        "reward_one",
        "reward_two",
    ):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(
                f"Phase 7 EXIT decision request {field} is invalid"
            )

    for field in (
        "capture_slot_start",
        "capture_slot_end",
        "lower_bin_id",
        "upper_bin_id",
        "bin_count",
    ):
        value = request.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"Phase 7 EXIT decision request {field} is invalid"
            )
    if request["capture_slot_start"] < 0:
        raise ValueError("Phase 7 EXIT decision request start slot is invalid")
    if request["capture_slot_end"] < request["capture_slot_start"]:
        raise ValueError("Phase 7 EXIT decision request slot range is invalid")
    if request["bin_count"] < 0:
        raise ValueError("Phase 7 EXIT decision request bin count is invalid")

    if request.get("lifecycle_route") != "OPEN_POSITION_OBSERVATION":
        raise ValueError("Phase 7 EXIT decision request lifecycle route mismatch")

    for field in (
        "position_account_present",
        "open_position_snapshot_ready",
        "exit_decision_request_ready",
        "explicit_human_exit_decision_required",
        "fresh_lifecycle_recheck_required",
        "separate_exit_authorization_required",
        "separate_exit_transaction_required",
        "post_exit_confirmation_required",
        "post_exit_closure_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT decision request requires {field}=true"
            )

    if request.get("position_closed_proven") is not False:
        raise ValueError(
            "Phase 7 EXIT decision request requires position_closed_proven=false"
        )

    for field in (
        "exit_decision_present",
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
                f"Phase 7 EXIT decision request requires {field}=false"
            )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if request["request_sha256"] != expected:
        raise ValueError("Phase 7 EXIT decision request digest mismatch")


def build_exit_decision_request(
    *,
    source_tree: str | Path,
    saved_lifecycle_status_path: str | Path,
    expected_lifecycle_status_sha256: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    lifecycle_module = _load_lifecycle_module(source)
    lifecycle = _load_json(
        saved_lifecycle_status_path,
        label="saved Phase 7 open-position lifecycle status",
    )
    lifecycle_module.validate_open_position_lifecycle_status(lifecycle)

    if not _is_hex_digest(expected_lifecycle_status_sha256, 64):
        raise ValueError("expected Phase 7 lifecycle digest is invalid")
    if lifecycle["lifecycle_status_sha256"] != expected_lifecycle_status_sha256:
        raise ValueError("saved Phase 7 lifecycle digest mismatch")

    if lifecycle.get("lifecycle_route") != "OPEN_POSITION_OBSERVATION":
        raise ValueError(
            "Phase 7 EXIT decision request requires open-position observation"
        )
    if lifecycle.get("position_account_present") is not True:
        raise ValueError(
            "Phase 7 EXIT decision request requires the position account"
        )
    if lifecycle.get("position_closed_proven") is not False:
        raise ValueError(
            "Phase 7 EXIT decision request cannot target a closed position"
        )
    if lifecycle.get("open_position_snapshot_ready") is not True:
        raise ValueError(
            "Phase 7 EXIT decision request requires a ready open-position snapshot"
        )
    if lifecycle.get("requires_separate_exit_decision") is not True:
        raise ValueError(
            "Phase 7 lifecycle lost the separate EXIT-decision boundary"
        )

    for field in (
        "new_live_entry_authorized",
        "exit_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if lifecycle.get(field) is not False:
            raise ValueError(
                f"Phase 7 lifecycle unexpectedly authorizes {field}"
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
        "decision_scope": DECISION_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "lifecycle_status_sha256": lifecycle["lifecycle_status_sha256"],
        "opened_decision_id": lifecycle["decision_id"],
        "opening_signature": lifecycle["signature"],
        "pool_address": lifecycle["pool_address"],
        "position_address": lifecycle["position_address"],
        "executor_wallet_pubkey": lifecycle["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": lifecycle["rpc_endpoint_sha256"],
        "executor_binary_path": lifecycle["executor_binary_path"],
        "executor_binary_sha256": lifecycle["executor_binary_sha256"],
        "position_snapshot_sha256": lifecycle["position_snapshot_sha256"],
        "capture_slot_start": lifecycle["capture_slot_start"],
        "capture_slot_end": lifecycle["capture_slot_end"],
        "lower_bin_id": lifecycle["lower_bin_id"],
        "upper_bin_id": lifecycle["upper_bin_id"],
        "total_x_amount": lifecycle["total_x_amount"],
        "total_y_amount": lifecycle["total_y_amount"],
        "fee_x": lifecycle["fee_x"],
        "fee_y": lifecycle["fee_y"],
        "reward_one": lifecycle["reward_one"],
        "reward_two": lifecycle["reward_two"],
        "bin_count": lifecycle["bin_count"],
        "position_account_present": True,
        "position_closed_proven": False,
        "open_position_snapshot_ready": True,
        "lifecycle_route": lifecycle["lifecycle_route"],
        "exit_decision_request_ready": True,
        "explicit_human_exit_decision_required": True,
        "fresh_lifecycle_recheck_required": True,
        "separate_exit_authorization_required": True,
        "separate_exit_transaction_required": True,
        "post_exit_confirmation_required": True,
        "post_exit_closure_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exit_decision_present": False,
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
    validate_exit_decision_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing human EXIT decision request for the exact "
            "reconciled Phase 7 open position. This tool does not decide EXIT, "
            "construct a transaction, sign, submit, retry, mutate the Pio "
            "database, or authorize Phase 7 promotion."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-lifecycle-status", required=True)
    parser.add_argument("--expected-lifecycle-status-sha256", required=True)
    args = parser.parse_args()

    request = build_exit_decision_request(
        source_tree=args.source_tree,
        saved_lifecycle_status_path=args.saved_lifecycle_status,
        expected_lifecycle_status_sha256=(
            args.expected_lifecycle_status_sha256
        ),
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
