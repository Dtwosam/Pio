from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
from typing import Any
import sys


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_MUTATION_AUTH_REQUEST_V1"

EVIDENCE_PACKAGE_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_evidence_package.py"
)
MUTATION_REVIEW_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_mutation_review.py"
)
BACKUP_CAPTURE_TOOL = Path(
    "deploy/tools/capture_manual_market_paper_preserved_backups.py"
)

REVIEWED_SOURCE_BLOBS = {
    EVIDENCE_PACKAGE_TOOL: "c3c61759246857855cbf28f844b4bc3e64ad8f3b",
    MUTATION_REVIEW_TOOL: "f4692a8208cd213f1b95d99f55d32b5bf7b7208c",
    BACKUP_CAPTURE_TOOL: "6c69bdf05d6621d38a0db33e1b97e123c1f2e6ca",
}

AUTHORIZATION_SCOPE = "PRESERVED_FILE_MUTATIONS_ONLY"
EXCLUDED_SCOPES = (
    "SERVICE_RESTART",
    "DETECTOR_CURSOR_MOVEMENT",
    "PAPER_TIMER_ENABLE",
    "TRANSACTION_SIGNING",
    "TRANSACTION_SUBMISSION",
    "LIVE_CAPITAL",
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
    "source_origin",
    "expected_current_blob",
    "target_blob",
    "private_bundle_sha256",
    "backup_required",
    "backup_relative_path",
    "backup_git_blob",
    "backup_sha256",
    "backup_size",
    "rollback_operation",
    "rollback_blob",
)

REQUEST_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "evidence_package_sha256",
    "mutation_review_sha256",
    "backup_capture_sha256",
    "plan_sha256",
    "private_bundle_sha256",
    "production_repository",
    "authorization_scope",
    "excluded_scopes",
    "operations",
    "operation_count",
    "all_lineage_bound",
    "all_backup_material_reverified",
    "authorization_request_ready",
    "explicit_human_approval_required",
    "approval_artifact_present",
    "fresh_execution_recheck_required",
    "backup_material_must_remain_available",
    "requires_separate_mutation_authorization",
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


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
        raise ValueError(f"JSON artifact must not be a symlink: {candidate}")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"JSON artifact must be a regular file: {candidate}")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {candidate}")
    return value


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any]:
    modules = []
    for index, (relative, expected_blob) in enumerate(REVIEWED_SOURCE_BLOBS.items()):
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"reviewed auth-request artifact is missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"reviewed auth-request artifact mismatch: {relative}")
        modules.append(
            _load_module(path, f"manual_market_paper_preserved_auth_request_{index}")
        )
    return modules[0], modules[1], modules[2]


def _safe_relative_path(raw: Any) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError("mutation authorization request path is invalid")
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise ValueError("mutation authorization request path must be relative")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("mutation authorization request path is unsafe")
    normalized = "/".join(pure.parts)
    if normalized != raw:
        raise ValueError("mutation authorization request path is not normalized")
    return normalized


def _safe_backup_relative_path(raw: Any) -> str:
    value = _safe_relative_path(raw)
    parts = PurePosixPath(value).parts
    if not parts or parts[0] != "files":
        raise ValueError(
            "mutation authorization request backup path must be under files/"
        )
    return value


def _safe_backup_root(report: dict[str, Any]) -> Path:
    raw = report.get("backup_dir")
    if not isinstance(raw, str) or not raw.startswith("/var/tmp/"):
        raise ValueError("auth-request backup directory is invalid")
    candidate = Path(raw)
    if candidate.is_symlink():
        raise ValueError("auth-request backup directory must not be a symlink")
    resolved = candidate.resolve(strict=True)
    var_tmp = Path("/var/tmp").resolve()
    if var_tmp not in resolved.parents or not resolved.is_dir():
        raise ValueError("auth-request backup directory escaped reviewed scope")
    return resolved


def _has_symlink_parent(root: Path, target: Path) -> bool:
    current = target.parent
    while current != root:
        if current.is_symlink():
            return True
        if current == current.parent:
            return True
        current = current.parent
    return root.is_symlink()


def _verify_backup_material(
    *,
    backup_module: Any,
    backup_report: dict[str, Any],
) -> tuple[dict[int, dict[str, Any]], bool]:
    root = _safe_backup_root(backup_report)
    entries: dict[int, dict[str, Any]] = {}
    all_verified = True

    for item in backup_report["operation_backups"]:
        index = item["index"]
        if index in entries:
            raise ValueError("auth-request backup indexes are duplicated")
        entries[index] = item

        if not item["backup_required"]:
            continue

        relative = backup_module._safe_backup_relative_path(
            item["backup_relative_path"]
        )
        target = root / relative
        try:
            target.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise ValueError("auth-request backup path escapes backup root") from exc
        if _has_symlink_parent(root, target):
            raise ValueError("auth-request backup path has a symlinked parent")
        if target.is_symlink() or not target.is_file():
            raise ValueError("auth-request backup material is not a regular file")

        payload = target.read_bytes()
        verified = bool(
            _git_blob_sha_bytes(payload) == item["backup_git_blob"]
            and _sha256_bytes(payload) == item["backup_sha256"]
            and len(payload) == item["backup_size"]
            and item["backup_git_blob"] == item["expected_current_blob"]
            and item["backup_git_blob"] == item["rollback_blob"]
        )
        if not verified:
            raise ValueError(
                f"auth-request backup material mismatch: {item['path']}"
            )
        all_verified = all_verified and verified

    return entries, all_verified


def _request_operation(
    *,
    review_item: dict[str, Any],
    backup_item: dict[str, Any],
) -> dict[str, Any]:
    for field in ("index", "layer", "operation", "path", "plan_operation_sha256"):
        if backup_item.get(field) != review_item.get(field):
            raise ValueError(f"auth-request operation binding mismatch: {field}")

    if backup_item.get("expected_current_blob") != review_item.get(
        "expected_current_blob"
    ):
        raise ValueError("auth-request expected-current binding mismatch")
    if backup_item.get("rollback_operation") != review_item.get(
        "rollback_operation"
    ):
        raise ValueError("auth-request rollback-operation binding mismatch")
    if backup_item.get("rollback_blob") != review_item.get("rollback_blob"):
        raise ValueError("auth-request rollback-blob binding mismatch")
    if backup_item.get("backup_required") != review_item.get("backup_required"):
        raise ValueError("auth-request backup-required binding mismatch")
    if backup_item.get("entry_ready") is not True:
        raise ValueError("auth-request backup entry is not ready")
    if review_item.get("operation_ready") is not True:
        raise ValueError("auth-request mutation-review operation is not ready")

    return {
        "index": review_item["index"],
        "layer": review_item["layer"],
        "operation": review_item["operation"],
        "path": review_item["path"],
        "plan_operation_sha256": review_item["plan_operation_sha256"],
        "source_origin": review_item["source_origin"],
        "expected_current_blob": review_item["expected_current_blob"],
        "target_blob": review_item["target_blob"],
        "private_bundle_sha256": review_item["private_bundle_sha256"],
        "backup_required": review_item["backup_required"],
        "backup_relative_path": backup_item["backup_relative_path"],
        "backup_git_blob": backup_item["backup_git_blob"],
        "backup_sha256": backup_item["backup_sha256"],
        "backup_size": backup_item["backup_size"],
        "rollback_operation": review_item["rollback_operation"],
        "rollback_blob": review_item["rollback_blob"],
    }


def validate_authorization_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ValueError("mutation authorization request must be a JSON object")
    if set(request) != set(REQUEST_FIELDS) | {"request_sha256"}:
        raise ValueError("mutation authorization request fields do not match schema")
    if request.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported mutation authorization request format")
    if request.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected mutation authorization request artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if request.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("mutation authorization request source lineage mismatch")

    for field in (
        "evidence_package_sha256",
        "mutation_review_sha256",
        "backup_capture_sha256",
        "plan_sha256",
        "private_bundle_sha256",
        "request_sha256",
    ):
        if not _is_hex_digest(request.get(field), 64):
            raise ValueError(f"mutation authorization request {field} is invalid")

    repository = request.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("mutation authorization request repository is invalid")
    if request.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("mutation authorization request scope mismatch")
    if request.get("excluded_scopes") != list(EXCLUDED_SCOPES):
        raise ValueError("mutation authorization request excluded scopes mismatch")

    operations = request.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError(
            "mutation authorization request operations must be a non-empty list"
        )
    if request.get("operation_count") != len(operations):
        raise ValueError("mutation authorization request operation count mismatch")

    seen: set[str] = set()
    for expected_index, item in enumerate(operations):
        if not isinstance(item, dict) or set(item) != set(OPERATION_FIELDS):
            raise ValueError("mutation authorization request operation schema mismatch")
        if item.get("index") != expected_index:
            raise ValueError("mutation authorization request indexes are not contiguous")
        path = _safe_relative_path(item.get("path"))
        if path in seen:
            raise ValueError("mutation authorization request path is duplicated")
        seen.add(path)
        if not _is_hex_digest(item.get("plan_operation_sha256"), 64):
            raise ValueError("mutation authorization request operation digest is invalid")
        if not _is_hex_digest(item.get("target_blob"), 40):
            raise ValueError("mutation authorization request target blob is invalid")

        expected_current = item.get("expected_current_blob")
        if expected_current is not None and not _is_hex_digest(expected_current, 40):
            raise ValueError("mutation authorization request expected-current blob is invalid")

        source_origin = item.get("source_origin")
        private_bundle_sha = item.get("private_bundle_sha256")
        operation = item.get("operation")
        if operation == "UPDATE_PRESERVED_FILE":
            if source_origin != "PRIVATE_BUNDLE":
                raise ValueError(
                    "mutation authorization request preserved source origin mismatch"
                )
            if private_bundle_sha != request["private_bundle_sha256"]:
                raise ValueError(
                    "mutation authorization request preserved bundle mismatch"
                )
        elif operation in {"CREATE_FILE", "UPDATE_FILE"}:
            if source_origin != "REVIEWED_SOURCE":
                raise ValueError(
                    "mutation authorization request standard source origin mismatch"
                )
            if private_bundle_sha is not None:
                raise ValueError(
                    "mutation authorization request standard private bundle must be null"
                )
        else:
            raise ValueError(
                "mutation authorization request operation type is invalid"
            )

        if item.get("backup_required") is True:
            if not _is_hex_digest(expected_current, 40):
                raise ValueError("mutation authorization request update base is invalid")
            _safe_backup_relative_path(item.get("backup_relative_path"))
            for field, length in (
                ("backup_git_blob", 40),
                ("backup_sha256", 64),
            ):
                if not _is_hex_digest(item.get(field), length):
                    raise ValueError(
                        f"mutation authorization request {field} is invalid"
                    )
            size = item.get("backup_size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError("mutation authorization request backup size is invalid")
            if item["backup_git_blob"] != expected_current:
                raise ValueError("mutation authorization request backup blob mismatch")
            if item.get("rollback_operation") != "RESTORE_EXPECTED_BLOB":
                raise ValueError("mutation authorization request rollback action mismatch")
            if item.get("rollback_blob") != expected_current:
                raise ValueError("mutation authorization request rollback blob mismatch")
        else:
            if item.get("operation") != "CREATE_FILE":
                raise ValueError("mutation authorization request no-backup operation mismatch")
            for field in (
                "backup_relative_path",
                "backup_git_blob",
                "backup_sha256",
                "backup_size",
            ):
                if item.get(field) is not None:
                    raise ValueError(
                        f"mutation authorization request create {field} must be null"
                    )
            if item.get("rollback_operation") != "DELETE_CREATED_FILE":
                raise ValueError("mutation authorization request create rollback mismatch")
            if item.get("rollback_blob") is not None:
                raise ValueError("mutation authorization request create rollback blob must be null")

    for field in (
        "all_lineage_bound",
        "all_backup_material_reverified",
        "authorization_request_ready",
        "explicit_human_approval_required",
        "fresh_execution_recheck_required",
        "backup_material_must_remain_available",
        "requires_separate_mutation_authorization",
    ):
        if request.get(field) is not True:
            raise ValueError(f"mutation authorization request requires {field}=true")
    if request.get("approval_artifact_present") is not False:
        raise ValueError("mutation authorization request must not contain approval")
    for field in AUTHORIZATION_FIELDS:
        if request.get(field) is not False:
            raise ValueError(f"mutation authorization request requires {field}=false")

    identity = {field: request[field] for field in REQUEST_FIELDS}
    expected = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if request["request_sha256"] != expected:
        raise ValueError("mutation authorization request digest mismatch")


def build_authorization_request(
    *,
    source_tree: str | Path,
    evidence_package_path: str | Path,
    mutation_review_path: str | Path,
    backup_capture_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    evidence_module, review_module, backup_module = _load_reviewed_modules(source)
    evidence = _load_json(evidence_package_path)
    review = _load_json(mutation_review_path)
    backup = _load_json(backup_capture_path)

    evidence_module.validate_evidence_package(evidence)
    review_module.validate_preserved_mutation_review(review)
    backup_module.validate_backup_capture(backup)

    if evidence.get("evidence_complete") is not True:
        raise ValueError("mutation authorization request evidence package is incomplete")
    if review.get("review_ready") is not True:
        raise ValueError("mutation authorization request mutation review is not ready")
    if backup.get("backup_capture_ready") is not True:
        raise ValueError("mutation authorization request backup capture is not ready")

    lineage_bound = bool(
        evidence["mutation_review_sha256"] == review["review_sha256"]
        and backup["evidence_package_sha256"] == evidence["package_sha256"]
        and backup["mutation_review_sha256"] == review["review_sha256"]
        and evidence["plan_sha256"] == review["plan_sha256"] == backup["plan_sha256"]
        and evidence["private_bundle_sha256"]
        == review["private_bundle_sha256"]
        == backup["private_bundle_sha256"]
        and review["production_repository"] == backup["production_repository"]
    )
    if not lineage_bound:
        raise ValueError("mutation authorization request lineage mismatch")

    for artifact in (evidence, review, backup):
        for field in AUTHORIZATION_FIELDS:
            if artifact.get(field) is not False:
                raise ValueError(
                    f"mutation authorization request input requires {field}=false"
                )

    backup_entries, backups_verified = _verify_backup_material(
        backup_module=backup_module,
        backup_report=backup,
    )

    operations = []
    for review_item in review["operation_checks"]:
        index = review_item["index"]
        if index not in backup_entries:
            raise ValueError("mutation authorization request backup entry missing")
        operations.append(
            _request_operation(
                review_item=review_item,
                backup_item=backup_entries[index],
            )
        )

    request_ready = bool(
        lineage_bound
        and backups_verified
        and bool(operations)
        and len(operations) == review["operation_count"]
    )
    if not request_ready:
        raise ValueError("mutation authorization request failed closed")

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        "evidence_package_sha256": evidence["package_sha256"],
        "mutation_review_sha256": review["review_sha256"],
        "backup_capture_sha256": backup["backup_capture_sha256"],
        "plan_sha256": review["plan_sha256"],
        "private_bundle_sha256": review["private_bundle_sha256"],
        "production_repository": review["production_repository"],
        "authorization_scope": AUTHORIZATION_SCOPE,
        "excluded_scopes": list(EXCLUDED_SCOPES),
        "operations": operations,
        "operation_count": len(operations),
        "all_lineage_bound": lineage_bound,
        "all_backup_material_reverified": backups_verified,
        "authorization_request_ready": request_ready,
        "explicit_human_approval_required": True,
        "approval_artifact_present": False,
        "fresh_execution_recheck_required": True,
        "backup_material_must_remain_available": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    request = {
        **identity,
        "request_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_authorization_request(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a non-authorizing file-mutation authorization request from "
            "the complete preserved evidence package, ready mutation review, "
            "and verified rollback backup capture. The tool re-verifies physical "
            "backup files but performs no production access or mutation. The "
            "result explicitly contains no approval and keeps every authorization "
            "flag false."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--evidence-package", required=True)
    parser.add_argument("--mutation-review", required=True)
    parser.add_argument("--backup-capture", required=True)
    args = parser.parse_args()

    request = build_authorization_request(
        source_tree=args.source_tree,
        evidence_package_path=args.evidence_package,
        mutation_review_path=args.mutation_review,
        backup_capture_path=args.backup_capture,
    )
    print(json.dumps(request, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
