from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V24_CONTINUATION_EVIDENCE_BUNDLE_V1"
)

CHECKPOINT_TOOL = Path(
    "deploy/tools/build_phase8_recursive_reentry_continuation_checkpoint_v24.py"
)
SUPERVISION_READINESS_TOOL = Path(
    "deploy/tools/check_phase8_recursive_reentry_checkpoint_v24_continuation_supervision_readiness.py"
)
REQUEST_TOOL = Path(
    "deploy/tools/build_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_request.py"
)
SIGNER_TOOL = Path(
    "deploy/tools/build_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_signed_authorization.py"
)
EXECUTION_READINESS_TOOL = Path(
    "deploy/tools/check_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_execution_readiness.py"
)
EXECUTOR_TOOL = Path(
    "deploy/tools/run_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_once.py"
)
POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_post_audit.py"
)

REVIEWED_SOURCE_BLOBS = {
    CHECKPOINT_TOOL: "3879b85ba74fe2197029cd054d37e3d2eeb84993",
    SUPERVISION_READINESS_TOOL: "370552ebefbefede3f795a5723e1c67d80bb7f0a",
    REQUEST_TOOL: "8c7c0ad59007717f6a96afeac0a5510e60e2a77d",
    SIGNER_TOOL: "839e264814505474bc3afb08a0fa4282d69e7f38",
    EXECUTION_READINESS_TOOL: "2368086290409e4bf9c93947610e77941865440b",
    EXECUTOR_TOOL: "2f50a572e601829869e6d106dfd4f4f033672ba5",
    POST_AUDIT_TOOL: "e4a950c721d956bc70a417d8ac34816115a5eae0",
}

ARTIFACT_NAMES = (
    "checkpoint",
    "continuation_readiness",
    "request",
    "signed_authorization_verification",
    "execution_readiness",
    "execution_receipt",
    "post_audit",
)

BUNDLE_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "artifact_file_sha256",
    "production_repository",
    "pio_database_path",
    "checkpoint_sha256",
    "continuation_readiness_sha256",
    "request_sha256",
    "signed_authorization_verification_sha256",
    "execution_readiness_sha256",
    "execution_receipt_sha256",
    "post_audit_sha256",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "previous_pair_id",
    "pair_id",
    "pool_address",
    "entry_observed_at",
    "previous_tick_observed_at",
    "incumbent_position_id",
    "challenger_position_id",
    "requested_position_ids",
    "evidence_cycle_id",
    "expected_run_id",
    "target_chain_observed_at",
    "approval_payload_sha256",
    "approval_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "authorization_expires_at",
    "receipt_tick_status",
    "receipt_pair_tick_complete",
    "receipt_pair_tick_partial_failure",
    "pair_both_open",
    "pair_any_closed",
    "next_debt_type",
    "next_scope",
    "continuation_route",
    "next_evidence_tick_review_ready",
    "tick_recovery_review_ready",
    "terminal_pair_evaluation_ready",
    "current_database_sha256",
    "current_wal_sha256",
    "current_shm_sha256",
    "database_matches_final_audit",
    "lineage_verified",
    "all_native_validators_passed",
    "bundle_read_only",
    "requires_separate_next_action_authorization",
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


def _load_reviewed(source: Path) -> dict[str, Any]:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"checkpoint v24 evidence bundle dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"checkpoint v24 evidence bundle dependency mismatch: {relative}"
            )

    return {
        "checkpoint": _load_module(
            source / CHECKPOINT_TOOL,
            "phase8_v24_bundle_checkpoint",
        ),
        "continuation_readiness": _load_module(
            source / SUPERVISION_READINESS_TOOL,
            "phase8_v24_bundle_continuation_readiness",
        ),
        "request": _load_module(
            source / REQUEST_TOOL,
            "phase8_v24_bundle_request",
        ),
        "signed_authorization_verification": _load_module(
            source / SIGNER_TOOL,
            "phase8_v24_bundle_signer",
        ),
        "execution_readiness": _load_module(
            source / EXECUTION_READINESS_TOOL,
            "phase8_v24_bundle_execution_readiness",
        ),
        "execution_receipt": _load_module(
            source / EXECUTOR_TOOL,
            "phase8_v24_bundle_executor",
        ),
        "post_audit": _load_module(
            source / POST_AUDIT_TOOL,
            "phase8_v24_bundle_post_audit",
        ),
    }


def _regular_file(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _load_json(path: str | Path, *, label: str) -> tuple[dict[str, Any], str]:
    resolved = _regular_file(path, label=label)
    payload = resolved.read_bytes()
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value, _sha256_bytes(payload)


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"unsafe Pio database state file: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _require_equal(label: str, *values: Any) -> Any:
    if not values:
        raise ValueError(f"{label} has no values")
    first = values[0]
    if any(value != first for value in values[1:]):
        raise ValueError(f"checkpoint v24 evidence bundle {label} mismatch")
    return first


def _require_false(report: dict[str, Any], *fields: str) -> None:
    for field in fields:
        if report.get(field) is not False:
            raise ValueError(
                f"checkpoint v24 evidence bundle requires {field}=false"
            )


def validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("checkpoint v24 evidence bundle must be an object")
    if set(report) != set(BUNDLE_FIELDS) | {"bundle_sha256"}:
        raise ValueError("checkpoint v24 evidence bundle schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint v24 evidence bundle format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected checkpoint v24 evidence bundle type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("checkpoint v24 evidence bundle source lineage mismatch")

    artifact_hashes = report.get("artifact_file_sha256")
    if (
        not isinstance(artifact_hashes, dict)
        or set(artifact_hashes) != set(ARTIFACT_NAMES)
    ):
        raise ValueError("checkpoint v24 evidence bundle artifact hashes are invalid")
    for value in artifact_hashes.values():
        if not _is_hex_digest(value, 64):
            raise ValueError(
                "checkpoint v24 evidence bundle artifact hash is invalid"
            )

    for field in (
        "checkpoint_sha256",
        "continuation_readiness_sha256",
        "request_sha256",
        "signed_authorization_verification_sha256",
        "execution_readiness_sha256",
        "execution_receipt_sha256",
        "post_audit_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "current_database_sha256",
        "bundle_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"checkpoint v24 evidence bundle {field} is invalid"
            )
    for field in ("current_wal_sha256", "current_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"checkpoint v24 evidence bundle {field} is invalid"
            )

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
        "previous_tick_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "evidence_cycle_id",
        "expected_run_id",
        "target_chain_observed_at",
        "approver_principal",
        "approval_id",
        "authorization_expires_at",
        "receipt_tick_status",
        "next_debt_type",
        "next_scope",
        "continuation_route",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"checkpoint v24 evidence bundle {field} is invalid"
            )

    if report.get("requested_position_ids") != [
        report["incumbent_position_id"],
        report["challenger_position_id"],
    ]:
        raise ValueError(
            "checkpoint v24 evidence bundle position scope mismatch"
        )

    for field in (
        "database_matches_final_audit",
        "lineage_verified",
        "all_native_validators_passed",
        "bundle_read_only",
        "requires_separate_next_action_authorization",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"checkpoint v24 evidence bundle requires {field}=true"
            )

    _require_false(
        report,
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

    complete = report.get("receipt_pair_tick_complete")
    partial = report.get("receipt_pair_tick_partial_failure")
    if not isinstance(complete, bool) or not isinstance(partial, bool):
        raise ValueError(
            "checkpoint v24 evidence bundle receipt outcome is invalid"
        )
    if complete == partial:
        raise ValueError(
            "checkpoint v24 evidence bundle receipt outcome is inconsistent"
        )

    for field in (
        "pair_both_open",
        "pair_any_closed",
        "next_evidence_tick_review_ready",
        "tick_recovery_review_ready",
        "terminal_pair_evaluation_ready",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(
                f"checkpoint v24 evidence bundle {field} is invalid"
            )

    identity = {field: report[field] for field in BUNDLE_FIELDS}
    if report["bundle_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError("checkpoint v24 evidence bundle digest mismatch")


def build_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle(
    *,
    repository: str | Path,
    source_tree: str | Path,
    checkpoint_path: str | Path,
    continuation_readiness_path: str | Path,
    request_path: str | Path,
    signed_authorization_verification_path: str | Path,
    execution_readiness_path: str | Path,
    execution_receipt_path: str | Path,
    post_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    modules = _load_reviewed(source)
    paths = {
        "checkpoint": checkpoint_path,
        "continuation_readiness": continuation_readiness_path,
        "request": request_path,
        "signed_authorization_verification": (
            signed_authorization_verification_path
        ),
        "execution_readiness": execution_readiness_path,
        "execution_receipt": execution_receipt_path,
        "post_audit": post_audit_path,
    }
    artifacts: dict[str, dict[str, Any]] = {}
    raw_hashes: dict[str, str] = {}
    for name, path in paths.items():
        value, raw_sha = _load_json(path, label=name.replace("_", " "))
        artifacts[name] = value
        raw_hashes[name] = raw_sha

    checkpoint = artifacts["checkpoint"]
    continuation = artifacts["continuation_readiness"]
    request = artifacts["request"]
    signed = artifacts["signed_authorization_verification"]
    execution_readiness = artifacts["execution_readiness"]
    receipt = artifacts["execution_receipt"]
    post_audit = artifacts["post_audit"]

    modules["checkpoint"].validate_phase8_recursive_reentry_continuation_checkpoint_v24(
        checkpoint
    )
    modules[
        "continuation_readiness"
    ].validate_phase8_recursive_reentry_checkpoint_v24_continuation_supervision_readiness(
        continuation
    )
    modules[
        "request"
    ].validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_request(
        request
    )
    modules["signed_authorization_verification"].validate_verification(signed)
    modules[
        "execution_readiness"
    ].validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_execution_readiness(
        execution_readiness
    )
    modules[
        "execution_receipt"
    ].validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_execution_receipt(
        receipt
    )
    modules[
        "post_audit"
    ].validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_tick_post_audit(
        post_audit
    )

    if checkpoint.get("checkpoint_state") != "CONTINUE":
        raise ValueError("checkpoint v24 evidence bundle requires CONTINUE checkpoint")
    if continuation.get("readiness_status") != "READY":
        raise ValueError("checkpoint v24 evidence bundle requires READY continuation")
    if request.get("request_ready") is not True:
        raise ValueError("checkpoint v24 evidence bundle request is not ready")
    if execution_readiness.get(
        "one_pair_evidence_tick_execution_readiness_ready"
    ) is not True:
        raise ValueError(
            "checkpoint v24 evidence bundle execution readiness is not ready"
        )
    if receipt.get("paper_supervisor_tick_executed") is not True:
        raise ValueError("checkpoint v24 evidence bundle tick was not executed")
    if post_audit.get("post_tick_audit_ready") is not True:
        raise ValueError("checkpoint v24 evidence bundle post-audit is not ready")

    checkpoint_sha = checkpoint["checkpoint_sha256"]
    continuation_sha = continuation["readiness_sha256"]
    request_sha = request["request_sha256"]
    signed_sha = signed["verification_sha256"]
    execution_readiness_sha = execution_readiness["readiness_sha256"]
    receipt_sha = receipt["receipt_sha256"]
    post_audit_sha = post_audit["post_audit_sha256"]

    _require_equal(
        "checkpoint digest",
        checkpoint_sha,
        continuation["source_checkpoint_sha256"],
        request["source_checkpoint_sha256"],
        signed["source_checkpoint_sha256"],
        execution_readiness["source_checkpoint_sha256"],
        receipt["source_checkpoint_sha256"],
        post_audit["source_checkpoint_sha256"],
    )
    _require_equal(
        "continuation readiness digest",
        continuation_sha,
        request["source_continuation_supervision_readiness_sha256"],
        signed["source_continuation_supervision_readiness_sha256"],
        execution_readiness["saved_continuation_supervision_readiness_sha256"],
        execution_readiness["fresh_continuation_supervision_readiness_sha256"],
    )
    _require_equal(
        "request digest",
        request_sha,
        signed["request_sha256"],
        execution_readiness["saved_request_sha256"],
        execution_readiness["fresh_request_sha256"],
        receipt["saved_request_sha256"],
    )
    _require_equal(
        "signed authorization verification digest",
        signed_sha,
        execution_readiness["saved_signed_authorization_verification_sha256"],
        execution_readiness["fresh_signed_authorization_verification_sha256"],
        receipt["signed_authorization_verification_sha256"],
    )
    _require_equal(
        "execution readiness digest",
        execution_readiness_sha,
        receipt["saved_readiness_sha256"],
        receipt["fresh_readiness_sha256"],
    )
    _require_equal(
        "execution receipt digest",
        receipt_sha,
        post_audit["execution_receipt_sha256"],
    )

    stable_lineage_fields = (
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
    )
    for field in stable_lineage_fields:
        _require_equal(
            field,
            checkpoint[field],
            continuation[field],
            request[field],
            signed[field],
            execution_readiness[field],
            receipt[field],
            post_audit[field],
        )

    _require_equal(
        "previous tick",
        checkpoint["latest_tick_observed_at"],
        continuation["previous_tick_observed_at"],
        request["previous_tick_observed_at"],
        signed["previous_tick_observed_at"],
        execution_readiness["previous_tick_observed_at"],
        receipt["previous_tick_observed_at"],
        post_audit["previous_tick_observed_at"],
    )
    for field in ("evidence_cycle_id", "target_chain_observed_at"):
        _require_equal(
            field,
            request[field],
            signed[field],
            execution_readiness[field],
            receipt[field],
            post_audit[field],
        )
    _require_equal(
        "expected run id",
        execution_readiness["expected_run_id"],
        receipt["expected_run_id"],
        post_audit["expected_run_id"],
    )

    historical_lineage_fields = (
        "source_post_audit_sha256",
        "source_execution_receipt_sha256",
        "source_pair_entry_post_audit_sha256",
        "pair_entry_request_sha256",
        "pair_entry_input_verification_sha256",
        "pair_lineage_sha256",
        "latest_previous_tick_post_audit_sha256",
        "source_final_evaluation_sha256",
    )
    for field in historical_lineage_fields:
        _require_equal(
            field,
            checkpoint[field],
            continuation[field],
            request[field],
            signed[field],
            execution_readiness[field],
            receipt[field],
            post_audit[field],
        )

    _require_equal(
        "approval payload digest",
        signed["approval_payload_sha256"],
        receipt["approval_payload_sha256"],
    )
    _require_equal(
        "approval signature digest",
        signed["approval_signature_sha256"],
        receipt["approval_signature_sha256"],
    )
    _require_equal(
        "allowed signers digest",
        signed["allowed_signers_sha256"],
        receipt["allowed_signers_sha256"],
    )
    _require_equal(
        "approver principal",
        signed["approver_principal"],
        receipt["approver_principal"],
    )
    _require_equal("approval id", signed["approval_id"], receipt["approval_id"])
    _require_equal(
        "authorization expiry",
        signed["expires_at"],
        receipt["authorization_expires_at"],
    )

    for artifact in (
        checkpoint,
        continuation,
        request,
        signed,
        execution_readiness,
        receipt,
        post_audit,
    ):
        if Path(artifact["production_repository"]).resolve() != production:
            raise ValueError(
                "checkpoint v24 evidence bundle production repository mismatch"
            )

    database = (production / "data" / "pio.db").resolve(strict=True)
    for artifact in (
        checkpoint,
        continuation,
        request,
        signed,
        execution_readiness,
        receipt,
        post_audit,
    ):
        if Path(artifact["pio_database_path"]).resolve() != database:
            raise ValueError(
                "checkpoint v24 evidence bundle database path mismatch"
            )

    _require_equal(
        "pre-execution database digest",
        continuation["pio_database_sha256"],
        request["pio_database_sha256"],
        signed["pio_database_sha256"],
        execution_readiness["pio_database_sha256"],
        receipt["pio_database_sha256_before"],
    )
    _require_equal(
        "post-execution database digest",
        receipt["pio_database_sha256_after"],
        post_audit["receipt_database_sha256_after"],
        post_audit["audit_database_sha256_before"],
        post_audit["audit_database_sha256_after"],
    )
    _require_equal(
        "post-execution WAL digest",
        receipt["pio_wal_sha256_after"],
        post_audit["receipt_wal_sha256_after"],
        post_audit["audit_wal_sha256_before"],
        post_audit["audit_wal_sha256_after"],
    )
    _require_equal(
        "post-execution SHM digest",
        receipt["pio_shm_sha256_after"],
        post_audit["receipt_shm_sha256_after"],
        post_audit["audit_shm_sha256_before"],
        post_audit["audit_shm_sha256_after"],
    )

    current = _database_state(database)
    if current["database"] != post_audit["audit_database_sha256_after"]:
        raise ValueError(
            "Pio database changed after checkpoint v24 post-audit"
        )
    if current["wal"] != post_audit["audit_wal_sha256_after"]:
        raise ValueError("Pio WAL changed after checkpoint v24 post-audit")
    if current["shm"] != post_audit["audit_shm_sha256_after"]:
        raise ValueError("Pio SHM changed after checkpoint v24 post-audit")

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
        "artifact_file_sha256": raw_hashes,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "checkpoint_sha256": checkpoint_sha,
        "continuation_readiness_sha256": continuation_sha,
        "request_sha256": request_sha,
        "signed_authorization_verification_sha256": signed_sha,
        "execution_readiness_sha256": execution_readiness_sha,
        "execution_receipt_sha256": receipt_sha,
        "post_audit_sha256": post_audit_sha,
        "active_cycle_id": post_audit["active_cycle_id"],
        "incumbent_model_id": post_audit["incumbent_model_id"],
        "challenger_model_id": post_audit["challenger_model_id"],
        "account_id": post_audit["account_id"],
        "previous_pair_id": post_audit["previous_pair_id"],
        "pair_id": post_audit["pair_id"],
        "pool_address": post_audit["pool_address"],
        "entry_observed_at": post_audit["entry_observed_at"],
        "previous_tick_observed_at": post_audit["previous_tick_observed_at"],
        "incumbent_position_id": post_audit["incumbent_position_id"],
        "challenger_position_id": post_audit["challenger_position_id"],
        "requested_position_ids": post_audit["requested_position_ids"],
        "evidence_cycle_id": post_audit["evidence_cycle_id"],
        "expected_run_id": post_audit["expected_run_id"],
        "target_chain_observed_at": post_audit["target_chain_observed_at"],
        "approval_payload_sha256": signed["approval_payload_sha256"],
        "approval_signature_sha256": signed["approval_signature_sha256"],
        "allowed_signers_sha256": signed["allowed_signers_sha256"],
        "approver_principal": signed["approver_principal"],
        "approval_id": signed["approval_id"],
        "authorization_expires_at": signed["expires_at"],
        "receipt_tick_status": post_audit["receipt_tick_status"],
        "receipt_pair_tick_complete": post_audit[
            "receipt_pair_tick_complete"
        ],
        "receipt_pair_tick_partial_failure": post_audit[
            "receipt_pair_tick_partial_failure"
        ],
        "pair_both_open": post_audit["pair_both_open"],
        "pair_any_closed": post_audit["pair_any_closed"],
        "next_debt_type": post_audit["next_debt_type"],
        "next_scope": post_audit["next_scope"],
        "continuation_route": post_audit["continuation_route"],
        "next_evidence_tick_review_ready": post_audit[
            "next_evidence_tick_review_ready"
        ],
        "tick_recovery_review_ready": post_audit[
            "tick_recovery_review_ready"
        ],
        "terminal_pair_evaluation_ready": post_audit[
            "terminal_pair_evaluation_ready"
        ],
        "current_database_sha256": current["database"],
        "current_wal_sha256": current["wal"],
        "current_shm_sha256": current["shm"],
        "database_matches_final_audit": True,
        "lineage_verified": True,
        "all_native_validators_passed": True,
        "bundle_read_only": True,
        "requires_separate_next_action_authorization": True,
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
    bundle = {
        **identity,
        "bundle_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle(
        bundle
    )
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and seal the complete checkpoint v24 continuation PAPER "
            "evidence chain into one read-only manifest. This does not execute "
            "another tick, sign an authorization, mutate production state, "
            "submit transactions, or authorize Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--continuation-readiness", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--signed-verification", required=True)
    parser.add_argument("--execution-readiness", required=True)
    parser.add_argument("--execution-receipt", required=True)
    parser.add_argument("--post-audit", required=True)
    args = parser.parse_args()

    bundle = build_phase8_recursive_reentry_checkpoint_v24_continuation_evidence_bundle(
        repository=args.repo,
        source_tree=args.source_tree,
        checkpoint_path=args.checkpoint,
        continuation_readiness_path=args.continuation_readiness,
        request_path=args.request,
        signed_authorization_verification_path=args.signed_verification,
        execution_readiness_path=args.execution_readiness,
        execution_receipt_path=args.execution_receipt,
        post_audit_path=args.post_audit,
    )
    print(json.dumps(bundle, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
