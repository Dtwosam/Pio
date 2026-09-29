from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE8_PAIRED_ML_PAPER_ENTRY_REQUEST_V1"

ACCOUNT_READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_account_readiness.py"
)
REVIEWED_SOURCE_BLOBS = {
    ACCOUNT_READINESS_TOOL: "076a9adfd2a80f959c238e27931785ed87d0fa08",
}

AUTHORIZATION_SCOPE = "OPEN_ONE_PHASE8_PAIRED_ML_PAPER_ENTRY_ONLY"
DECISION = "AUTHORIZE_ONE_PHASE8_PAIRED_ML_PAPER_ENTRY"

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "decision",
    "source_account_readiness_sha256",
    "paired_entry_input_verification_sha256",
    "source_post_audit_sha256",
    "source_paper_evidence_input_verification_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "pair_id",
    "pool_address",
    "capital_quote",
    "entry_cost_quote",
    "required_pair_cash_quote",
    "incumbent_position_id",
    "challenger_position_id",
    "incumbent_event_key",
    "challenger_event_key",
    "reason",
    "request_ready",
    "explicit_human_authorization_required",
    "fresh_account_readiness_recheck_required",
    "fresh_model_inference_readiness_required",
    "same_candidate_frame_required",
    "same_decision_snapshot_required",
    "equal_capital_required",
    "atomic_pair_open_required",
    "post_pair_audit_required",
    "paper_account_creation_authorized",
    "paper_pair_entry_authorization_present",
    "paper_pair_entry_authorized",
    "paper_pair_entry_executed",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "phase8_promotion_authorized",
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


def _load_readiness_module(source: Path) -> Any:
    path = source / ACCOUNT_READINESS_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed paired PAPER account readiness tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[ACCOUNT_READINESS_TOOL]:
        raise ValueError(
            "reviewed paired PAPER account readiness tool blob mismatch"
        )
    return _load_module(path, "phase8_paired_paper_entry_request_readiness")


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


def validate_phase8_paired_ml_paper_entry_request(
    request: dict[str, Any],
) -> None:
    if not isinstance(request, dict):
        raise ValueError("Phase 8 paired PAPER entry request must be an object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("Phase 8 paired PAPER entry request schema mismatch")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 8 paired PAPER request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 8 paired PAPER request type")
    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 8 paired PAPER request lineage mismatch")
    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("Phase 8 paired PAPER request scope mismatch")
    if request.get("decision") != DECISION:
        raise ValueError("Phase 8 paired PAPER request decision mismatch")

    for field in (
        "source_account_readiness_sha256",
        "paired_entry_input_verification_sha256",
        "source_post_audit_sha256",
        "source_paper_evidence_input_verification_sha256",
        "pio_database_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(
                f"Phase 8 paired PAPER request {field} is invalid"
            )
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = request.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 8 paired PAPER request {field} is invalid"
            )
    for field in (
        "production_repository",
        "pio_database_path",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_id",
        "pair_id",
        "pool_address",
        "incumbent_position_id",
        "challenger_position_id",
        "incumbent_event_key",
        "challenger_event_key",
        "reason",
    ):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(
                f"Phase 8 paired PAPER request {field} is invalid"
            )

    for field in (
        "request_ready",
        "explicit_human_authorization_required",
        "fresh_account_readiness_recheck_required",
        "fresh_model_inference_readiness_required",
        "same_candidate_frame_required",
        "same_decision_snapshot_required",
        "equal_capital_required",
        "atomic_pair_open_required",
        "post_pair_audit_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                f"Phase 8 paired PAPER request requires {field}=true"
            )

    for field in (
        "paper_account_creation_authorized",
        "paper_pair_entry_authorization_present",
        "paper_pair_entry_authorized",
        "paper_pair_entry_executed",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
    ):
        if request.get(field) is not False:
            raise ValueError(
                f"Phase 8 paired PAPER request requires {field}=false"
            )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    if request["request_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("Phase 8 paired PAPER request digest mismatch")


def build_phase8_paired_ml_paper_entry_request(
    *,
    source_tree: str | Path,
    account_readiness_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    module = _load_readiness_module(source)
    readiness = _load_json(
        account_readiness_path,
        label="Phase 8 paired ML PAPER account readiness",
    )
    module.validate_phase8_paired_ml_paper_account_readiness(readiness)

    if readiness.get("readiness_status") != module.STATUS_READY:
        raise ValueError(
            "Phase 8 paired PAPER entry request requires READY account state"
        )
    for field in (
        "account_ready",
        "paired_entry_preconditions_ready",
        "cycle_model_binding_valid",
        "paper_cash_sufficient_for_pair",
        "pair_position_ids_available",
        "pair_event_keys_available",
    ):
        if readiness.get(field) is not True:
            raise ValueError(
                f"Phase 8 paired PAPER entry request requires readiness {field}=true"
            )
    if readiness.get("account_creation_required") is not False:
        raise ValueError(
            "Phase 8 paired PAPER entry request refuses account creation dependency"
        )
    for field in (
        "paper_account_creation_authorized",
        "paper_pair_entry_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
    ):
        if readiness.get(field) is not False:
            raise ValueError(
                f"Phase 8 paired PAPER entry request refuses readiness {field}=true"
            )

    reason = (
        "open exactly one equal-capital ML_CHAMPION vs ML_CHALLENGER "
        "PAPER pair from one freshly resolved no-lookahead decision snapshot"
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
        "decision": DECISION,
        "source_account_readiness_sha256": readiness["readiness_sha256"],
        "paired_entry_input_verification_sha256": readiness[
            "paired_entry_input_verification_sha256"
        ],
        "source_post_audit_sha256": readiness["source_post_audit_sha256"],
        "source_paper_evidence_input_verification_sha256": readiness[
            "source_paper_evidence_input_verification_sha256"
        ],
        "production_repository": readiness["production_repository"],
        "pio_database_path": readiness["pio_database_path"],
        "pio_database_sha256": readiness["pio_database_sha256"],
        "pio_wal_sha256": readiness["pio_wal_sha256"],
        "pio_shm_sha256": readiness["pio_shm_sha256"],
        "active_cycle_id": readiness["active_cycle_id"],
        "incumbent_model_id": readiness["incumbent_model_id"],
        "challenger_model_id": readiness["challenger_model_id"],
        "account_id": readiness["account_id"],
        "pair_id": readiness["pair_id"],
        "pool_address": readiness["pool_address"],
        "capital_quote": readiness["capital_quote"],
        "entry_cost_quote": readiness["entry_cost_quote"],
        "required_pair_cash_quote": readiness["required_pair_cash_quote"],
        "incumbent_position_id": readiness["incumbent_position_id"],
        "challenger_position_id": readiness["challenger_position_id"],
        "incumbent_event_key": readiness["incumbent_event_key"],
        "challenger_event_key": readiness["challenger_event_key"],
        "reason": reason,
        "request_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_account_readiness_recheck_required": True,
        "fresh_model_inference_readiness_required": True,
        "same_candidate_frame_required": True,
        "same_decision_snapshot_required": True,
        "equal_capital_required": True,
        "atomic_pair_open_required": True,
        "post_pair_audit_required": True,
        "paper_account_creation_authorized": False,
        "paper_pair_entry_authorization_present": False,
        "paper_pair_entry_authorized": False,
        "paper_pair_entry_executed": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
    }
    request = {
        **identity,
        "request_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_entry_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request for exactly one fair paired "
            "ML_CHAMPION vs ML_CHALLENGER PAPER entry from a READY account. "
            "The request requires fresh inference readiness and a separate "
            "human signature before any PAPER position can be opened."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--account-readiness", required=True)
    args = parser.parse_args()

    request = build_phase8_paired_ml_paper_entry_request(
        source_tree=args.source_tree,
        account_readiness_path=args.account_readiness,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
