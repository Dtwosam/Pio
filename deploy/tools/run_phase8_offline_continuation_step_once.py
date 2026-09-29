from __future__ import annotations

import argparse
import fcntl
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
    "PHASE8_OFFLINE_CONTINUATION_ONE_SHOT_EXECUTION_RECEIPT_V1"
)

READINESS_TOOL = Path(
    "deploy/tools/check_phase8_offline_continuation_execution_readiness.py"
)
ORIGINAL_EXECUTOR_TOOL = Path(
    "deploy/tools/run_phase8_offline_step_once.py"
)

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "efb0c6cacd7e243d34c90682540d09523b879c66",
    ORIGINAL_EXECUTOR_TOOL: "02d0a3369309dd91d90bd891e7e62882d7577d8e",
}

ALLOWED_DEBT_TYPES = {
    "RETRAIN_DATASET_BUILD_READY",
    "RETRAIN_OFFLINE_TRAIN_READY",
    "RETRAIN_OFFLINE_VALIDATION_READY",
}
LOCK_PATH = Path("/var/tmp/pio-phase8-offline-step-one-shot.lock")

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_readiness_sha256",
    "fresh_readiness_sha256",
    "saved_readiness_stable_sha256",
    "fresh_readiness_stable_sha256",
    "source_post_audit_sha256",
    "source_execution_receipt_sha256",
    "source_offline_step_request_sha256",
    "continuation_request_sha256",
    "signed_authorization_verification_sha256",
    "approval_payload_sha256",
    "approval_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "authorization_expires_at",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "research_artifacts_before",
    "research_artifacts_after",
    "research_artifact_changes",
    "research_artifact_change_count",
    "production_research_artifacts_modified",
    "debt_type",
    "scope",
    "planner_before",
    "planner_before_sha256",
    "planner_after",
    "planner_after_sha256",
    "operation_status",
    "operation",
    "operation_sha256",
    "error",
    "fresh_readiness_matches_saved",
    "human_continuation_offline_step_authorization_verified",
    "exact_planner_action_matched",
    "one_step_only",
    "research_only",
    "offline_only",
    "policy_actionable",
    "execution_wired",
    "offline_step_execution_authorized",
    "offline_step_attempted",
    "offline_step_executed",
    "offline_step_execution_completed",
    "step_progressed",
    "requires_post_step_audit",
    "paper_challenger_transition_performed",
    "paper_trading_authorized",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_performed",
    "transaction_submission_performed",
    "automatic_resubmission_performed",
    "new_live_capital_used",
    "phase8_policy_action_authorized",
    "phase8_execution_authorized",
    "phase8_promotion_authorized",
    "phase8_promotion_persisted",
    "production_source_file_modified",
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
    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_reviewed(source: Path) -> tuple[Any, Any, dict[str, Any]]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                "Phase 8 continuation executor dependency missing: "
                f"{relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                "Phase 8 continuation executor dependency mismatch: "
                f"{relative}"
            )

    readiness = _load_module(
        source / READINESS_TOOL,
        "phase8_continuation_one_shot_readiness",
    )
    original_executor = _load_module(
        source / ORIGINAL_EXECUTOR_TOOL,
        "phase8_continuation_one_shot_original_executor",
    )
    _, runtime = original_executor._load_reviewed(source)
    return readiness, original_executor, runtime


def _stable_readiness_projection(
    value: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key not in {
            "readiness_sha256",
            "fresh_post_audit_sha256",
        }
    }


def _stable_readiness_sha256(value: dict[str, Any]) -> str:
    return _sha256_bytes(
        _canonical_bytes(_stable_readiness_projection(value))
    )


def _next_action(plan: dict[str, Any]) -> dict[str, Any] | None:
    value = plan.get("next_action")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 8 continuation executor planner next_action is invalid"
        )
    return value


def _validate_artifact_inventory(
    artifacts: Any,
    *,
    original_executor: Any,
    label: str,
) -> None:
    if not isinstance(artifacts, list):
        raise ValueError(
            f"Phase 8 continuation executor {label} must be a list"
        )
    allowed_roots = tuple(
        getattr(
            original_executor,
            "ALLOWED_ARTIFACT_ROOTS",
            (),
        )
    )
    if not allowed_roots:
        raise ValueError(
            "reviewed Phase 8 executor artifact roots are unavailable"
        )
    for item in artifacts:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "size_bytes", "sha256"}
            or not isinstance(item["path"], str)
            or not item["path"]
            or Path(item["path"]).is_absolute()
            or ".." in Path(item["path"]).parts
            or not any(
                item["path"] == root
                or item["path"].startswith(root + "/")
                for root in allowed_roots
            )
            or not isinstance(item["size_bytes"], int)
            or isinstance(item["size_bytes"], bool)
            or item["size_bytes"] < 0
            or not _is_hex_digest(item["sha256"], 64)
        ):
            raise ValueError(
                f"Phase 8 continuation executor {label} entry is invalid"
            )


def validate_phase8_offline_continuation_execution_receipt(
    receipt: dict[str, Any],
    *,
    original_executor: Any | None = None,
) -> None:
    if not isinstance(receipt, dict):
        raise ValueError(
            "Phase 8 continuation one-shot receipt must be an object"
        )
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError(
            "Phase 8 continuation one-shot receipt schema mismatch"
        )
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 8 continuation receipt format"
        )
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 8 continuation receipt type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "Phase 8 continuation receipt lineage mismatch"
        )

    for field in (
        "saved_readiness_sha256",
        "fresh_readiness_sha256",
        "saved_readiness_stable_sha256",
        "fresh_readiness_stable_sha256",
        "source_post_audit_sha256",
        "source_execution_receipt_sha256",
        "source_offline_step_request_sha256",
        "continuation_request_sha256",
        "signed_authorization_verification_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "planner_before_sha256",
        "planner_after_sha256",
        "operation_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(receipt.get(field), 64):
            raise ValueError(
                f"Phase 8 continuation receipt {field} is invalid"
            )

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = receipt.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 8 continuation receipt {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "debt_type",
        "scope",
        "approver_principal",
        "approval_id",
        "authorization_expires_at",
        "operation_status",
    ):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(
                f"Phase 8 continuation receipt {field} is invalid"
            )

    if receipt["debt_type"] not in ALLOWED_DEBT_TYPES:
        raise ValueError(
            "Phase 8 continuation receipt debt type is not allowed"
        )
    if receipt["operation_status"] not in {
        "COMPLETE",
        "NOT_QUALIFIED",
        "FAILED",
    }:
        raise ValueError(
            "Phase 8 continuation receipt operation status is invalid"
        )

    for field in ("planner_before", "planner_after"):
        value = receipt.get(field)
        if not isinstance(value, dict):
            raise ValueError(
                f"Phase 8 continuation receipt {field} is invalid"
            )
        if value.get("research_only") is not True:
            raise ValueError(
                f"Phase 8 continuation {field} must be research-only"
            )
        if value.get("policy_actionable") is not False:
            raise ValueError(
                f"Phase 8 continuation {field} became policy-actionable"
            )
        if value.get("execution_wired") is not False:
            raise ValueError(
                f"Phase 8 continuation {field} became execution-wired"
            )

    if receipt["planner_before_sha256"] != _sha256_bytes(
        _canonical_bytes(receipt["planner_before"])
    ):
        raise ValueError(
            "Phase 8 continuation planner-before digest mismatch"
        )
    if receipt["planner_after_sha256"] != _sha256_bytes(
        _canonical_bytes(receipt["planner_after"])
    ):
        raise ValueError(
            "Phase 8 continuation planner-after digest mismatch"
        )

    operation = receipt.get("operation")
    if operation is not None and not isinstance(operation, dict):
        raise ValueError(
            "Phase 8 continuation receipt operation is invalid"
        )
    if receipt["operation_sha256"] != _sha256_bytes(
        _canonical_bytes(operation)
    ):
        raise ValueError(
            "Phase 8 continuation operation digest mismatch"
        )

    if (
        receipt["saved_readiness_stable_sha256"]
        != receipt["fresh_readiness_stable_sha256"]
    ):
        raise ValueError(
            "Phase 8 continuation stable readiness mismatch"
        )

    for field in (
        "fresh_readiness_matches_saved",
        "human_continuation_offline_step_authorization_verified",
        "exact_planner_action_matched",
        "one_step_only",
        "research_only",
        "offline_only",
        "offline_step_execution_authorized",
        "offline_step_attempted",
        "offline_step_executed",
        "requires_post_step_audit",
    ):
        if receipt.get(field) is not True:
            raise ValueError(
                "Phase 8 continuation receipt requires "
                f"{field}=true"
            )

    for field in (
        "policy_actionable",
        "execution_wired",
        "paper_challenger_transition_performed",
        "paper_trading_authorized",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_performed",
        "transaction_submission_performed",
        "automatic_resubmission_performed",
        "new_live_capital_used",
        "phase8_policy_action_authorized",
        "phase8_execution_authorized",
        "phase8_promotion_authorized",
        "phase8_promotion_persisted",
        "production_source_file_modified",
        "production_repository_git_mutated",
    ):
        if receipt.get(field) is not False:
            raise ValueError(
                "Phase 8 continuation receipt requires "
                f"{field}=false"
            )

    completed = receipt["operation_status"] in {
        "COMPLETE",
        "NOT_QUALIFIED",
    }
    if receipt.get("offline_step_execution_completed") is not completed:
        raise ValueError(
            "Phase 8 continuation completion/status binding mismatch"
        )

    if receipt["operation_status"] == "FAILED":
        if not isinstance(receipt.get("error"), str) or not receipt["error"]:
            raise ValueError(
                "Phase 8 continuation failed receipt needs error"
            )
        if receipt.get("operation") is not None:
            raise ValueError(
                "Phase 8 continuation failed receipt must not claim output"
            )
    else:
        if receipt.get("error") is not None:
            raise ValueError(
                "Phase 8 continuation completed receipt has error"
            )
        if not isinstance(receipt.get("operation"), dict):
            raise ValueError(
                "Phase 8 continuation completed receipt needs output"
            )

    if original_executor is not None:
        for field in (
            "research_artifacts_before",
            "research_artifacts_after",
        ):
            _validate_artifact_inventory(
                receipt.get(field),
                original_executor=original_executor,
                label=field,
            )
        expected_changes = original_executor._artifact_changes(
            receipt["research_artifacts_before"],
            receipt["research_artifacts_after"],
        )
        if receipt.get("research_artifact_changes") != expected_changes:
            raise ValueError(
                "Phase 8 continuation artifact changes mismatch"
            )
        if receipt.get("research_artifact_change_count") != len(
            expected_changes
        ):
            raise ValueError(
                "Phase 8 continuation artifact change count mismatch"
            )
        artifacts_modified = bool(expected_changes)
        if receipt.get(
            "production_research_artifacts_modified"
        ) is not artifacts_modified:
            raise ValueError(
                "Phase 8 continuation artifact modified binding mismatch"
            )

        db_modified = (
            receipt["pio_database_sha256_before"]
            != receipt["pio_database_sha256_after"]
            or receipt["pio_wal_sha256_before"]
            != receipt["pio_wal_sha256_after"]
            or receipt["pio_shm_sha256_before"]
            != receipt["pio_shm_sha256_after"]
        )
        if receipt.get("production_pio_database_modified") is not db_modified:
            raise ValueError(
                "Phase 8 continuation DB-modified binding mismatch"
            )

        progressed = (
            receipt["operation_status"] == "COMPLETE"
            and original_executor._planner_progressed(
                receipt["planner_before"],
                receipt["planner_after"],
            )
        )
        if receipt.get("step_progressed") is not progressed:
            raise ValueError(
                "Phase 8 continuation planner-progress binding mismatch"
            )

    before_action = _next_action(receipt["planner_before"])
    if not isinstance(before_action, dict):
        raise ValueError(
            "Phase 8 continuation planner-before action is missing"
        )
    if (
        before_action.get("debt_type") != receipt["debt_type"]
        or before_action.get("scope") != receipt["scope"]
    ):
        raise ValueError(
            "Phase 8 continuation planner-before binding mismatch"
        )

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    if receipt["receipt_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 8 continuation receipt digest mismatch"
        )


def execute_phase8_offline_continuation_step_once(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_readiness_path: str | Path,
    saved_post_audit_path: str | Path,
    execution_receipt_path: str | Path,
    continuation_request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    readiness_module, original_executor, runtime = _load_reviewed(
        source
    )
    saved_readiness = _load_json(
        saved_readiness_path,
        label="saved Phase 8 continuation execution readiness",
    )
    readiness_module.validate_phase8_offline_continuation_execution_readiness(
        saved_readiness
    )

    if saved_readiness.get(
        "continuation_execution_readiness_ready"
    ) is not True:
        raise ValueError(
            "saved Phase 8 continuation readiness is not ready"
        )
    if saved_readiness.get(
        "requires_immediate_one_shot_continuation_executor"
    ) is not True:
        raise ValueError(
            "saved Phase 8 continuation readiness lacks executor boundary"
        )
    if saved_readiness.get("readiness_only") is not True:
        raise ValueError(
            "saved Phase 8 continuation readiness is not readiness-only"
        )
    if saved_readiness.get("debt_type") not in ALLOWED_DEBT_TYPES:
        raise ValueError(
            "saved Phase 8 continuation readiness debt type is not allowed"
        )
    if (
        Path(str(saved_readiness["production_repository"])).resolve()
        != production
    ):
        raise ValueError(
            "Phase 8 continuation readiness repository mismatch"
        )

    database = original_executor._production_database(production)
    if Path(str(saved_readiness["pio_database_path"])).resolve() != database:
        raise ValueError(
            "Phase 8 continuation readiness database mismatch"
        )

    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    lock_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(LOCK_PATH, lock_flags, 0o600)
    except OSError as exc:
        raise ValueError(
            "Phase 8 continuation one-shot lock path is unsafe"
        ) from exc
    lock_stat = os.fstat(lock_fd)
    if not stat.S_ISREG(lock_stat.st_mode):
        os.close(lock_fd)
        raise ValueError(
            "Phase 8 continuation one-shot lock must be regular"
        )

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "another Phase 8 offline one-shot executor is active"
            ) from exc

        fresh_readiness = (
            readiness_module.build_phase8_offline_continuation_execution_readiness(
                repository=production,
                source_tree=source,
                saved_post_audit_path=saved_post_audit_path,
                execution_receipt_path=execution_receipt_path,
                continuation_request_path=continuation_request_path,
                saved_signed_authorization_verification_path=(
                    saved_signed_authorization_verification_path
                ),
                signed_payload_path=signed_payload_path,
                signature_path=signature_path,
                allowed_signers_path=allowed_signers_path,
                expected_allowed_signers_sha256=(
                    expected_allowed_signers_sha256
                ),
                now=now,
            )
        )
        readiness_module.validate_phase8_offline_continuation_execution_readiness(
            fresh_readiness
        )

        saved_stable = _stable_readiness_sha256(saved_readiness)
        fresh_stable = _stable_readiness_sha256(fresh_readiness)
        if fresh_stable != saved_stable:
            raise ValueError(
                "fresh Phase 8 continuation readiness stable state "
                "differs from saved readiness"
            )

        before_state = original_executor._database_state(database)
        expected_state = {
            "database": saved_readiness["pio_database_sha256"],
            "wal": saved_readiness["pio_wal_sha256"],
            "shm": saved_readiness["pio_shm_sha256"],
        }
        if before_state != expected_state:
            raise ValueError(
                "Pio database changed after continuation readiness"
            )

        data_root = database.parent
        before_artifacts = original_executor._research_artifacts(
            data_root
        )
        if (
            _sha256_bytes(_canonical_bytes(before_artifacts))
            != saved_readiness["research_artifacts_sha256"]
        ):
            raise ValueError(
                "Phase 8 research artifacts changed after "
                "continuation readiness"
            )
        if len(before_artifacts) != saved_readiness[
            "research_artifact_count"
        ]:
            raise ValueError(
                "Phase 8 research artifact count changed after readiness"
            )

        storage = runtime["Storage"](database)
        planner_before = original_executor._record(
            runtime["build_phase8_evidence_plan"](storage)
        )
        if (
            planner_before.get("research_only") is not True
            or planner_before.get("policy_actionable") is not False
            or planner_before.get("execution_wired") is not False
        ):
            raise ValueError(
                "Phase 8 continuation planner safety boundary changed"
            )

        action = _next_action(planner_before)
        if action is None:
            raise ValueError(
                "Phase 8 continuation planner has no offline action"
            )
        if action.get("operator_required") is not False:
            raise ValueError(
                "Phase 8 continuation planner now requires operator"
            )
        if action.get("shell_command") is None:
            raise ValueError(
                "Phase 8 continuation planner lost its offline command"
            )

        debt_type = str(saved_readiness["debt_type"])
        scope = str(saved_readiness["scope"])
        if (
            action.get("debt_type") != debt_type
            or action.get("scope") != scope
        ):
            raise ValueError(
                "Phase 8 continuation planner differs from authorized step"
            )
        if debt_type not in ALLOWED_DEBT_TYPES:
            raise ValueError(
                "Phase 8 continuation selected non-offline debt type"
            )

        operation_status, operation, error = (
            original_executor._execute_exact_offline_step(
                runtime,
                storage,
                debt_type=debt_type,
                scope=scope,
                data_root=data_root,
            )
        )
        planner_after = original_executor._record(
            runtime["build_phase8_evidence_plan"](storage)
        )
        after_state = original_executor._database_state(database)
        after_artifacts = original_executor._research_artifacts(
            data_root
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    artifact_changes = original_executor._artifact_changes(
        before_artifacts,
        after_artifacts,
    )
    db_modified = before_state != after_state
    artifacts_modified = bool(artifact_changes)
    completed = operation_status in {"COMPLETE", "NOT_QUALIFIED"}
    progressed = (
        operation_status == "COMPLETE"
        and original_executor._planner_progressed(
            planner_before,
            planner_after,
        )
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
        "saved_readiness_sha256": saved_readiness["readiness_sha256"],
        "fresh_readiness_sha256": fresh_readiness["readiness_sha256"],
        "saved_readiness_stable_sha256": saved_stable,
        "fresh_readiness_stable_sha256": fresh_stable,
        "source_post_audit_sha256": saved_readiness[
            "saved_post_audit_sha256"
        ],
        "source_execution_receipt_sha256": saved_readiness[
            "source_execution_receipt_sha256"
        ],
        "source_offline_step_request_sha256": saved_readiness[
            "source_offline_step_request_sha256"
        ],
        "continuation_request_sha256": saved_readiness[
            "continuation_request_sha256"
        ],
        "signed_authorization_verification_sha256": saved_readiness[
            "fresh_signed_authorization_verification_sha256"
        ],
        "approval_payload_sha256": saved_readiness[
            "approval_payload_sha256"
        ],
        "approval_signature_sha256": saved_readiness[
            "approval_signature_sha256"
        ],
        "allowed_signers_sha256": saved_readiness[
            "allowed_signers_sha256"
        ],
        "approver_principal": saved_readiness["approver_principal"],
        "approval_id": saved_readiness["approval_id"],
        "authorization_expires_at": saved_readiness[
            "authorization_expires_at"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before_state["database"],
        "pio_database_sha256_after": after_state["database"],
        "pio_wal_sha256_before": before_state["wal"],
        "pio_wal_sha256_after": after_state["wal"],
        "pio_shm_sha256_before": before_state["shm"],
        "pio_shm_sha256_after": after_state["shm"],
        "research_artifacts_before": before_artifacts,
        "research_artifacts_after": after_artifacts,
        "research_artifact_changes": artifact_changes,
        "research_artifact_change_count": len(artifact_changes),
        "production_research_artifacts_modified": artifacts_modified,
        "debt_type": debt_type,
        "scope": scope,
        "planner_before": planner_before,
        "planner_before_sha256": _sha256_bytes(
            _canonical_bytes(planner_before)
        ),
        "planner_after": planner_after,
        "planner_after_sha256": _sha256_bytes(
            _canonical_bytes(planner_after)
        ),
        "operation_status": operation_status,
        "operation": operation,
        "operation_sha256": _sha256_bytes(
            _canonical_bytes(operation)
        ),
        "error": error,
        "fresh_readiness_matches_saved": True,
        "human_continuation_offline_step_authorization_verified": True,
        "exact_planner_action_matched": True,
        "one_step_only": True,
        "research_only": True,
        "offline_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "offline_step_execution_authorized": True,
        "offline_step_attempted": True,
        "offline_step_executed": True,
        "offline_step_execution_completed": completed,
        "step_progressed": progressed,
        "requires_post_step_audit": True,
        "paper_challenger_transition_performed": False,
        "paper_trading_authorized": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_performed": False,
        "transaction_submission_performed": False,
        "automatic_resubmission_performed": False,
        "new_live_capital_used": False,
        "phase8_policy_action_authorized": False,
        "phase8_execution_authorized": False,
        "phase8_promotion_authorized": False,
        "phase8_promotion_persisted": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": db_modified,
    }
    receipt = {
        **identity,
        "receipt_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_offline_continuation_execution_receipt(
        receipt,
        original_executor=original_executor,
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one continued Phase 8 offline research step "
            "after a fresh continuation-readiness recheck. The executor "
            "shares the same Phase 8 one-shot lock, requires the planner's "
            "exact debt type and scope to match the authorization, and "
            "emits a receipt requiring a separate post-step audit. It never "
            "starts PAPER, signs/submits transactions, uses live capital, "
            "or persists Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--saved-post-audit", required=True)
    parser.add_argument("--execution-receipt", required=True)
    parser.add_argument("--continuation-request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument(
        "--expected-allowed-signers-sha256",
        required=True,
    )
    parser.add_argument("--now")
    args = parser.parse_args()

    receipt = execute_phase8_offline_continuation_step_once(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_readiness_path=args.saved_readiness,
        saved_post_audit_path=args.saved_post_audit,
        execution_receipt_path=args.execution_receipt,
        continuation_request_path=args.continuation_request,
        saved_signed_authorization_verification_path=(
            args.saved_signed_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=(
            args.expected_allowed_signers_sha256
        ),
        now=args.now,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
