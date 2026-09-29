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
ARTIFACT_TYPE = "PHASE8_OFFLINE_STEP_CONTINUATION_REQUEST_V1"

POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_offline_step_post_audit.py"
)
REVIEWED_SOURCE_BLOBS = {
    POST_AUDIT_TOOL: "dff8ad3e889912ed3d142e7222fec6436bd20b2e",
}

AUTHORIZATION_SCOPE = "EXECUTE_ONE_PHASE8_OFFLINE_RESEARCH_STEP_ONLY"
ALLOWED_DEBT_TYPES = {
    "RETRAIN_DATASET_BUILD_READY",
    "RETRAIN_OFFLINE_TRAIN_READY",
    "RETRAIN_OFFLINE_VALIDATION_READY",
}

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_scope",
    "source_post_audit_sha256",
    "source_execution_receipt_sha256",
    "source_offline_step_request_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "research_artifacts_sha256",
    "research_artifact_count",
    "debt_type",
    "scope",
    "reason",
    "suggested_command",
    "continuation_route",
    "automatic_action_available",
    "allowed_offline_debt_type",
    "one_step_only",
    "request_ready",
    "explicit_human_authorization_required",
    "fresh_post_audit_recheck_required",
    "offline_step_authorization_present",
    "offline_step_execution_authorized",
    "offline_step_executed",
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
    "production_pio_database_modified",
    "production_research_artifacts_modified",
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


def _load_audit_module(source: Path) -> Any:
    path = source / POST_AUDIT_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed Phase 8 post-step audit tool is missing")
    if _git_blob_sha(path) != REVIEWED_SOURCE_BLOBS[POST_AUDIT_TOOL]:
        raise ValueError("reviewed Phase 8 post-step audit blob mismatch")
    return _load_module(
        path,
        "phase8_offline_continuation_post_audit",
    )


def _production_database(production: Path) -> Path:
    data = production / "data"
    if data.is_symlink() or not data.is_dir():
        raise ValueError(
            "Phase 8 continuation request data directory is unsafe"
        )
    database = data / "pio.db"
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError(
            "Phase 8 continuation request Pio database is missing"
        ) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(
            "Phase 8 continuation request Pio database is unsafe"
        )
    return database.resolve()


def validate_phase8_offline_continuation_request(
    request: dict[str, Any],
) -> None:
    if not isinstance(request, dict):
        raise ValueError(
            "Phase 8 offline continuation request must be an object"
        )
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError(
            "Phase 8 offline continuation request schema mismatch"
        )
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 8 offline continuation request format"
        )
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 8 offline continuation request type"
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
            "Phase 8 offline continuation request lineage mismatch"
        )

    for field in (
        "source_post_audit_sha256",
        "source_execution_receipt_sha256",
        "source_offline_step_request_sha256",
        "pio_database_sha256",
        "research_artifacts_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(
                f"Phase 8 offline continuation request {field} is invalid"
            )

    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = request.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 8 offline continuation request {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "debt_type",
        "scope",
        "reason",
        "suggested_command",
        "continuation_route",
    ):
        if not isinstance(request.get(field), str) or not request[field]:
            raise ValueError(
                f"Phase 8 offline continuation request {field} is invalid"
            )

    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError(
            "Phase 8 offline continuation authorization scope mismatch"
        )
    if request.get("debt_type") not in ALLOWED_DEBT_TYPES:
        raise ValueError(
            "Phase 8 offline continuation debt type is not allowed"
        )

    count = request.get("research_artifact_count")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
    ):
        raise ValueError(
            "Phase 8 offline continuation artifact count is invalid"
        )

    for field in (
        "automatic_action_available",
        "allowed_offline_debt_type",
        "one_step_only",
        "request_ready",
        "explicit_human_authorization_required",
        "fresh_post_audit_recheck_required",
    ):
        if request.get(field) is not True:
            raise ValueError(
                "Phase 8 offline continuation request requires "
                f"{field}=true"
            )

    for field in (
        "offline_step_authorization_present",
        "offline_step_execution_authorized",
        "offline_step_executed",
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
        "production_pio_database_modified",
        "production_research_artifacts_modified",
    ):
        if request.get(field) is not False:
            raise ValueError(
                "Phase 8 offline continuation request requires "
                f"{field}=false"
            )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    if request["request_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 8 offline continuation request digest mismatch"
        )


def build_phase8_offline_continuation_request(
    *,
    repository: str | Path,
    source_tree: str | Path,
    post_audit_path: str | Path,
    expected_post_audit_sha256: str,
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

    audit_module = _load_audit_module(source)
    audit = _load_json(
        post_audit_path,
        label="Phase 8 offline-step post-execution audit",
    )
    audit_module.validate_phase8_offline_step_post_audit(audit)

    if (
        not _is_hex_digest(expected_post_audit_sha256, 64)
        or audit["post_audit_sha256"] != expected_post_audit_sha256
    ):
        raise ValueError(
            "Phase 8 continuation request post-audit digest mismatch"
        )

    route_next = getattr(
        audit_module,
        "ROUTE_NEXT_OFFLINE",
        "PHASE8_NEXT_OFFLINE_STEP_REAUTHORIZATION",
    )
    if audit.get("continuation_route") != route_next:
        raise ValueError(
            "Phase 8 post-step audit is not routed to another "
            "automatic offline step"
        )
    for field in (
        "post_step_audit_ready",
        "requires_separate_phase8_action",
        "requires_new_human_authorization",
        "next_offline_step_reauthorization_required",
        "phase7_dependency_satisfied",
        "phase8_research_only",
        "phase8_read_only",
    ):
        if audit.get(field) is not True:
            raise ValueError(
                f"Phase 8 continuation request requires audit {field}=true"
            )
    for field in (
        "paper_challenger_transition_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "new_live_capital_authorized",
        "phase8_execution_authorized",
        "phase8_promotion_authorized",
    ):
        if audit.get(field) is not False:
            raise ValueError(
                f"Phase 8 continuation request refuses audit {field}=true"
            )

    debt_type = audit.get("next_debt_type")
    scope = audit.get("next_scope")
    if debt_type not in ALLOWED_DEBT_TYPES:
        raise ValueError(
            "Phase 8 continuation next debt type is not allowed"
        )
    if not isinstance(scope, str) or not scope:
        raise ValueError("Phase 8 continuation next scope is invalid")

    operator = audit.get("fresh_phase8_operator_handoff")
    if not isinstance(operator, dict):
        raise ValueError(
            "Phase 8 continuation operator handoff is missing"
        )
    if operator.get("status") != "AUTOMATIC_ACTION":
        raise ValueError(
            "Phase 8 continuation operator handoff is not automatic"
        )
    if operator.get("automatic_action_available") is not True:
        raise ValueError(
            "Phase 8 continuation automatic action is unavailable"
        )
    if operator.get("operator_action_required") is not False:
        raise ValueError(
            "Phase 8 continuation action unexpectedly requires operator"
        )
    if operator.get("manual_input_required") is not False:
        raise ValueError(
            "Phase 8 continuation action unexpectedly requires input"
        )
    if (
        operator.get("debt_type") != debt_type
        or operator.get("scope") != scope
    ):
        raise ValueError(
            "Phase 8 continuation audit/operator action mismatch"
        )
    reason = operator.get("reason")
    suggested = operator.get("suggested_command")
    if not isinstance(reason, str) or not reason:
        raise ValueError("Phase 8 continuation action reason is invalid")
    if not isinstance(suggested, str) or not suggested:
        raise ValueError(
            "Phase 8 continuation suggested command is invalid"
        )

    if Path(str(audit["production_repository"])).resolve() != production:
        raise ValueError(
            "Phase 8 continuation audit repository binding mismatch"
        )
    database = _production_database(production)
    if Path(str(audit["pio_database_path"])).resolve() != database:
        raise ValueError(
            "Phase 8 continuation audit database binding mismatch"
        )

    current_state = audit_module._database_state(database)
    expected_state = {
        "database": audit["audit_database_sha256_after"],
        "wal": audit["audit_wal_sha256_after"],
        "shm": audit["audit_shm_sha256_after"],
    }
    if current_state != expected_state:
        raise ValueError(
            "production Pio database changed after Phase 8 post-step audit"
        )

    executor_module, _ = audit_module._load_reviewed(source)
    if not hasattr(executor_module, "_research_artifacts"):
        raise ValueError(
            "reviewed Phase 8 executor lacks research artifact inventory"
        )
    research_artifacts = executor_module._research_artifacts(
        database.parent
    )
    if research_artifacts != audit["audit_research_artifacts_after"]:
        raise ValueError(
            "production Phase 8 research artifacts changed after post-audit"
        )
    research_artifacts_sha256 = _sha256_bytes(
        _canonical_bytes(research_artifacts)
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
        "source_post_audit_sha256": audit["post_audit_sha256"],
        "source_execution_receipt_sha256": audit[
            "execution_receipt_sha256"
        ],
        "source_offline_step_request_sha256": audit[
            "offline_step_request_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": current_state["database"],
        "pio_wal_sha256": current_state["wal"],
        "pio_shm_sha256": current_state["shm"],
        "research_artifacts_sha256": research_artifacts_sha256,
        "research_artifact_count": len(research_artifacts),
        "debt_type": str(debt_type),
        "scope": scope,
        "reason": reason,
        "suggested_command": suggested,
        "continuation_route": route_next,
        "automatic_action_available": True,
        "allowed_offline_debt_type": True,
        "one_step_only": True,
        "request_ready": True,
        "explicit_human_authorization_required": True,
        "fresh_post_audit_recheck_required": True,
        "offline_step_authorization_present": False,
        "offline_step_execution_authorized": False,
        "offline_step_executed": False,
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
        "production_pio_database_modified": False,
        "production_research_artifacts_modified": False,
    }
    request = {
        **identity,
        "request_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_offline_continuation_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing request for exactly one additional "
            "Phase 8 automatic offline research step after a clean post-step "
            "audit. The request binds the audited DB and research-artifact "
            "state and requires a new explicit human authorization. It never "
            "executes the step, starts PAPER, uses live capital, submits a "
            "transaction, or persists Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--post-audit", required=True)
    parser.add_argument("--expected-post-audit-sha256", required=True)
    args = parser.parse_args()

    request = build_phase8_offline_continuation_request(
        repository=args.repo,
        source_tree=args.source_tree,
        post_audit_path=args.post_audit,
        expected_post_audit_sha256=args.expected_post_audit_sha256,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
