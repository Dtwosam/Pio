from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_OFFLINE_CONTINUATION_POST_EXECUTION_AUDIT_V1"
)

EXECUTOR_TOOL = Path(
    "deploy/tools/run_phase8_offline_continuation_step_once.py"
)
EVIDENCE_PLAN = Path(
    "python-learner/src/meteora_learner/phase8_evidence_plan.py"
)
EVIDENCE_STATUS = Path(
    "python-learner/src/meteora_learner/phase8_evidence_status.py"
)
OPERATOR_HANDOFF = Path(
    "python-learner/src/meteora_learner/phase8_operator_handoff.py"
)
CONTINUOUS_LEARNING = Path(
    "python-learner/src/meteora_learner/continuous_learning.py"
)
VALIDATION = Path(
    "python-learner/src/meteora_learner/phase8_validation.py"
)
RETRAINING_CYCLE = Path(
    "python-learner/src/meteora_learner/retraining_cycle.py"
)
OFFLINE_RETRAINING = Path(
    "python-learner/src/meteora_learner/phase8_offline_retraining.py"
)
RETRAIN_INPUTS = Path(
    "python-learner/src/meteora_learner/phase8_retrain_inputs.py"
)
STORAGE = Path("python-learner/src/meteora_learner/storage.py")

REVIEWED_SOURCE_BLOBS = {
    EXECUTOR_TOOL: "70ee3495934c7722f763009aeead8fefdc6e516d",
    EVIDENCE_PLAN: "f8c162a61e516e61bcc4bf8554cc22a870a9f3d1",
    EVIDENCE_STATUS: "ea2bcf69c1c269d54ca439e56eb6eaf57d36a2b5",
    OPERATOR_HANDOFF: "17334b7ac35ed62aaead66285b4206669c7413ad",
    CONTINUOUS_LEARNING: "dc65c48dd5964a68b30f6edea85b03664f840e4e",
    VALIDATION: "1f712d83f440385aa16756f8e2755ef99dd1ef86",
    RETRAINING_CYCLE: "a02bda4a6b7a995275b186c9d71d415537197ee5",
    OFFLINE_RETRAINING: "ac025dc201b191460c178e631b700bd2ca34ea7b",
    RETRAIN_INPUTS: "9059b9aa190cd9598fabbb657c183215c22aa794",
    STORAGE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

ROUTE_NEXT_OFFLINE = "PHASE8_NEXT_OFFLINE_STEP_REAUTHORIZATION"
ROUTE_PAPER_REVIEW = "PHASE8_PAPER_CHALLENGER_REVIEW"
ROUTE_PROMOTION_REVIEW = "PHASE8_PROMOTION_REVIEW"
ROUTE_MANUAL_REVIEW = "PHASE8_MANUAL_OR_EVIDENCE_REVIEW"
ROUTE_FAILURE_REVIEW = "PHASE8_OFFLINE_STEP_FAILURE_REVIEW"
ROUTE_NOT_QUALIFIED_REVIEW = "PHASE8_OFFLINE_NOT_QUALIFIED_REVIEW"
ROUTE_NO_PROGRESS_REVIEW = "PHASE8_OFFLINE_NO_PROGRESS_REVIEW"
ROUTE_CURRENT = "PHASE8_CURRENT"

AUTOMATIC_OFFLINE_DEBT_TYPES = {
    "RETRAIN_DATASET_BUILD_READY",
    "RETRAIN_OFFLINE_TRAIN_READY",
    "RETRAIN_OFFLINE_VALIDATION_READY",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "execution_receipt_sha256",
    "source_post_audit_sha256",
    "source_execution_receipt_sha256",
    "source_offline_step_request_sha256",
    "continuation_request_sha256",
    "signed_authorization_verification_sha256",
    "production_repository",
    "pio_database_path",
    "receipt_database_sha256_after",
    "receipt_wal_sha256_after",
    "receipt_shm_sha256_after",
    "receipt_research_artifacts_after",
    "audit_research_artifacts_before",
    "audit_research_artifacts_after",
    "production_research_artifacts_match_receipt",
    "production_research_artifacts_unchanged_by_audit",
    "execution_research_artifact_change_count",
    "audit_database_sha256_before",
    "audit_database_sha256_after",
    "audit_wal_sha256_before",
    "audit_wal_sha256_after",
    "audit_shm_sha256_before",
    "audit_shm_sha256_after",
    "production_database_matches_receipt",
    "production_database_unchanged_by_audit",
    "executed_debt_type",
    "executed_scope",
    "execution_status",
    "execution_completed",
    "execution_progressed",
    "fresh_phase8_evidence_status",
    "fresh_phase8_evidence_status_sha256",
    "fresh_phase8_evidence_plan",
    "fresh_phase8_evidence_plan_sha256",
    "receipt_planner_after_stable_sha256",
    "fresh_planner_stable_sha256",
    "receipt_planner_after_matches_fresh",
    "fresh_phase8_operator_handoff",
    "fresh_phase8_operator_handoff_sha256",
    "next_debt_type",
    "next_scope",
    "continuation_route",
    "phase7_dependency_satisfied",
    "phase8_research_only",
    "phase8_read_only",
    "phase8_policy_actionable",
    "phase8_execution_wired",
    "phase8_promotion_ready",
    "phase8_persisted_current",
    "post_step_audit_ready",
    "requires_separate_phase8_action",
    "requires_new_human_authorization",
    "next_offline_step_reauthorization_required",
    "paper_challenger_review_required",
    "phase8_promotion_review_required",
    "manual_or_evidence_review_required",
    "failure_review_required",
    "phase8_current",
    "paper_challenger_transition_authorized",
    "paper_trading_authorized",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
    "phase8_policy_action_authorized",
    "phase8_execution_authorized",
    "phase8_promotion_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified_by_audit",
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
        raise ValueError(
            "Phase 8 continuation post-audit object did not produce a record"
        )
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


def _load_reviewed(
    source: Path,
) -> tuple[Any, Any, dict[str, Any]]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                "Phase 8 continuation post-audit dependency missing: "
                f"{relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                "Phase 8 continuation post-audit dependency mismatch: "
                f"{relative}"
            )

    executor = _load_module(
        source / EXECUTOR_TOOL,
        "phase8_continuation_post_audit_executor",
    )
    _, original_executor, _ = executor._load_reviewed(source)

    python_src = source / "python-learner" / "src"
    if str(python_src) not in sys.path:
        sys.path.insert(0, str(python_src))

    from meteora_learner.phase8_evidence_plan import (
        build_phase8_evidence_plan,
    )
    from meteora_learner.phase8_evidence_status import (
        evaluate_phase8_evidence_status,
    )
    from meteora_learner.phase8_operator_handoff import (
        build_phase8_operator_handoff,
    )
    from meteora_learner.storage import Storage

    return executor, original_executor, {
        "Storage": Storage,
        "build_phase8_evidence_plan": build_phase8_evidence_plan,
        "evaluate_phase8_evidence_status": evaluate_phase8_evidence_status,
        "build_phase8_operator_handoff": build_phase8_operator_handoff,
    }


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(
            f"unsafe Phase 8 continuation audit state file: {path}"
        )
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
        raise ValueError(
            "Phase 8 continuation post-audit data directory is unsafe"
        )
    database = data / "pio.db"
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError(
            "Phase 8 continuation post-audit database is missing"
        ) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(
            "Phase 8 continuation post-audit database is unsafe"
        )
    return database.resolve()


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    before = _database_state(source)
    if before["database"] is None:
        raise ValueError(
            "Pio database disappeared before continuation post-audit"
        )
    uri = f"file:{source.as_posix()}?mode=ro"
    if before["wal"] is None:
        uri += "&immutable=1"
    src = sqlite3.connect(uri, uri=True)
    try:
        src.execute("PRAGMA query_only=ON")
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if _database_state(source) != before:
        raise ValueError(
            "production Pio database changed during continuation snapshot"
        )


def _stable_plan_projection(value: dict[str, Any]) -> dict[str, Any]:
    projection = dict(value)
    projection.pop("as_of", None)
    return projection


def _stable_plan_sha256(value: dict[str, Any]) -> str:
    return _sha256_bytes(
        _canonical_bytes(_stable_plan_projection(value))
    )


def _next_action(plan: dict[str, Any]) -> dict[str, Any] | None:
    value = plan.get("next_action")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(
            "Phase 8 continuation post-audit next action is invalid"
        )
    return value


def _route(
    receipt: dict[str, Any],
    plan: dict[str, Any],
) -> str:
    if receipt.get("operation_status") == "FAILED":
        return ROUTE_FAILURE_REVIEW
    if receipt.get("operation_status") == "NOT_QUALIFIED":
        return ROUTE_NOT_QUALIFIED_REVIEW
    if receipt.get("operation_status") == "COMPLETE" and not bool(
        receipt.get("step_progressed")
    ):
        return ROUTE_NO_PROGRESS_REVIEW
    if bool(plan.get("persisted_phase8_current")):
        return ROUTE_CURRENT

    action = _next_action(plan)
    if action is None:
        return ROUTE_MANUAL_REVIEW
    debt_type = action.get("debt_type")
    if debt_type in AUTOMATIC_OFFLINE_DEBT_TYPES:
        return ROUTE_NEXT_OFFLINE
    if debt_type == "PAPER_CHALLENGER_START_REQUIRED":
        return ROUTE_PAPER_REVIEW
    if debt_type == "PHASE8_PROMOTION_PERSISTENCE_REQUIRED":
        return ROUTE_PROMOTION_REVIEW
    return ROUTE_MANUAL_REVIEW


def validate_phase8_offline_continuation_post_audit(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 8 continuation post-audit must be an object"
        )
    if set(report) != set(REPORT_FIELDS) | {"post_audit_sha256"}:
        raise ValueError(
            "Phase 8 continuation post-audit schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 8 continuation post-audit format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 8 continuation post-audit type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "Phase 8 continuation post-audit lineage mismatch"
        )

    for field in (
        "execution_receipt_sha256",
        "source_post_audit_sha256",
        "source_execution_receipt_sha256",
        "source_offline_step_request_sha256",
        "continuation_request_sha256",
        "signed_authorization_verification_sha256",
        "receipt_database_sha256_after",
        "audit_database_sha256_before",
        "audit_database_sha256_after",
        "fresh_phase8_evidence_status_sha256",
        "fresh_phase8_evidence_plan_sha256",
        "receipt_planner_after_stable_sha256",
        "fresh_planner_stable_sha256",
        "fresh_phase8_operator_handoff_sha256",
        "post_audit_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 8 continuation post-audit {field} is invalid"
            )

    for field in (
        "receipt_wal_sha256_after",
        "receipt_shm_sha256_after",
        "audit_wal_sha256_before",
        "audit_wal_sha256_after",
        "audit_shm_sha256_before",
        "audit_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 8 continuation post-audit {field} is invalid"
            )

    for artifact_field in (
        "receipt_research_artifacts_after",
        "audit_research_artifacts_before",
        "audit_research_artifacts_after",
    ):
        artifacts = report.get(artifact_field)
        if not isinstance(artifacts, list):
            raise ValueError(
                "Phase 8 continuation post-audit "
                f"{artifact_field} is invalid"
            )
        for item in artifacts:
            if (
                not isinstance(item, dict)
                or set(item) != {"path", "size_bytes", "sha256"}
                or not isinstance(item["path"], str)
                or not item["path"]
                or not isinstance(item["size_bytes"], int)
                or isinstance(item["size_bytes"], bool)
                or item["size_bytes"] < 0
                or not _is_hex_digest(item["sha256"], 64)
            ):
                raise ValueError(
                    "Phase 8 continuation post-audit "
                    f"{artifact_field} entry is invalid"
                )

    if (
        report["receipt_research_artifacts_after"]
        != report["audit_research_artifacts_before"]
    ):
        raise ValueError(
            "Phase 8 continuation research artifacts do not match receipt"
        )
    if (
        report["audit_research_artifacts_before"]
        != report["audit_research_artifacts_after"]
    ):
        raise ValueError(
            "Phase 8 continuation post-audit changed research artifacts"
        )

    change_count = report.get(
        "execution_research_artifact_change_count"
    )
    if (
        not isinstance(change_count, int)
        or isinstance(change_count, bool)
        or change_count < 0
    ):
        raise ValueError(
            "Phase 8 continuation execution artifact count is invalid"
        )

    for field in (
        "production_repository",
        "pio_database_path",
        "executed_debt_type",
        "executed_scope",
        "execution_status",
        "continuation_route",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 8 continuation post-audit {field} is invalid"
            )

    for field in (
        "fresh_phase8_evidence_status",
        "fresh_phase8_evidence_plan",
        "fresh_phase8_operator_handoff",
    ):
        if not isinstance(report.get(field), dict):
            raise ValueError(
                f"Phase 8 continuation post-audit {field} is invalid"
            )

    if report["fresh_phase8_evidence_status_sha256"] != _sha256_bytes(
        _canonical_bytes(report["fresh_phase8_evidence_status"])
    ):
        raise ValueError(
            "Phase 8 continuation post-audit status digest mismatch"
        )
    if report["fresh_phase8_evidence_plan_sha256"] != _sha256_bytes(
        _canonical_bytes(report["fresh_phase8_evidence_plan"])
    ):
        raise ValueError(
            "Phase 8 continuation post-audit plan digest mismatch"
        )
    if report["fresh_phase8_operator_handoff_sha256"] != _sha256_bytes(
        _canonical_bytes(report["fresh_phase8_operator_handoff"])
    ):
        raise ValueError(
            "Phase 8 continuation post-audit handoff digest mismatch"
        )

    if (
        report["receipt_planner_after_stable_sha256"]
        != report["fresh_planner_stable_sha256"]
    ):
        raise ValueError(
            "Phase 8 continuation post-audit stable planner mismatch"
        )

    for field in (
        "production_database_matches_receipt",
        "production_database_unchanged_by_audit",
        "production_research_artifacts_match_receipt",
        "production_research_artifacts_unchanged_by_audit",
        "receipt_planner_after_matches_fresh",
        "phase7_dependency_satisfied",
        "phase8_research_only",
        "phase8_read_only",
        "post_step_audit_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(
                "Phase 8 continuation post-audit requires "
                f"{field}=true"
            )

    for field in (
        "phase8_policy_actionable",
        "phase8_execution_wired",
        "paper_challenger_transition_authorized",
        "paper_trading_authorized",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase8_policy_action_authorized",
        "phase8_execution_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified_by_audit",
    ):
        if report.get(field) is not False:
            raise ValueError(
                "Phase 8 continuation post-audit requires "
                f"{field}=false"
            )

    allowed_routes = {
        ROUTE_NEXT_OFFLINE,
        ROUTE_PAPER_REVIEW,
        ROUTE_PROMOTION_REVIEW,
        ROUTE_MANUAL_REVIEW,
        ROUTE_FAILURE_REVIEW,
        ROUTE_NOT_QUALIFIED_REVIEW,
        ROUTE_NO_PROGRESS_REVIEW,
        ROUTE_CURRENT,
    }
    route = report["continuation_route"]
    if route not in allowed_routes:
        raise ValueError(
            "Phase 8 continuation post-audit route is invalid"
        )

    route_bindings = {
        "next_offline_step_reauthorization_required": (
            route == ROUTE_NEXT_OFFLINE
        ),
        "paper_challenger_review_required": (
            route == ROUTE_PAPER_REVIEW
        ),
        "phase8_promotion_review_required": (
            route == ROUTE_PROMOTION_REVIEW
        ),
        "manual_or_evidence_review_required": (
            route == ROUTE_MANUAL_REVIEW
        ),
        "failure_review_required": route in {
            ROUTE_FAILURE_REVIEW,
            ROUTE_NOT_QUALIFIED_REVIEW,
            ROUTE_NO_PROGRESS_REVIEW,
        },
        "phase8_current": route == ROUTE_CURRENT,
    }
    for field, expected in route_bindings.items():
        if report.get(field) is not expected:
            raise ValueError(
                "Phase 8 continuation post-audit "
                f"{field} route binding mismatch"
            )

    separate = route != ROUTE_CURRENT
    if report.get("requires_separate_phase8_action") is not separate:
        raise ValueError(
            "Phase 8 continuation separate-action binding mismatch"
        )
    needs_auth = route in {
        ROUTE_NEXT_OFFLINE,
        ROUTE_PAPER_REVIEW,
        ROUTE_PROMOTION_REVIEW,
    }
    if report.get("requires_new_human_authorization") is not needs_auth:
        raise ValueError(
            "Phase 8 continuation human-authorization binding mismatch"
        )

    plan = report["fresh_phase8_evidence_plan"]
    status = report["fresh_phase8_evidence_status"]
    handoff = report["fresh_phase8_operator_handoff"]
    if (
        plan.get("research_only") is not True
        or plan.get("policy_actionable") is not False
        or plan.get("execution_wired") is not False
    ):
        raise ValueError(
            "Phase 8 continuation plan safety boundary changed"
        )
    if status.get("phase7_promoted") is not True:
        raise ValueError(
            "Phase 8 continuation lost Phase 7 dependency"
        )
    if (
        handoff.get("research_only") is not True
        or handoff.get("read_only") is not True
        or handoff.get("policy_actionable") is not False
        or handoff.get("execution_wired") is not False
    ):
        raise ValueError(
            "Phase 8 continuation handoff safety boundary changed"
        )

    if report.get("phase8_promotion_ready") is not bool(
        plan.get("promotion_ready")
    ):
        raise ValueError(
            "Phase 8 continuation promotion-ready binding mismatch"
        )
    if report.get("phase8_persisted_current") is not bool(
        plan.get("persisted_phase8_current")
    ):
        raise ValueError(
            "Phase 8 continuation persisted-current binding mismatch"
        )

    action = _next_action(plan)
    expected_debt = (
        str(action.get("debt_type"))
        if action is not None and action.get("debt_type") is not None
        else None
    )
    expected_scope = (
        str(action.get("scope"))
        if action is not None and action.get("scope") is not None
        else None
    )
    if report.get("next_debt_type") != expected_debt:
        raise ValueError(
            "Phase 8 continuation next debt binding mismatch"
        )
    if report.get("next_scope") != expected_scope:
        raise ValueError(
            "Phase 8 continuation next scope binding mismatch"
        )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["post_audit_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 8 continuation post-audit digest mismatch"
        )


def build_phase8_offline_continuation_post_audit(
    *,
    repository: str | Path,
    source_tree: str | Path,
    execution_receipt_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    executor_module, original_executor, runtime = _load_reviewed(
        source
    )
    receipt = _load_json(
        execution_receipt_path,
        label="Phase 8 continuation execution receipt",
    )
    executor_module.validate_phase8_offline_continuation_execution_receipt(
        receipt,
        original_executor=original_executor,
    )

    if Path(str(receipt["production_repository"])).resolve() != production:
        raise ValueError(
            "Phase 8 continuation receipt repository mismatch"
        )

    database = _production_database(production)
    if Path(str(receipt["pio_database_path"])).resolve() != database:
        raise ValueError(
            "Phase 8 continuation receipt database mismatch"
        )

    before = _database_state(database)
    artifact_before = original_executor._research_artifacts(
        database.parent
    )
    if artifact_before != receipt["research_artifacts_after"]:
        raise ValueError(
            "production Phase 8 research artifacts changed after "
            "continuation receipt"
        )

    expected_after = {
        "database": receipt["pio_database_sha256_after"],
        "wal": receipt["pio_wal_sha256_after"],
        "shm": receipt["pio_shm_sha256_after"],
    }
    if before != expected_after:
        raise ValueError(
            "production Pio database changed after continuation receipt"
        )

    with tempfile.TemporaryDirectory(
        prefix="pio-phase8-continuation-post-audit-"
    ) as tmp:
        private_db = Path(tmp) / "pio.db"
        _snapshot_sqlite(database, private_db)
        storage = runtime["Storage"](private_db)
        status = _record(
            runtime["evaluate_phase8_evidence_status"](storage)
        )
        plan = _record(
            runtime["build_phase8_evidence_plan"](storage)
        )
        handoff = _record(
            runtime["build_phase8_operator_handoff"](storage)
        )

    after = _database_state(database)
    artifact_after = original_executor._research_artifacts(
        database.parent
    )
    if after != before:
        raise ValueError(
            "production Pio database changed during continuation audit"
        )
    if artifact_after != artifact_before:
        raise ValueError(
            "production Phase 8 research artifacts changed during "
            "continuation audit"
        )

    if (
        status.get("research_only") is not True
        or status.get("policy_actionable") is not False
        or status.get("execution_wired") is not False
        or status.get("phase7_promoted") is not True
    ):
        raise ValueError(
            "Phase 8 continuation status safety boundary changed"
        )
    if (
        plan.get("research_only") is not True
        or plan.get("policy_actionable") is not False
        or plan.get("execution_wired") is not False
    ):
        raise ValueError(
            "Phase 8 continuation plan safety boundary changed"
        )
    if (
        handoff.get("research_only") is not True
        or handoff.get("read_only") is not True
        or handoff.get("policy_actionable") is not False
        or handoff.get("execution_wired") is not False
    ):
        raise ValueError(
            "Phase 8 continuation operator handoff safety boundary changed"
        )

    receipt_plan_stable = _stable_plan_sha256(
        receipt["planner_after"]
    )
    fresh_plan_stable = _stable_plan_sha256(plan)
    if receipt_plan_stable != fresh_plan_stable:
        raise ValueError(
            "fresh Phase 8 planner differs from continuation receipt "
            "after removing only as_of"
        )

    route = _route(receipt, plan)
    action = _next_action(plan)
    next_debt = (
        str(action["debt_type"])
        if action is not None and action.get("debt_type") is not None
        else None
    )
    next_scope = (
        str(action["scope"])
        if action is not None and action.get("scope") is not None
        else None
    )

    status_sha = _sha256_bytes(_canonical_bytes(status))
    plan_sha = _sha256_bytes(_canonical_bytes(plan))
    handoff_sha = _sha256_bytes(_canonical_bytes(handoff))
    requires_separate = route != ROUTE_CURRENT
    requires_new_auth = route in {
        ROUTE_NEXT_OFFLINE,
        ROUTE_PAPER_REVIEW,
        ROUTE_PROMOTION_REVIEW,
    }

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
        "execution_receipt_sha256": receipt["receipt_sha256"],
        "source_post_audit_sha256": receipt[
            "source_post_audit_sha256"
        ],
        "source_execution_receipt_sha256": receipt[
            "source_execution_receipt_sha256"
        ],
        "source_offline_step_request_sha256": receipt[
            "source_offline_step_request_sha256"
        ],
        "continuation_request_sha256": receipt[
            "continuation_request_sha256"
        ],
        "signed_authorization_verification_sha256": receipt[
            "signed_authorization_verification_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "receipt_database_sha256_after": receipt[
            "pio_database_sha256_after"
        ],
        "receipt_wal_sha256_after": receipt["pio_wal_sha256_after"],
        "receipt_shm_sha256_after": receipt["pio_shm_sha256_after"],
        "receipt_research_artifacts_after": receipt[
            "research_artifacts_after"
        ],
        "audit_research_artifacts_before": artifact_before,
        "audit_research_artifacts_after": artifact_after,
        "production_research_artifacts_match_receipt": True,
        "production_research_artifacts_unchanged_by_audit": True,
        "execution_research_artifact_change_count": receipt[
            "research_artifact_change_count"
        ],
        "audit_database_sha256_before": before["database"],
        "audit_database_sha256_after": after["database"],
        "audit_wal_sha256_before": before["wal"],
        "audit_wal_sha256_after": after["wal"],
        "audit_shm_sha256_before": before["shm"],
        "audit_shm_sha256_after": after["shm"],
        "production_database_matches_receipt": True,
        "production_database_unchanged_by_audit": True,
        "executed_debt_type": receipt["debt_type"],
        "executed_scope": receipt["scope"],
        "execution_status": receipt["operation_status"],
        "execution_completed": receipt[
            "offline_step_execution_completed"
        ],
        "execution_progressed": receipt["step_progressed"],
        "fresh_phase8_evidence_status": status,
        "fresh_phase8_evidence_status_sha256": status_sha,
        "fresh_phase8_evidence_plan": plan,
        "fresh_phase8_evidence_plan_sha256": plan_sha,
        "receipt_planner_after_stable_sha256": receipt_plan_stable,
        "fresh_planner_stable_sha256": fresh_plan_stable,
        "receipt_planner_after_matches_fresh": True,
        "fresh_phase8_operator_handoff": handoff,
        "fresh_phase8_operator_handoff_sha256": handoff_sha,
        "next_debt_type": next_debt,
        "next_scope": next_scope,
        "continuation_route": route,
        "phase7_dependency_satisfied": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "phase8_promotion_ready": bool(plan.get("promotion_ready")),
        "phase8_persisted_current": bool(
            plan.get("persisted_phase8_current")
        ),
        "post_step_audit_ready": True,
        "requires_separate_phase8_action": requires_separate,
        "requires_new_human_authorization": requires_new_auth,
        "next_offline_step_reauthorization_required": (
            route == ROUTE_NEXT_OFFLINE
        ),
        "paper_challenger_review_required": (
            route == ROUTE_PAPER_REVIEW
        ),
        "phase8_promotion_review_required": (
            route == ROUTE_PROMOTION_REVIEW
        ),
        "manual_or_evidence_review_required": (
            route == ROUTE_MANUAL_REVIEW
        ),
        "failure_review_required": route in {
            ROUTE_FAILURE_REVIEW,
            ROUTE_NOT_QUALIFIED_REVIEW,
            ROUTE_NO_PROGRESS_REVIEW,
        },
        "phase8_current": route == ROUTE_CURRENT,
        "paper_challenger_transition_authorized": False,
        "paper_trading_authorized": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_policy_action_authorized": False,
        "phase8_execution_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified_by_audit": False,
    }
    report = {
        **identity,
        "post_audit_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_offline_continuation_post_audit(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of one continued Phase 8 offline-step receipt. "
            "The audit requires the current DB and research-artifact state to "
            "match the receipt, rebuilds Phase 8 status/plan/operator handoff "
            "on a private DB snapshot, and emits the next explicit route. It "
            "never retries, starts PAPER, uses live capital, submits a "
            "transaction, or persists Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--execution-receipt", required=True)
    args = parser.parse_args()

    report = build_phase8_offline_continuation_post_audit(
        repository=args.repo,
        source_tree=args.source_tree,
        execution_receipt_path=args.execution_receipt,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
