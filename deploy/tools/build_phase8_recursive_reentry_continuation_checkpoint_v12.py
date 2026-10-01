from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 12
ARTIFACT_TYPE = "PHASE8_RECURSIVE_REENTRY_CONTINUATION_CHECKPOINT_V12"

POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_recursive_reentry_checkpoint_v11_continuation_evidence_tick_post_audit.py"
)
REVIEWED_SOURCE_BLOBS = {
    POST_AUDIT_TOOL: "5ee8bae1fa1247a0cfb053aa220fecc0032af7c0",
}

STATE_CONTINUE = "CONTINUE"
STATE_TERMINAL = "TERMINAL"
STATE_RECOVERY = "RECOVERY"

CHECKPOINT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_post_audit_sha256",
    "source_execution_receipt_sha256",
    "source_final_evaluation_sha256",
    "source_pair_entry_post_audit_sha256",
    "pair_entry_request_sha256",
    "pair_entry_input_verification_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "previous_pair_id",
    "pair_id",
    "pool_address",
    "entry_observed_at",
    "incumbent_position_id",
    "challenger_position_id",
    "requested_position_ids",
    "pair_lineage",
    "pair_lineage_sha256",
    "latest_previous_tick_post_audit_sha256",
    "latest_tick_observed_at",
    "latest_evidence_cycle_id",
    "latest_expected_run_id",
    "pair_both_open",
    "pair_any_closed",
    "checkpoint_state",
    "next_debt_type",
    "next_scope",
    "continuation_route",
    "continuation_review_ready",
    "terminal_review_ready",
    "recovery_review_ready",
    "separate_next_action_authorization_required",
    "paper_supervisor_tick_authorized",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
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


def _is_hex_digest(value: Any, length: int = 64) -> bool:
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


def _load_post_audit(source: Path) -> Any:
    path = source / POST_AUDIT_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed recursive re-entry post-audit is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[POST_AUDIT_TOOL]:
        raise ValueError("reviewed recursive re-entry post-audit blob mismatch")
    return _load_module(
        path,
        "phase8_recursive_reentry_continuation_checkpoint_v12_post_audit",
    )


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


def _pair_lineage(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_pair_entry_post_audit_sha256": audit[
            "source_pair_entry_post_audit_sha256"
        ],
        "pair_entry_request_sha256": audit[
            "pair_entry_request_sha256"
        ],
        "pair_entry_input_verification_sha256": audit[
            "pair_entry_input_verification_sha256"
        ],
        "source_final_evaluation_sha256": audit[
            "source_final_evaluation_sha256"
        ],
        "active_cycle_id": audit["active_cycle_id"],
        "incumbent_model_id": audit["incumbent_model_id"],
        "challenger_model_id": audit["challenger_model_id"],
        "account_id": audit["account_id"],
        "previous_pair_id": audit["previous_pair_id"],
        "pair_id": audit["pair_id"],
        "pool_address": audit["pool_address"],
        "entry_observed_at": audit["entry_observed_at"],
        "incumbent_position_id": audit["incumbent_position_id"],
        "challenger_position_id": audit["challenger_position_id"],
    }


def validate_phase8_recursive_reentry_continuation_checkpoint_v12(
    checkpoint: dict[str, Any],
) -> None:
    if not isinstance(checkpoint, dict):
        raise ValueError("recursive re-entry continuation checkpoint v12 must be an object")
    if set(checkpoint) != set(CHECKPOINT_FIELDS) | {"checkpoint_sha256"}:
        raise ValueError("recursive re-entry continuation checkpoint v12 schema mismatch")
    if checkpoint.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported recursive re-entry continuation checkpoint v12 format")
    if checkpoint.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected recursive re-entry continuation checkpoint v12 type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if checkpoint.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("recursive re-entry continuation checkpoint v12 lineage mismatch")

    for field in (
        "source_post_audit_sha256",
        "source_execution_receipt_sha256",
        "source_final_evaluation_sha256",
        "source_pair_entry_post_audit_sha256",
        "pair_entry_request_sha256",
        "pair_entry_input_verification_sha256",
        "pio_database_sha256",
        "pair_lineage_sha256",
        "latest_previous_tick_post_audit_sha256",
        "checkpoint_sha256",
    ):
        if not _is_hex_digest(checkpoint.get(field)):
            raise ValueError(f"recursive re-entry continuation checkpoint v12 {field} is invalid")
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = checkpoint.get(field)
        if value is not None and not _is_hex_digest(value):
            raise ValueError(f"recursive re-entry continuation checkpoint v12 {field} is invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_id",
        "previous_pair_id",
        "pair_id",
        "pool_address",
        "entry_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "latest_tick_observed_at",
        "latest_evidence_cycle_id",
        "latest_expected_run_id",
        "checkpoint_state",
        "next_debt_type",
        "next_scope",
        "continuation_route",
    ):
        if not isinstance(checkpoint.get(field), str) or not checkpoint[field]:
            raise ValueError(f"recursive re-entry continuation checkpoint v12 {field} is invalid")

    if checkpoint["pair_id"] == checkpoint["previous_pair_id"]:
        raise ValueError("recursive re-entry continuation checkpoint v12 pair id was not advanced")
    if checkpoint.get("requested_position_ids") != [
        checkpoint["incumbent_position_id"],
        checkpoint["challenger_position_id"],
    ]:
        raise ValueError("recursive re-entry continuation checkpoint v12 position scope mismatch")

    lineage = checkpoint.get("pair_lineage")
    if not isinstance(lineage, dict):
        raise ValueError("recursive re-entry continuation checkpoint v12 pair lineage is invalid")
    expected_lineage = {
        "source_pair_entry_post_audit_sha256": checkpoint[
            "source_pair_entry_post_audit_sha256"
        ],
        "pair_entry_request_sha256": checkpoint[
            "pair_entry_request_sha256"
        ],
        "pair_entry_input_verification_sha256": checkpoint[
            "pair_entry_input_verification_sha256"
        ],
        "source_final_evaluation_sha256": checkpoint["source_final_evaluation_sha256"],
        "active_cycle_id": checkpoint["active_cycle_id"],
        "incumbent_model_id": checkpoint["incumbent_model_id"],
        "challenger_model_id": checkpoint["challenger_model_id"],
        "account_id": checkpoint["account_id"],
        "previous_pair_id": checkpoint["previous_pair_id"],
        "pair_id": checkpoint["pair_id"],
        "pool_address": checkpoint["pool_address"],
        "entry_observed_at": checkpoint["entry_observed_at"],
        "incumbent_position_id": checkpoint["incumbent_position_id"],
        "challenger_position_id": checkpoint["challenger_position_id"],
    }
    if lineage != expected_lineage:
        raise ValueError("recursive re-entry continuation checkpoint v12 pair lineage drifted")
    for field in (
        "source_pair_entry_post_audit_sha256",
        "pair_entry_request_sha256",
        "pair_entry_input_verification_sha256",
        "source_final_evaluation_sha256",
    ):
        if not _is_hex_digest(lineage.get(field)):
            raise ValueError(f"recursive re-entry continuation checkpoint v12 lineage {field} is invalid")
    if checkpoint["pair_lineage_sha256"] != _sha256_bytes(_canonical_bytes(lineage)):
        raise ValueError("recursive re-entry continuation checkpoint v12 pair lineage digest mismatch")

    flags = (
        checkpoint.get("continuation_review_ready") is True,
        checkpoint.get("terminal_review_ready") is True,
        checkpoint.get("recovery_review_ready") is True,
    )
    if sum(bool(value) for value in flags) != 1:
        raise ValueError("recursive re-entry continuation checkpoint v12 requires exactly one route")

    if checkpoint["checkpoint_state"] == STATE_CONTINUE:
        if flags != (True, False, False):
            raise ValueError("recursive re-entry continuation checkpoint v12 CONTINUE route mismatch")
        if checkpoint.get("pair_both_open") is not True:
            raise ValueError("recursive re-entry continuation checkpoint v12 CONTINUE requires open pair")
        if checkpoint.get("pair_any_closed") is not False:
            raise ValueError("recursive re-entry continuation checkpoint v12 CONTINUE forbids closed leg")
    elif checkpoint["checkpoint_state"] == STATE_TERMINAL:
        if flags != (False, True, False):
            raise ValueError("recursive re-entry continuation checkpoint v12 TERMINAL route mismatch")
        if checkpoint.get("pair_any_closed") is not True:
            raise ValueError("recursive re-entry continuation checkpoint v12 TERMINAL requires closed leg")
        if checkpoint.get("pair_both_open") is not False:
            raise ValueError("recursive re-entry continuation checkpoint v12 TERMINAL forbids open pair")
    elif checkpoint["checkpoint_state"] == STATE_RECOVERY:
        if flags != (False, False, True):
            raise ValueError("recursive re-entry continuation checkpoint v12 RECOVERY route mismatch")
    else:
        raise ValueError("recursive re-entry continuation checkpoint v12 state is invalid")

    if checkpoint.get("separate_next_action_authorization_required") is not True:
        raise ValueError(
            "recursive re-entry continuation checkpoint v12 requires separate next action authorization"
        )
    for field in (
        "paper_supervisor_tick_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if checkpoint.get(field) is not False:
            raise ValueError(
                f"recursive re-entry continuation checkpoint v12 requires {field}=false"
            )

    identity = {field: checkpoint[field] for field in CHECKPOINT_FIELDS}
    if checkpoint["checkpoint_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError("recursive re-entry continuation checkpoint v12 digest mismatch")


def build_phase8_recursive_reentry_continuation_checkpoint_v12(
    *,
    source_tree: str | Path,
    post_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    module = _load_post_audit(source)
    audit = _load_json(
        post_audit_path,
        label="recursive re-entry continuation post-audit",
    )
    module.validate_phase8_recursive_reentry_checkpoint_v11_continuation_evidence_tick_post_audit(
        audit
    )

    if audit.get("post_tick_audit_ready") is not True:
        raise ValueError("recursive re-entry continuation post-audit is not ready")
    if audit.get("separate_next_action_authorization_required") is not True:
        raise ValueError("recursive re-entry continuation post-audit lost authorization boundary")
    if audit["pair_id"] == audit["previous_pair_id"]:
        raise ValueError("recursive re-entry continuation pair id was not advanced")

    route_flags = (
        audit.get("next_evidence_tick_review_ready") is True,
        audit.get("terminal_pair_evaluation_ready") is True,
        audit.get("tick_recovery_review_ready") is True,
    )
    if sum(bool(value) for value in route_flags) != 1:
        raise ValueError("recursive re-entry continuation post-audit route is ambiguous")

    if route_flags[0]:
        state = STATE_CONTINUE
    elif route_flags[1]:
        state = STATE_TERMINAL
    else:
        state = STATE_RECOVERY

    lineage = _pair_lineage(audit)
    if audit["pair_lineage_sha256"] != _sha256_bytes(
        _canonical_bytes(lineage)
    ):
        raise ValueError(
            "recursive re-entry continuation post-audit pair lineage drifted"
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
        "source_post_audit_sha256": audit["post_audit_sha256"],
        "source_execution_receipt_sha256": audit["execution_receipt_sha256"],
        "source_final_evaluation_sha256": audit["source_final_evaluation_sha256"],
        "source_pair_entry_post_audit_sha256": lineage[
            "source_pair_entry_post_audit_sha256"
        ],
        "pair_entry_request_sha256": lineage[
            "pair_entry_request_sha256"
        ],
        "pair_entry_input_verification_sha256": lineage[
            "pair_entry_input_verification_sha256"
        ],
        "production_repository": audit["production_repository"],
        "pio_database_path": audit["pio_database_path"],
        "pio_database_sha256": audit["audit_database_sha256_after"],
        "pio_wal_sha256": audit["audit_wal_sha256_after"],
        "pio_shm_sha256": audit["audit_shm_sha256_after"],
        "active_cycle_id": audit["active_cycle_id"],
        "incumbent_model_id": audit["incumbent_model_id"],
        "challenger_model_id": audit["challenger_model_id"],
        "account_id": audit["account_id"],
        "previous_pair_id": audit["previous_pair_id"],
        "pair_id": audit["pair_id"],
        "pool_address": audit["pool_address"],
        "entry_observed_at": audit["entry_observed_at"],
        "incumbent_position_id": audit["incumbent_position_id"],
        "challenger_position_id": audit["challenger_position_id"],
        "requested_position_ids": list(audit["requested_position_ids"]),
        "pair_lineage": lineage,
        "pair_lineage_sha256": _sha256_bytes(_canonical_bytes(lineage)),
        "latest_previous_tick_post_audit_sha256": audit[
            "post_audit_sha256"
        ],
        "latest_tick_observed_at": audit["target_chain_observed_at"],
        "latest_evidence_cycle_id": audit["evidence_cycle_id"],
        "latest_expected_run_id": audit["expected_run_id"],
        "pair_both_open": audit["pair_both_open"],
        "pair_any_closed": audit["pair_any_closed"],
        "checkpoint_state": state,
        "next_debt_type": audit["next_debt_type"],
        "next_scope": audit["next_scope"],
        "continuation_route": audit["continuation_route"],
        "continuation_review_ready": route_flags[0],
        "terminal_review_ready": route_flags[1],
        "recovery_review_ready": route_flags[2],
        "separate_next_action_authorization_required": True,
        "paper_supervisor_tick_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    result = {
        **identity,
        "checkpoint_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_recursive_reentry_continuation_checkpoint_v12(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a canonical, read-only recursive re-entry continuation v8 "
            "checkpoint from the latest sealed PAPER tick post-audit."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-audit", required=True)
    args = parser.parse_args()
    value = build_phase8_recursive_reentry_continuation_checkpoint_v12(
        source_tree=args.source_tree,
        post_audit_path=args.post_audit,
    )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
