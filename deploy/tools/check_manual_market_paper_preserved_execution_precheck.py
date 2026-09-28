from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_EXECUTION_PRECHECK_V1"
AUTHORIZATION_SCOPE = "PRESERVED_FILE_MUTATIONS_ONLY"

AUTHORIZATION_REVIEW_TOOL = Path(
    "deploy/tools/check_manual_market_paper_preserved_authorization_review.py"
)
AUTHORIZATION_REQUEST_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_human_authorization_request.py"
)
SIGNED_AUTHORIZATION_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_signed_human_authorization.py"
)

REVIEWED_SOURCE_BLOBS = {
    AUTHORIZATION_REVIEW_TOOL: "56cf2b9e460e22cb529aeabe438a57d9cfc8a994",
    AUTHORIZATION_REQUEST_TOOL: "f7d31b7bbd421945fd8f7ca1353832e6e89da11b",
    SIGNED_AUTHORIZATION_TOOL: "948975c6c9244d553c3f36b5514195e354ca554d",
}

AUTHORIZATION_FIELDS = (
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

REQUEST_OPERATION_FIELDS = (
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

OPERATION_PRECHECK_FIELDS = (
    *REQUEST_OPERATION_FIELDS,
    "authorization_review_operation_ready",
    "backup_integrity_matches",
    "production_expected_state_revalidated",
    "target_source_revalidated",
    "operation_precheck_ready",
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_authorization_review_sha256",
    "fresh_authorization_review_sha256",
    "authorization_request_sha256",
    "saved_signed_authorization_verification_sha256",
    "fresh_signed_authorization_verification_sha256",
    "production_repository",
    "backup_dir",
    "approver_principal",
    "approval_id",
    "approval_expires_at",
    "authorization_scope",
    "operation_prechecks",
    "operation_count",
    "fresh_authorization_review_matches_saved",
    "request_binds_authorization_review",
    "request_scope_matches_review",
    "fresh_signed_authorization_matches_saved",
    "signed_authorization_binds_request",
    "human_authorization_verified",
    "approval_not_expired",
    "backup_integrity_ready",
    "all_operations_prechecked",
    "preserved_file_mutation_authorization_present",
    "execution_precheck_ready",
    "requires_immediate_per_operation_recheck",
    "execution_ready",
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


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any]:
    modules: dict[Path, Any] = {}
    for index, (relative, expected_blob) in enumerate(
        sorted(REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0]))
    ):
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"reviewed execution-precheck dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"reviewed execution-precheck dependency mismatch: {relative}"
            )
        modules[relative] = _load_module(
            path,
            f"manual_market_paper_preserved_execution_precheck_{index}",
        )
    return (
        modules[AUTHORIZATION_REVIEW_TOOL],
        modules[AUTHORIZATION_REQUEST_TOOL],
        modules[SIGNED_AUTHORIZATION_TOOL],
    )


def _request_projection(review_entry: dict[str, Any]) -> dict[str, Any]:
    return {field: review_entry[field] for field in REQUEST_OPERATION_FIELDS}


def _validate_request_scope(
    *,
    request: dict[str, Any],
    authorization_review: dict[str, Any],
) -> None:
    request_operations = request.get("operations")
    review_scope = authorization_review.get("operation_scope")
    if not isinstance(request_operations, list) or not isinstance(review_scope, list):
        raise ValueError("execution precheck operation scope is invalid")
    if len(request_operations) != len(review_scope):
        raise ValueError("execution precheck operation scope count mismatch")

    for request_entry, review_entry in zip(
        request_operations,
        review_scope,
        strict=True,
    ):
        if request_entry != _request_projection(review_entry):
            raise ValueError(
                "execution precheck request/review operation scope mismatch"
            )


def _operation_precheck(
    *,
    request_entry: dict[str, Any],
    review_entry: dict[str, Any],
) -> dict[str, Any]:
    if request_entry != _request_projection(review_entry):
        raise ValueError("execution precheck operation projection mismatch")
    if review_entry.get("operation_ready") is not True:
        raise ValueError("execution precheck authorization-review operation not ready")
    if review_entry.get("backup_integrity_matches") is not True:
        raise ValueError("execution precheck rollback backup integrity mismatch")

    return {
        **request_entry,
        "authorization_review_operation_ready": True,
        "backup_integrity_matches": True,
        "production_expected_state_revalidated": True,
        "target_source_revalidated": True,
        "operation_precheck_ready": True,
    }


def validate_execution_precheck(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("execution precheck must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"execution_precheck_sha256"}:
        raise ValueError("execution precheck fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported execution precheck format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected execution precheck artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("execution precheck source lineage mismatch")

    for field in (
        "saved_authorization_review_sha256",
        "fresh_authorization_review_sha256",
        "authorization_request_sha256",
        "saved_signed_authorization_verification_sha256",
        "fresh_signed_authorization_verification_sha256",
        "execution_precheck_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"execution precheck {field} is invalid")

    repository = report.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("execution precheck production repository is invalid")
    backup_dir = report.get("backup_dir")
    if not isinstance(backup_dir, str) or not backup_dir.startswith("/var/tmp/"):
        raise ValueError("execution precheck backup directory is invalid")
    if report.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("execution precheck authorization scope mismatch")

    for field in ("approver_principal", "approval_id", "approval_expires_at"):
        value = report.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"execution precheck {field} is invalid")

    operations = report.get("operation_prechecks")
    if not isinstance(operations, list) or not operations:
        raise ValueError("execution precheck operations must be non-empty")
    if report.get("operation_count") != len(operations):
        raise ValueError("execution precheck operation count mismatch")

    seen_paths: set[str] = set()
    for expected_index, entry in enumerate(operations):
        if not isinstance(entry, dict) or set(entry) != set(
            OPERATION_PRECHECK_FIELDS
        ):
            raise ValueError("execution precheck operation schema mismatch")
        if entry.get("index") != expected_index:
            raise ValueError("execution precheck operation indexes are not contiguous")
        path = entry.get("path")
        if not isinstance(path, str) or not path or path in seen_paths:
            raise ValueError("execution precheck operation path is invalid")
        seen_paths.add(path)

        if not _is_hex_digest(entry.get("plan_operation_sha256"), 64):
            raise ValueError("execution precheck operation digest is invalid")
        target_blob = entry.get("target_blob")
        if not _is_hex_digest(target_blob, 40):
            raise ValueError("execution precheck target blob is invalid")
        expected_current = entry.get("expected_current_blob")
        if expected_current is not None and not _is_hex_digest(
            expected_current,
            40,
        ):
            raise ValueError("execution precheck expected-current blob is invalid")
        rollback_blob = entry.get("rollback_blob")
        if rollback_blob is not None and not _is_hex_digest(rollback_blob, 40):
            raise ValueError("execution precheck rollback blob is invalid")

        layer = entry.get("layer")
        operation = entry.get("operation")
        rollback_operation = entry.get("rollback_operation")
        if not isinstance(layer, str) or not layer:
            raise ValueError("execution precheck operation layer is invalid")
        if operation not in {"UPDATE_PRESERVED_FILE", "UPDATE_FILE", "CREATE_FILE"}:
            raise ValueError("execution precheck operation type is invalid")
        if not isinstance(entry.get("backup_required"), bool):
            raise ValueError("execution precheck backup_required must be boolean")

        if entry["backup_required"]:
            backup_relative_path = entry.get("backup_relative_path")
            if not isinstance(backup_relative_path, str) or not backup_relative_path.startswith(
                "files/"
            ):
                raise ValueError("execution precheck backup path is invalid")
            if not _is_hex_digest(entry.get("backup_git_blob"), 40):
                raise ValueError("execution precheck backup Git blob is invalid")
            if not _is_hex_digest(entry.get("backup_sha256"), 64):
                raise ValueError("execution precheck backup SHA-256 is invalid")
            for field in ("backup_size", "backup_mode"):
                value = entry.get(field)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                ):
                    raise ValueError(f"execution precheck {field} is invalid")
            if expected_current is None:
                raise ValueError("execution precheck backup update base is missing")
            if entry["backup_git_blob"] != expected_current:
                raise ValueError("execution precheck backup blob mismatch")
            if rollback_operation != "RESTORE_EXPECTED_BLOB":
                raise ValueError("execution precheck rollback action mismatch")
            if rollback_blob != expected_current:
                raise ValueError("execution precheck rollback blob mismatch")
        else:
            if operation != "CREATE_FILE":
                raise ValueError("execution precheck non-backup operation mismatch")
            if expected_current is not None:
                raise ValueError("execution precheck create expected state must be absent")
            for field in (
                "backup_relative_path",
                "backup_git_blob",
                "backup_sha256",
                "backup_size",
                "backup_mode",
            ):
                if entry.get(field) is not None:
                    raise ValueError(
                        f"execution precheck non-backup {field} must be null"
                    )
            if rollback_operation != "DELETE_CREATED_FILE":
                raise ValueError("execution precheck create rollback mismatch")
            if rollback_blob is not None:
                raise ValueError("execution precheck create rollback blob must be null")

        for field in (
            "authorization_review_operation_ready",
            "backup_integrity_matches",
            "production_expected_state_revalidated",
            "target_source_revalidated",
            "operation_precheck_ready",
        ):
            if entry.get(field) is not True:
                raise ValueError(
                    f"execution precheck operation requires {field}=true"
                )

    for field in (
        "fresh_authorization_review_matches_saved",
        "request_binds_authorization_review",
        "request_scope_matches_review",
        "fresh_signed_authorization_matches_saved",
        "signed_authorization_binds_request",
        "human_authorization_verified",
        "approval_not_expired",
        "backup_integrity_ready",
        "all_operations_prechecked",
        "preserved_file_mutation_authorization_present",
        "execution_precheck_ready",
        "requires_immediate_per_operation_recheck",
    ):
        if report.get(field) is not True:
            raise ValueError(f"execution precheck requires {field}=true")

    if report.get("execution_ready") is not False:
        raise ValueError(
            "execution precheck must remain non-executable until per-operation recheck"
        )
    if report.get("production_file_modified") is not False:
        raise ValueError("execution precheck must not modify production files")
    if report.get("production_repository_git_mutated") is not False:
        raise ValueError("execution precheck must not mutate production Git state")
    for field in AUTHORIZATION_FIELDS:
        if report.get(field) is not False:
            raise ValueError(f"execution precheck requires {field}=false")

    if report["saved_authorization_review_sha256"] != report[
        "fresh_authorization_review_sha256"
    ]:
        raise ValueError("execution precheck authorization-review digest mismatch")
    if report["saved_signed_authorization_verification_sha256"] != report[
        "fresh_signed_authorization_verification_sha256"
    ]:
        raise ValueError("execution precheck signed-authorization digest mismatch")

    expected_all_operations = all(
        entry["operation_precheck_ready"] for entry in operations
    )
    if report["all_operations_prechecked"] is not expected_all_operations:
        raise ValueError("execution precheck operation aggregate mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["execution_precheck_sha256"] != expected_digest:
        raise ValueError("execution precheck digest mismatch")


def build_execution_precheck(
    *,
    repository: str | Path,
    source_tree: str | Path,
    private_bundle_report_path: str | Path,
    handoff_path: str | Path,
    plan_path: str | Path,
    gate_path: str | Path,
    mutation_review_path: str | Path,
    evidence_package_path: str | Path,
    backup_capture_path: str | Path,
    authorization_review_path: str | Path,
    authorization_request_path: str | Path,
    signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
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

    authorization_module, request_module, signed_module = _load_reviewed_modules(
        source
    )

    saved_review = _load_json(
        authorization_review_path,
        label="saved authorization review",
    )
    request = _load_json(
        authorization_request_path,
        label="human-authorization request",
    )
    saved_signed = _load_json(
        signed_authorization_verification_path,
        label="saved signed human-authorization verification",
    )

    authorization_module.validate_authorization_review(saved_review)
    request_module.validate_authorization_request(request)
    signed_module._validate_verification(saved_signed)

    if saved_review.get("authorization_review_ready") is not True:
        raise ValueError("saved authorization review is not ready")
    if request.get("authorization_request_ready") is not True:
        raise ValueError("human-authorization request is not ready")
    if saved_signed.get("human_authorization_verified") is not True:
        raise ValueError("saved signed human authorization is not verified")

    if request.get("authorization_review_sha256") != saved_review.get(
        "authorization_review_sha256"
    ):
        raise ValueError("execution precheck request/review binding mismatch")
    if request.get("production_repository") != saved_review.get(
        "production_repository"
    ):
        raise ValueError("execution precheck request repository binding mismatch")
    if Path(str(saved_review["production_repository"])).resolve() != production:
        raise ValueError("execution precheck production repository binding mismatch")

    _validate_request_scope(
        request=request,
        authorization_review=saved_review,
    )

    if saved_signed.get("request_sha256") != request.get("request_sha256"):
        raise ValueError("execution precheck signed request binding mismatch")
    if saved_signed.get("authorization_review_sha256") != saved_review.get(
        "authorization_review_sha256"
    ):
        raise ValueError("execution precheck signed review binding mismatch")
    if saved_signed.get("authorization_scope") != AUTHORIZATION_SCOPE:
        raise ValueError("execution precheck signed authorization scope mismatch")
    if saved_signed.get("execution_ready") is not False:
        raise ValueError("saved signed authorization must remain non-executable")
    if saved_signed.get("mutation_authorized") is not False:
        raise ValueError("saved signed authorization must not authorize mutation")

    fresh_review = authorization_module.build_authorization_review(
        repository=production,
        source_tree=source,
        private_bundle_report_path=private_bundle_report_path,
        handoff_path=handoff_path,
        plan_path=plan_path,
        gate_path=gate_path,
        mutation_review_path=mutation_review_path,
        evidence_package_path=evidence_package_path,
        backup_capture_path=backup_capture_path,
    )
    authorization_module.validate_authorization_review(fresh_review)
    if fresh_review != saved_review:
        raise ValueError(
            "execution precheck fresh authorization review differs from saved review"
        )

    fresh_signed = signed_module.verify_authorization(
        source_tree=source,
        request_path=authorization_request_path,
        payload_path=signed_payload_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        expected_allowed_signers_sha256=expected_allowed_signers_sha256,
    )
    signed_module._validate_verification(fresh_signed)
    if fresh_signed != saved_signed:
        raise ValueError(
            "execution precheck fresh signed authorization differs from saved verification"
        )

    operation_prechecks = [
        _operation_precheck(
            request_entry=request_entry,
            review_entry=review_entry,
        )
        for request_entry, review_entry in zip(
            request["operations"],
            fresh_review["operation_scope"],
            strict=True,
        )
    ]
    all_operations_prechecked = all(
        item["operation_precheck_ready"] for item in operation_prechecks
    )
    backup_integrity_ready = bool(fresh_review["backup_integrity_ready"])

    execution_precheck_ready = bool(
        fresh_review["authorization_review_ready"]
        and fresh_signed["human_authorization_verified"]
        and fresh_signed["approval_not_expired"]
        and backup_integrity_ready
        and all_operations_prechecked
    )
    if not execution_precheck_ready:
        raise ValueError("execution precheck failed closed")

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
        "saved_authorization_review_sha256": saved_review[
            "authorization_review_sha256"
        ],
        "fresh_authorization_review_sha256": fresh_review[
            "authorization_review_sha256"
        ],
        "authorization_request_sha256": request["request_sha256"],
        "saved_signed_authorization_verification_sha256": saved_signed[
            "verification_sha256"
        ],
        "fresh_signed_authorization_verification_sha256": fresh_signed[
            "verification_sha256"
        ],
        "production_repository": str(production),
        "backup_dir": fresh_review["backup_dir"],
        "approver_principal": fresh_signed["approver_principal"],
        "approval_id": fresh_signed["approval_id"],
        "approval_expires_at": fresh_signed["expires_at"],
        "authorization_scope": AUTHORIZATION_SCOPE,
        "operation_prechecks": operation_prechecks,
        "operation_count": len(operation_prechecks),
        "fresh_authorization_review_matches_saved": True,
        "request_binds_authorization_review": True,
        "request_scope_matches_review": True,
        "fresh_signed_authorization_matches_saved": True,
        "signed_authorization_binds_request": True,
        "human_authorization_verified": True,
        "approval_not_expired": True,
        "backup_integrity_ready": backup_integrity_ready,
        "all_operations_prechecked": all_operations_prechecked,
        "preserved_file_mutation_authorization_present": True,
        "execution_precheck_ready": execution_precheck_ready,
        "requires_immediate_per_operation_recheck": True,
        "execution_ready": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    report = {
        **identity,
        "execution_precheck_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_execution_precheck(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Perform the read-only execution precheck for a preserved PAPER "
            "mutation. The tool rebuilds the live terminal authorization review, "
            "re-verifies the short-lived detached human signature against the "
            "externally pinned trust root, and rebinds every exact operation and "
            "rollback backup. It never writes production and deliberately keeps "
            "execution_ready=false until a future writer performs an immediate "
            "per-operation expected-state recheck."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--gate", required=True)
    parser.add_argument("--mutation-review", required=True)
    parser.add_argument("--evidence-package", required=True)
    parser.add_argument("--backup-capture", required=True)
    parser.add_argument("--authorization-review", required=True)
    parser.add_argument("--authorization-request", required=True)
    parser.add_argument("--signed-authorization-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--expected-allowed-signers-sha256", required=True)
    args = parser.parse_args()

    report = build_execution_precheck(
        repository=args.repo,
        source_tree=args.source_tree,
        private_bundle_report_path=args.private_bundle_report,
        handoff_path=args.handoff,
        plan_path=args.plan,
        gate_path=args.gate,
        mutation_review_path=args.mutation_review,
        evidence_package_path=args.evidence_package,
        backup_capture_path=args.backup_capture,
        authorization_review_path=args.authorization_review,
        authorization_request_path=args.authorization_request,
        signed_authorization_verification_path=args.signed_authorization_verification,
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
