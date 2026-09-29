from __future__ import annotations

import argparse
from dataclasses import asdict
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
ARTIFACT_TYPE = "PHASE8_OFFLINE_STEP_ONE_SHOT_EXECUTION_RECEIPT_V1"

READINESS_TOOL = Path(
    "deploy/tools/check_phase8_offline_step_execution_readiness.py"
)
EVIDENCE_PLAN = Path(
    "python-learner/src/meteora_learner/phase8_evidence_plan.py"
)
OFFLINE_RETRAINING = Path(
    "python-learner/src/meteora_learner/phase8_offline_retraining.py"
)
RETRAIN_INPUTS = Path(
    "python-learner/src/meteora_learner/phase8_retrain_inputs.py"
)
CONTINUOUS_LEARNING = Path(
    "python-learner/src/meteora_learner/continuous_learning.py"
)
EVIDENCE_STATUS = Path(
    "python-learner/src/meteora_learner/phase8_evidence_status.py"
)
VALIDATION = Path(
    "python-learner/src/meteora_learner/phase8_validation.py"
)
RETRAINING_CYCLE = Path(
    "python-learner/src/meteora_learner/retraining_cycle.py"
)
STORAGE = Path("python-learner/src/meteora_learner/storage.py")

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "b613f3e177e16680cba975fa2330e36e4de5b700",
    EVIDENCE_PLAN: "f8c162a61e516e61bcc4bf8554cc22a870a9f3d1",
    OFFLINE_RETRAINING: "ac025dc201b191460c178e631b700bd2ca34ea7b",
    RETRAIN_INPUTS: "9059b9aa190cd9598fabbb657c183215c22aa794",
    CONTINUOUS_LEARNING: "dc65c48dd5964a68b30f6edea85b03664f840e4e",
    EVIDENCE_STATUS: "ea2bcf69c1c269d54ca439e56eb6eaf57d36a2b5",
    VALIDATION: "1f712d83f440385aa16756f8e2755ef99dd1ef86",
    RETRAINING_CYCLE: "a02bda4a6b7a995275b186c9d71d415537197ee5",
    STORAGE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

ALLOWED_DEBT_TYPES = {
    "RETRAIN_DATASET_BUILD_READY",
    "RETRAIN_OFFLINE_TRAIN_READY",
    "RETRAIN_OFFLINE_VALIDATION_READY",
}
ALLOWED_ARTIFACT_ROOTS = (
    "phase8_retraining_datasets",
    "phase8_ml_artifacts",
)
LOCK_PATH = Path("/var/tmp/pio-phase8-offline-step-one-shot.lock")

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_readiness_sha256",
    "fresh_readiness_sha256",
    "saved_readiness_stable_sha256",
    "fresh_readiness_stable_sha256",
    "offline_step_request_sha256",
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
    "human_offline_step_authorization_verified",
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


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


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


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ValueError("Phase 8 offline-step object did not produce a record")
    return result


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


def _load_reviewed(source: Path) -> tuple[Any, dict[str, Any]]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 8 one-shot dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 8 one-shot dependency mismatch: {relative}"
            )

    readiness = _load_module(
        source / READINESS_TOOL,
        "phase8_offline_step_one_shot_readiness",
    )

    python_src = source / "python-learner" / "src"
    if str(python_src) not in sys.path:
        sys.path.insert(0, str(python_src))

    from meteora_learner.phase8_evidence_plan import (
        build_phase8_evidence_plan,
    )
    from meteora_learner.phase8_offline_retraining import (
        train_phase8_cycle_challenger,
        validate_phase8_cycle_challenger_offline,
    )
    from meteora_learner.phase8_retrain_inputs import (
        load_phase8_retrain_inputs,
        run_phase8_retrain_build_from_inputs,
    )
    from meteora_learner.storage import Storage

    return readiness, {
        "Storage": Storage,
        "build_phase8_evidence_plan": build_phase8_evidence_plan,
        "load_phase8_retrain_inputs": load_phase8_retrain_inputs,
        "run_phase8_retrain_build_from_inputs": (
            run_phase8_retrain_build_from_inputs
        ),
        "train_phase8_cycle_challenger": train_phase8_cycle_challenger,
        "validate_phase8_cycle_challenger_offline": (
            validate_phase8_cycle_challenger_offline
        ),
    }


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"unsafe Phase 8 Pio database state file: {path}")
    return _sha256_path(path)


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _production_database(production: Path) -> Path:
    data = production / "data"
    if data.is_symlink() or not data.is_dir():
        raise ValueError("Phase 8 one-shot data directory is unsafe")
    database = data / "pio.db"
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError("Phase 8 one-shot Pio database is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("Phase 8 one-shot Pio database is unsafe")
    return database.resolve()


def _research_artifacts(data_root: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for root_name in ALLOWED_ARTIFACT_ROOTS:
        root = data_root / root_name
        try:
            root_stat = os.lstat(root)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(
            root_stat.st_mode
        ):
            raise ValueError(
                f"unsafe Phase 8 research artifact root: {root}"
            )
        for path in sorted(root.rglob("*")):
            item_stat = os.lstat(path)
            if stat.S_ISLNK(item_stat.st_mode):
                raise ValueError(
                    f"unsafe Phase 8 research artifact symlink: {path}"
                )
            if stat.S_ISDIR(item_stat.st_mode):
                continue
            if not stat.S_ISREG(item_stat.st_mode):
                raise ValueError(
                    f"unsafe Phase 8 research artifact file type: {path}"
                )
            payload = path.read_bytes()
            artifacts.append(
                {
                    "path": path.relative_to(data_root).as_posix(),
                    "size_bytes": len(payload),
                    "sha256": _sha256_bytes(payload),
                }
            )
    return artifacts


def _artifact_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    before_by_path = {item["path"]: item for item in before}
    after_by_path = {item["path"]: item for item in after}
    changes: list[dict[str, Any]] = []
    for path in sorted(set(before_by_path) | set(after_by_path)):
        old = before_by_path.get(path)
        new = after_by_path.get(path)
        if old == new:
            continue
        status_name = (
            "ADDED"
            if old is None
            else "REMOVED"
            if new is None
            else "MODIFIED"
        )
        changes.append(
            {
                "path": path,
                "status": status_name,
                "before_size_bytes": (
                    old["size_bytes"] if old is not None else None
                ),
                "after_size_bytes": (
                    new["size_bytes"] if new is not None else None
                ),
                "before_sha256": (
                    old["sha256"] if old is not None else None
                ),
                "after_sha256": (
                    new["sha256"] if new is not None else None
                ),
            }
        )
    return changes


def _stable_readiness_projection(
    value: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key not in {
            "readiness_sha256",
            "fresh_phase8_handoff_sha256",
        }
    }


def _stable_readiness_sha256(value: dict[str, Any]) -> str:
    return _sha256_bytes(
        _canonical_bytes(_stable_readiness_projection(value))
    )


def _next_action_record(plan: dict[str, Any]) -> dict[str, Any] | None:
    action = plan.get("next_action")
    if action is None:
        return None
    if not isinstance(action, dict):
        raise ValueError("Phase 8 planner next_action is invalid")
    return action


def _planner_progressed(
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    if (
        not bool(before.get("persisted_phase8_current"))
        and bool(after.get("persisted_phase8_current"))
    ):
        return True
    old = _next_action_record(before)
    new = _next_action_record(after)
    if old is None:
        return False
    if new is None:
        return True
    return (
        old.get("debt_type"),
        old.get("scope"),
    ) != (
        new.get("debt_type"),
        new.get("scope"),
    )


def _execute_exact_offline_step(
    runtime: dict[str, Any],
    storage: Any,
    *,
    debt_type: str,
    scope: str,
    data_root: Path,
) -> tuple[str, dict[str, Any] | None, str | None]:
    try:
        if debt_type == "RETRAIN_DATASET_BUILD_READY":
            try:
                evidence_id = int(scope)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Phase 8 retrain dataset evidence scope must be an integer"
                ) from exc
            artifact = runtime["load_phase8_retrain_inputs"](
                storage,
                evidence_id=evidence_id,
            )
            if artifact is None:
                raise ValueError(
                    "Phase 8 retraining input artifact disappeared"
                )
            result = runtime["run_phase8_retrain_build_from_inputs"](
                storage,
                artifact=artifact,
                output_directory=(
                    data_root / "phase8_retraining_datasets"
                ),
            )
            return "COMPLETE", _record(result), None

        if debt_type == "RETRAIN_OFFLINE_TRAIN_READY":
            result = runtime["train_phase8_cycle_challenger"](
                storage,
                cycle_id=scope,
                artifact_directory=(
                    data_root / "phase8_ml_artifacts" / scope
                ),
            )
            return "COMPLETE", _record(result), None

        if debt_type == "RETRAIN_OFFLINE_VALIDATION_READY":
            result = runtime[
                "validate_phase8_cycle_challenger_offline"
            ](
                storage,
                cycle_id=scope,
            )
            status = (
                "COMPLETE"
                if bool(getattr(result, "offline_qualified", False))
                else "NOT_QUALIFIED"
            )
            return status, _record(result), None

        raise ValueError(
            f"Phase 8 one-shot debt type is not allowed: {debt_type}"
        )
    except Exception as exc:
        return (
            "FAILED",
            None,
            f"{type(exc).__name__}: {str(exc)[:2000]}",
        )


def validate_phase8_offline_step_execution_receipt(
    receipt: dict[str, Any],
) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("Phase 8 one-shot receipt must be an object")
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError("Phase 8 one-shot receipt schema mismatch")
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 8 one-shot receipt format")
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 8 one-shot receipt type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 8 one-shot receipt lineage mismatch")

    for field in (
        "saved_readiness_sha256",
        "fresh_readiness_sha256",
        "saved_readiness_stable_sha256",
        "fresh_readiness_stable_sha256",
        "offline_step_request_sha256",
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
            raise ValueError(f"Phase 8 one-shot receipt {field} is invalid")

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = receipt.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 8 one-shot receipt {field} is invalid")

    for artifact_field in (
        "research_artifacts_before",
        "research_artifacts_after",
    ):
        artifacts = receipt.get(artifact_field)
        if not isinstance(artifacts, list):
            raise ValueError(
                f"Phase 8 one-shot receipt {artifact_field} is invalid"
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
                    for root in ALLOWED_ARTIFACT_ROOTS
                )
                or not isinstance(item["size_bytes"], int)
                or isinstance(item["size_bytes"], bool)
                or item["size_bytes"] < 0
                or not _is_hex_digest(item["sha256"], 64)
            ):
                raise ValueError(
                    f"Phase 8 one-shot receipt {artifact_field} entry is invalid"
                )

    expected_artifact_changes = _artifact_changes(
        receipt["research_artifacts_before"],
        receipt["research_artifacts_after"],
    )
    if receipt.get("research_artifact_changes") != expected_artifact_changes:
        raise ValueError(
            "Phase 8 one-shot research artifact changes mismatch"
        )
    if receipt.get("research_artifact_change_count") != len(
        expected_artifact_changes
    ):
        raise ValueError(
            "Phase 8 one-shot research artifact change count mismatch"
        )
    artifacts_modified = bool(expected_artifact_changes)
    if receipt.get(
        "production_research_artifacts_modified"
    ) is not artifacts_modified:
        raise ValueError(
            "Phase 8 one-shot research artifact modified binding mismatch"
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
            raise ValueError(f"Phase 8 one-shot receipt {field} is invalid")

    if receipt["debt_type"] not in ALLOWED_DEBT_TYPES:
        raise ValueError("Phase 8 one-shot receipt debt type is not allowed")
    if receipt["operation_status"] not in {
        "COMPLETE",
        "NOT_QUALIFIED",
        "FAILED",
    }:
        raise ValueError("Phase 8 one-shot receipt operation status is invalid")

    for field in ("planner_before", "planner_after"):
        value = receipt.get(field)
        if not isinstance(value, dict):
            raise ValueError(f"Phase 8 one-shot receipt {field} is invalid")
        if value.get("research_only") is not True:
            raise ValueError(f"Phase 8 one-shot {field} must be research-only")
        if value.get("policy_actionable") is not False:
            raise ValueError(
                f"Phase 8 one-shot {field} must not be policy-actionable"
            )
        if value.get("execution_wired") is not False:
            raise ValueError(
                f"Phase 8 one-shot {field} must remain execution-unwired"
            )

    if receipt["planner_before_sha256"] != _sha256_bytes(
        _canonical_bytes(receipt["planner_before"])
    ):
        raise ValueError("Phase 8 one-shot planner-before digest mismatch")
    if receipt["planner_after_sha256"] != _sha256_bytes(
        _canonical_bytes(receipt["planner_after"])
    ):
        raise ValueError("Phase 8 one-shot planner-after digest mismatch")

    operation = receipt.get("operation")
    if operation is not None and not isinstance(operation, dict):
        raise ValueError("Phase 8 one-shot receipt operation is invalid")
    expected_operation_sha = _sha256_bytes(
        _canonical_bytes(operation)
    )
    if receipt["operation_sha256"] != expected_operation_sha:
        raise ValueError("Phase 8 one-shot operation digest mismatch")

    if (
        receipt["saved_readiness_stable_sha256"]
        != receipt["fresh_readiness_stable_sha256"]
    ):
        raise ValueError("Phase 8 one-shot stable readiness mismatch")

    for field in (
        "fresh_readiness_matches_saved",
        "human_offline_step_authorization_verified",
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
                f"Phase 8 one-shot receipt requires {field}=true"
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
                f"Phase 8 one-shot receipt requires {field}=false"
            )

    completed = receipt["operation_status"] in {
        "COMPLETE",
        "NOT_QUALIFIED",
    }
    if receipt.get("offline_step_execution_completed") is not completed:
        raise ValueError(
            "Phase 8 one-shot completion/status binding mismatch"
        )
    if receipt["operation_status"] == "FAILED":
        if not isinstance(receipt.get("error"), str) or not receipt["error"]:
            raise ValueError("Phase 8 one-shot failed receipt needs error")
        if receipt.get("operation") is not None:
            raise ValueError(
                "Phase 8 one-shot failed receipt must not claim operation"
            )
    else:
        if receipt.get("error") is not None:
            raise ValueError(
                "Phase 8 one-shot successful receipt must not have error"
            )
        if not isinstance(receipt.get("operation"), dict):
            raise ValueError(
                "Phase 8 one-shot completed receipt needs operation"
            )

    modified = (
        receipt["pio_database_sha256_before"]
        != receipt["pio_database_sha256_after"]
        or receipt["pio_wal_sha256_before"]
        != receipt["pio_wal_sha256_after"]
        or receipt["pio_shm_sha256_before"]
        != receipt["pio_shm_sha256_after"]
    )
    if receipt.get("production_pio_database_modified") is not modified:
        raise ValueError(
            "Phase 8 one-shot database-modified binding mismatch"
        )

    expected_progressed = (
        receipt["operation_status"] == "COMPLETE"
        and _planner_progressed(
            receipt["planner_before"],
            receipt["planner_after"],
        )
    )
    if receipt.get("step_progressed") is not expected_progressed:
        raise ValueError(
            "Phase 8 one-shot planner-progress binding mismatch"
        )

    before_action = _next_action_record(receipt["planner_before"])
    if not isinstance(before_action, dict):
        raise ValueError(
            "Phase 8 one-shot planner-before action is missing"
        )
    if (
        before_action.get("debt_type") != receipt["debt_type"]
        or before_action.get("scope") != receipt["scope"]
    ):
        raise ValueError(
            "Phase 8 one-shot planner-before action binding mismatch"
        )

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    if receipt["receipt_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("Phase 8 one-shot receipt digest mismatch")


def execute_phase8_offline_step_once(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_readiness_path: str | Path,
    saved_phase8_handoff_path: str | Path,
    phase7_post_promotion_audit_path: str | Path,
    offline_step_request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source_candidate = Path(source_tree).expanduser()
    production_candidate = Path(repository).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    if production_candidate.is_symlink():
        raise ValueError("production repository root must not be a symlink")
    source = source_candidate.resolve()
    production = production_candidate.resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    readiness_module, runtime = _load_reviewed(source)
    saved_readiness = _load_json(
        saved_readiness_path,
        label="saved Phase 8 offline-step execution readiness",
    )
    readiness_module.validate_phase8_offline_step_execution_readiness(
        saved_readiness
    )

    if saved_readiness.get(
        "offline_step_execution_readiness_ready"
    ) is not True:
        raise ValueError("saved Phase 8 offline-step readiness is not ready")
    if saved_readiness.get(
        "requires_immediate_one_shot_offline_executor"
    ) is not True:
        raise ValueError(
            "saved Phase 8 readiness lacks one-shot executor boundary"
        )
    if saved_readiness.get("readiness_only") is not True:
        raise ValueError("saved Phase 8 readiness is not readiness-only")
    if saved_readiness.get("debt_type") not in ALLOWED_DEBT_TYPES:
        raise ValueError("saved Phase 8 readiness debt type is not allowed")
    if Path(str(saved_readiness["production_repository"])).resolve() != production:
        raise ValueError("Phase 8 readiness repository binding mismatch")

    database = _production_database(production)
    if Path(str(saved_readiness["pio_database_path"])).resolve() != database:
        raise ValueError("Phase 8 readiness database binding mismatch")

    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    lock_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(LOCK_PATH, lock_flags, 0o600)
    except OSError as exc:
        raise ValueError("Phase 8 one-shot lock path is unsafe") from exc
    lock_stat = os.fstat(lock_fd)
    if not stat.S_ISREG(lock_stat.st_mode):
        os.close(lock_fd)
        raise ValueError("Phase 8 one-shot lock must be a regular file")

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "another Phase 8 offline one-shot executor is active"
            ) from exc

        fresh_readiness = (
            readiness_module.build_phase8_offline_step_execution_readiness(
                repository=production,
                source_tree=source,
                saved_phase8_handoff_path=saved_phase8_handoff_path,
                phase7_post_promotion_audit_path=(
                    phase7_post_promotion_audit_path
                ),
                offline_step_request_path=offline_step_request_path,
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
        readiness_module.validate_phase8_offline_step_execution_readiness(
            fresh_readiness
        )

        saved_stable = _stable_readiness_sha256(saved_readiness)
        fresh_stable = _stable_readiness_sha256(fresh_readiness)
        if fresh_stable != saved_stable:
            raise ValueError(
                "fresh Phase 8 offline-step readiness stable state "
                "differs from saved readiness"
            )

        before_state = _database_state(database)
        if before_state["database"] != saved_readiness["pio_database_sha256"]:
            raise ValueError(
                "Pio database changed after Phase 8 execution readiness"
            )

        data_root = database.parent
        before_artifacts = _research_artifacts(data_root)

        storage = runtime["Storage"](database)
        planner_before = _record(
            runtime["build_phase8_evidence_plan"](storage)
        )
        if (
            planner_before.get("research_only") is not True
            or planner_before.get("policy_actionable") is not False
            or planner_before.get("execution_wired") is not False
        ):
            raise ValueError(
                "Phase 8 planner safety boundary changed before execution"
            )
        action = _next_action_record(planner_before)
        if action is None:
            raise ValueError(
                "Phase 8 planner no longer has an offline action"
            )
        if action.get("operator_required") is not False:
            raise ValueError(
                "Phase 8 planner action now requires an operator"
            )
        if action.get("shell_command") is None:
            raise ValueError(
                "Phase 8 planner action no longer has an offline command"
            )
        debt_type = str(saved_readiness["debt_type"])
        scope = str(saved_readiness["scope"])
        if (
            action.get("debt_type") != debt_type
            or action.get("scope") != scope
        ):
            raise ValueError(
                "Phase 8 planner action differs from authorized exact step"
            )
        if debt_type not in ALLOWED_DEBT_TYPES:
            raise ValueError(
                "Phase 8 planner selected a non-offline debt type"
            )

        operation_status, operation, error = _execute_exact_offline_step(
            runtime,
            storage,
            debt_type=debt_type,
            scope=scope,
            data_root=data_root,
        )
        planner_after = _record(
            runtime["build_phase8_evidence_plan"](storage)
        )
        after_state = _database_state(database)
        after_artifacts = _research_artifacts(data_root)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    operation_sha256 = _sha256_bytes(_canonical_bytes(operation))
    planner_before_sha256 = _sha256_bytes(
        _canonical_bytes(planner_before)
    )
    planner_after_sha256 = _sha256_bytes(
        _canonical_bytes(planner_after)
    )
    database_modified = before_state != after_state
    artifact_changes = _artifact_changes(
        before_artifacts,
        after_artifacts,
    )
    artifacts_modified = bool(artifact_changes)
    completed = operation_status in {"COMPLETE", "NOT_QUALIFIED"}
    progressed = (
        operation_status == "COMPLETE"
        and _planner_progressed(planner_before, planner_after)
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
        "offline_step_request_sha256": fresh_readiness[
            "offline_step_request_sha256"
        ],
        "signed_authorization_verification_sha256": fresh_readiness[
            "fresh_signed_authorization_verification_sha256"
        ],
        "approval_payload_sha256": fresh_readiness[
            "approval_payload_sha256"
        ],
        "approval_signature_sha256": fresh_readiness[
            "approval_signature_sha256"
        ],
        "allowed_signers_sha256": fresh_readiness[
            "allowed_signers_sha256"
        ],
        "approver_principal": fresh_readiness["approver_principal"],
        "approval_id": fresh_readiness["approval_id"],
        "authorization_expires_at": fresh_readiness[
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
        "planner_before_sha256": planner_before_sha256,
        "planner_after": planner_after,
        "planner_after_sha256": planner_after_sha256,
        "operation_status": operation_status,
        "operation": operation,
        "operation_sha256": operation_sha256,
        "error": error,
        "fresh_readiness_matches_saved": True,
        "human_offline_step_authorization_verified": True,
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
        "production_pio_database_modified": database_modified,
    }
    receipt = {
        **identity,
        "receipt_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_offline_step_execution_receipt(receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one previously authorized Phase 8 offline "
            "research step after a fresh readiness recheck. The executor "
            "holds a one-shot lock, requires the planner's exact debt type "
            "and scope to match the authorization, never starts PAPER, never "
            "signs/submits transactions, never uses live capital, and emits "
            "a receipt requiring a separate post-step audit."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--saved-phase8-handoff", required=True)
    parser.add_argument("--phase7-post-promotion-audit", required=True)
    parser.add_argument("--offline-step-request", required=True)
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

    receipt = execute_phase8_offline_step_once(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_readiness_path=args.saved_readiness,
        saved_phase8_handoff_path=args.saved_phase8_handoff,
        phase7_post_promotion_audit_path=(
            args.phase7_post_promotion_audit
        ),
        offline_step_request_path=args.offline_step_request,
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
