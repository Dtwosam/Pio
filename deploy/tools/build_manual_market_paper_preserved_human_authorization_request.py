from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_HUMAN_AUTHORIZATION_REQUEST_V1"

AUTHORIZATION_REVIEW_TOOL = Path(
    "deploy/tools/check_manual_market_paper_preserved_authorization_review.py"
)
REVIEWED_SOURCE_BLOBS = {
    AUTHORIZATION_REVIEW_TOOL: "56cf2b9e460e22cb529aeabe438a57d9cfc8a994",
}

AUTHORIZATION_SCOPE = "PRESERVED_FILE_MUTATIONS_ONLY"
EXCLUDED_SCOPES = (
    "SERVICE_RESTART",
    "DETECTOR_CURSOR_MOVEMENT",
    "PAPER_TIMER_ENABLE",
    "TRANSACTION_SIGNING",
    "TRANSACTION_SUBMISSION",
    "LIVE_CAPITAL",
    "BROAD_GIT_OPERATIONS",
)

AUTHORIZATION_FIELDS = (
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

OPERATION_FIELDS = (
    "index",
    "layer",
    "operation",
    "path",
    "plan_operation_sha256",
    "expected_current_blob",
    "target_blob",
    "rollback_operation",
    "rollback_blob",
    "backup_required",
    "backup_relative_path",
    "backup_git_blob",
    "backup_sha256",
    "backup_size",
    "backup_mode",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "authorization_review_sha256",
    "production_repository",
    "backup_dir",
    "authorization_scope",
    "excluded_scopes",
    "operations",
    "operation_count",
    "authorization_request_ready",
    "approval_artifact_present",
    "authorization_granted",
    "explicit_human_authorization_required",
    "fresh_execution_recheck_required",
    "requires_separate_mutation_authorization",
    "production_file_modified",
    "production_repository_git_mutated",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


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


def _load_json(path: str | Path) -> dict[str, Any]:
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError(
            f"authorization request input must not be a symlink: {candidate}"
        )
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(
            f"authorization request input must be a regular file: {candidate}"
        )
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("authorization review must be a JSON object")
    return value


def _load_reviewed_authorization_module(source: Path) -> Any:
    path = source / AUTHORIZATION_REVIEW_TOOL
    if path.is_symlink() or not path.is_file():
        raise ValueError("reviewed authorization-review tool is missing")
    expected = REVIEWED_SOURCE_BLOBS[AUTHORIZATION_REVIEW_TOOL]
    if _git_blob_sha(path) != expected:
        raise ValueError("reviewed authorization-review tool blob mismatch")
    return _load_module(
        path,
        "manual_market_paper_preserved_human_auth_request_review",
    )


def _safe_relative_path(raw: Any) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError("authorization request operation path is invalid")
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise ValueError("authorization request operation path must be relative")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("authorization request operation path is unsafe")
    normalized = "/".join(pure.parts)
    if normalized != raw:
        raise ValueError("authorization request operation path is not normalized")
    return normalized


def _safe_backup_relative_path(raw: Any) -> str:
    value = _safe_relative_path(raw)
    parts = PurePosixPath(value).parts
    if not parts or parts[0] != "files":
        raise ValueError("authorization request backup path must be under files/")
    return value


def _request_operation(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": entry["index"],
        "layer": entry["layer"],
        "operation": entry["operation"],
        "path": entry["path"],
        "plan_operation_sha256": entry["plan_operation_sha256"],
        "expected_current_blob": entry["expected_current_blob"],
        "target_blob": entry["target_blob"],
        "rollback_operation": entry["rollback_operation"],
        "rollback_blob": entry["rollback_blob"],
        "backup_required": entry["backup_required"],
        "backup_relative_path": entry["backup_relative_path"],
        "backup_git_blob": entry["backup_git_blob"],
        "backup_sha256": entry["backup_sha256"],
        "backup_size": entry["backup_size"],
        "backup_mode": entry["backup_mode"],
    }


def validate_authorization_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError("human authorization request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("human authorization request fields do not match schema")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported human authorization request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected human authorization request artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("human authorization request source lineage mismatch")

    for field in ("authorization_review_sha256", "request_sha256"):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(f"human authorization request {field} is invalid")

    repository = request.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("human authorization request repository is invalid")
    backup_dir = request.get("backup_dir")
    if not isinstance(backup_dir, str) or not backup_dir.startswith("/var/tmp/"):
        raise ValueError("human authorization request backup directory is invalid")

    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("human authorization request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError("human authorization request excluded scopes mismatch")

    operations = request.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("human authorization request operations must be non-empty")
    if request.get("operation_count") != len(operations):
        raise ValueError("human authorization request operation count mismatch")

    seen_paths: set[str] = set()
    for expected_index, item in enumerate(operations):
        if not isinstance(item, dict) or set(item) != set(OPERATION_FIELDS):
            raise ValueError("human authorization request operation schema mismatch")
        if item.get("index") != expected_index:
            raise ValueError("human authorization request indexes are not contiguous")

        path = _safe_relative_path(item.get("path"))
        if path in seen_paths:
            raise ValueError("human authorization request path is duplicated")
        seen_paths.add(path)

        if not _is_hex_digest(item.get("plan_operation_sha256"), 64):
            raise ValueError("human authorization request operation digest is invalid")
        if not _is_hex_digest(item.get("target_blob"), 40):
            raise ValueError("human authorization request target_blob is invalid")

        expected_current = item.get("expected_current_blob")
        if expected_current is not None and not _is_hex_digest(expected_current, 40):
            raise ValueError(
                "human authorization request expected-current blob is invalid"
            )
        rollback_blob = item.get("rollback_blob")
        if rollback_blob is not None and not _is_hex_digest(rollback_blob, 40):
            raise ValueError("human authorization request rollback blob is invalid")

        if not isinstance(item.get("backup_required"), bool):
            raise ValueError(
                "human authorization request backup_required must be boolean"
            )

        if item["backup_required"]:
            if item.get("operation") not in {
                "UPDATE_PRESERVED_FILE",
                "UPDATE_FILE",
            }:
                raise ValueError(
                    "human authorization request backup operation mismatch"
                )
            if expected_current is None:
                raise ValueError(
                    "human authorization request update base is missing"
                )
            _safe_backup_relative_path(item.get("backup_relative_path"))
            for field, length in (
                ("backup_git_blob", 40),
                ("backup_sha256", 64),
            ):
                if not _is_hex_digest(item.get(field), length):
                    raise ValueError(
                        f"human authorization request {field} is invalid"
                    )
            for field in ("backup_size", "backup_mode"):
                value = item.get(field)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                ):
                    raise ValueError(
                        f"human authorization request {field} is invalid"
                    )
            if item["backup_git_blob"] != expected_current:
                raise ValueError(
                    "human authorization request backup blob mismatch"
                )
            if item.get("rollback_operation") != "RESTORE_EXPECTED_BLOB":
                raise ValueError(
                    "human authorization request rollback action mismatch"
                )
            if rollback_blob != expected_current:
                raise ValueError(
                    "human authorization request rollback blob mismatch"
                )
        else:
            if item.get("operation") != "CREATE_FILE":
                raise ValueError(
                    "human authorization request non-backup operation mismatch"
                )
            if expected_current is not None:
                raise ValueError(
                    "human authorization request create base must be null"
                )
            for field in (
                "backup_relative_path",
                "backup_git_blob",
                "backup_sha256",
                "backup_size",
                "backup_mode",
            ):
                if item.get(field) is not None:
                    raise ValueError(
                        f"human authorization request non-backup {field} must be null"
                    )
            if item.get("rollback_operation") != "DELETE_CREATED_FILE":
                raise ValueError(
                    "human authorization request create rollback mismatch"
                )
            if rollback_blob is not None:
                raise ValueError(
                    "human authorization request create rollback blob must be null"
                )

    if request.get("authorization_request_ready") is not True:
        raise ValueError("human authorization request must be ready")
    if request.get("approval_artifact_present") is not False:
        raise ValueError("human authorization request must not contain approval")
    if request.get("authorization_granted") is not False:
        raise ValueError("human authorization request must not grant authorization")
    if request.get("explicit_human_authorization_required") is not True:
        raise ValueError(
            "human authorization request requires explicit human authorization"
        )
    if request.get("fresh_execution_recheck_required") is not True:
        raise ValueError(
            "human authorization request requires a fresh execution recheck"
        )
    if request.get("requires_separate_mutation_authorization") is not True:
        raise ValueError(
            "human authorization request requires separate mutation authorization"
        )
    if request.get("production_file_modified") is not False:
        raise ValueError("human authorization request must not modify production")
    if request.get("production_repository_git_mutated") is not False:
        raise ValueError(
            "human authorization request must not mutate production Git"
        )
    for field in AUTHORIZATION_FIELDS:
        if request.get(field) is not False:
            raise ValueError(
                f"human authorization request requires {field}=false"
            )

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if request["request_sha256"] != expected_digest:
        raise ValueError("human authorization request digest mismatch")


def build_authorization_request(
    *,
    source_tree: str | Path,
    authorization_review_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    review_module = _load_reviewed_authorization_module(source)
    review = _load_json(authorization_review_path)
    review_module.validate_authorization_review(review)

    if review.get("authorization_review_ready") is not True:
        raise ValueError("terminal authorization review is not ready")
    if review.get("authorization_granted") is not False:
        raise ValueError(
            "terminal authorization review unexpectedly grants authorization"
        )
    if review.get("requires_explicit_human_authorization") is not True:
        raise ValueError(
            "terminal authorization review lacks human-authorization boundary"
        )
    if review.get("requires_fresh_execution_recheck") is not True:
        raise ValueError(
            "terminal authorization review lacks execution-recheck boundary"
        )
    if review.get("requires_separate_mutation_authorization") is not True:
        raise ValueError(
            "terminal authorization review lacks separate mutation boundary"
        )
    for field in AUTHORIZATION_FIELDS:
        if review.get(field) is not False:
            raise ValueError(
                f"terminal authorization review requires {field}=false"
            )

    scope = review.get("operation_scope")
    if not isinstance(scope, list) or not scope:
        raise ValueError("terminal authorization review operation scope is empty")
    operations = [_request_operation(entry) for entry in scope]

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        "authorization_review_sha256": review["authorization_review_sha256"],
        "production_repository": review["production_repository"],
        "backup_dir": review["backup_dir"],
        "authorization_scope": AUTHORIZATION_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "operations": operations,
        "operation_count": len(operations),
        "authorization_request_ready": True,
        "approval_artifact_present": False,
        "authorization_granted": False,
        "explicit_human_authorization_required": True,
        "fresh_execution_recheck_required": True,
        "requires_separate_mutation_authorization": True,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_authorization_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing human-authorization request from the exact "
            "terminal preserved authorization-review artifact. The request seals "
            "the reviewed operation/target/rollback scope and the exact "
            "authorization_review_sha256, but contains no approval, performs no "
            "production access or writes, and keeps every authorization flag false."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--authorization-review", required=True)
    args = parser.parse_args()

    request = build_authorization_request(
        source_tree=args.source_tree,
        authorization_review_path=args.authorization_review,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
