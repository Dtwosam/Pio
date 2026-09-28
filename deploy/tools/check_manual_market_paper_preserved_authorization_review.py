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
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_AUTHORIZATION_REVIEW_V1"

EVIDENCE_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_evidence_package.py"
)
MUTATION_REVIEW_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_mutation_review.py"
)
BACKUP_CAPTURE_TOOL = Path(
    "deploy/tools/capture_manual_market_paper_preserved_backups.py"
)

REVIEWED_SOURCE_BLOBS = {
    EVIDENCE_TOOL: "c3c61759246857855cbf28f844b4bc3e64ad8f3b",
    MUTATION_REVIEW_TOOL: "f4692a8208cd213f1b95d99f55d32b5bf7b7208c",
    BACKUP_CAPTURE_TOOL: "6c69bdf05d6621d38a0db33e1b97e123c1f2e6ca",
}

AUTHORIZATION_FIELDS = (
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

SCOPE_ENTRY_FIELDS = (
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
    "backup_observed_state",
    "backup_observed_blob",
    "backup_observed_sha256",
    "backup_observed_size",
    "backup_observed_mode",
    "backup_integrity_matches",
    "operation_ready",
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "evidence_package_sha256",
    "saved_mutation_review_sha256",
    "fresh_mutation_review_sha256",
    "backup_capture_sha256",
    "plan_sha256",
    "private_bundle_sha256",
    "production_repository",
    "backup_dir",
    "backup_dir_private",
    "fresh_review_matches_saved",
    "fresh_review_ready",
    "backup_capture_ready",
    "operation_scope",
    "operation_count",
    "all_scope_operations_ready",
    "backup_integrity_ready",
    "authorization_review_ready",
    "authorization_granted",
    "requires_explicit_human_authorization",
    "requires_fresh_execution_recheck",
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


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _verify_reviewed_source(source: Path) -> tuple[Any, Any, Any]:
    modules: list[Any] = []
    for index, (relative, expected) in enumerate(
        sorted(REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0]))
    ):
        path = source / relative
        if not path.is_file():
            raise ValueError(f"reviewed authorization artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed authorization artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed authorization artifact mismatch: {relative}")
        modules.append(
            _load_module(
                path,
                f"manual_market_paper_preserved_authorization_review_{index}",
            )
        )

    by_path = {
        path: module
        for path, module in zip(
            sorted(REVIEWED_SOURCE_BLOBS, key=str),
            modules,
            strict=True,
        )
    }
    return (
        by_path[EVIDENCE_TOOL],
        by_path[MUTATION_REVIEW_TOOL],
        by_path[BACKUP_CAPTURE_TOOL],
    )


def _existing_private_backup_dir(raw: str | Path) -> tuple[Path, bool]:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("authorization-review backup directory must be absolute")
    if path.is_symlink():
        raise ValueError("authorization-review backup directory must not be a symlink")
    resolved = path.resolve(strict=True)
    var_tmp = Path("/var/tmp").resolve()
    if resolved == var_tmp or var_tmp not in resolved.parents:
        raise ValueError("authorization-review backup directory must be under /var/tmp")
    st = os.lstat(resolved)
    if not stat.S_ISDIR(st.st_mode):
        raise ValueError("authorization-review backup directory is not a directory")
    mode = stat.S_IMODE(st.st_mode)
    return resolved, (mode & 0o077) == 0


def _scope_entry(
    *,
    review_operation: dict[str, Any],
    backup_entry: dict[str, Any],
    backup_root: Path,
    backup_module: Any,
) -> dict[str, Any]:
    for field in (
        "index",
        "layer",
        "operation",
        "path",
        "plan_operation_sha256",
        "expected_current_blob",
        "rollback_operation",
        "rollback_blob",
        "backup_required",
    ):
        if review_operation.get(field) != backup_entry.get(field):
            raise ValueError(
                f"authorization-review operation/backup mismatch for {field}: "
                f"{review_operation.get('path')}"
            )

    if review_operation.get("current_matches_expected") is not True:
        raise ValueError(
            f"authorization-review current production blob is not ready: "
            f"{review_operation.get('path')}"
        )
    if review_operation.get("source_matches_target") is not True:
        raise ValueError(
            f"authorization-review source target is not ready: "
            f"{review_operation.get('path')}"
        )
    if review_operation.get("operation_ready") is not True:
        raise ValueError(
            f"authorization-review saved operation is not ready: "
            f"{review_operation.get('path')}"
        )

    backup_required = bool(backup_entry["backup_required"])
    backup_relative_path = backup_entry.get("backup_relative_path")
    backup_git_blob = backup_entry.get("backup_git_blob")
    backup_sha256 = backup_entry.get("backup_sha256")
    backup_size = backup_entry.get("backup_size")
    backup_mode = backup_entry.get("backup_mode")

    observed_state: str
    observed_blob: str | None
    observed_sha: str | None
    observed_size: int | None
    observed_mode: int | None
    integrity: bool

    if backup_required:
        relative = backup_module._safe_backup_relative_path(backup_relative_path)
        observed_state, payload, observed_mode = backup_module._read_regular_no_follow(
            backup_root,
            relative,
        )
        if observed_state == "BLOB_READ" and payload is not None:
            observed_blob = _git_blob_sha_bytes(payload)
            observed_sha = _sha256_bytes(payload)
            observed_size = len(payload)
        else:
            observed_blob = None
            observed_sha = None
            observed_size = None

        integrity = bool(
            observed_state == "BLOB_READ"
            and observed_blob == backup_git_blob
            and observed_blob == backup_entry.get("expected_current_blob")
            and observed_sha == backup_sha256
            and observed_size == backup_size
            and observed_mode == backup_mode
            and backup_entry.get("backup_matches_expected") is True
            and backup_entry.get("entry_ready") is True
        )
    else:
        observed_state = "NOT_REQUIRED"
        observed_blob = None
        observed_sha = None
        observed_size = None
        observed_mode = None
        integrity = bool(
            backup_relative_path is None
            and backup_git_blob is None
            and backup_sha256 is None
            and backup_size is None
            and backup_mode is None
            and backup_entry.get("backup_materialized") is False
            and backup_entry.get("backup_matches_expected") is True
            and backup_entry.get("entry_ready") is True
        )

    return {
        "index": review_operation["index"],
        "layer": review_operation["layer"],
        "operation": review_operation["operation"],
        "path": review_operation["path"],
        "plan_operation_sha256": review_operation["plan_operation_sha256"],
        "expected_current_blob": review_operation["expected_current_blob"],
        "target_blob": review_operation["target_blob"],
        "rollback_operation": review_operation["rollback_operation"],
        "rollback_blob": review_operation["rollback_blob"],
        "backup_required": backup_required,
        "backup_relative_path": backup_relative_path,
        "backup_git_blob": backup_git_blob,
        "backup_sha256": backup_sha256,
        "backup_size": backup_size,
        "backup_mode": backup_mode,
        "backup_observed_state": observed_state,
        "backup_observed_blob": observed_blob,
        "backup_observed_sha256": observed_sha,
        "backup_observed_size": observed_size,
        "backup_observed_mode": observed_mode,
        "backup_integrity_matches": integrity,
        "operation_ready": bool(
            review_operation["operation_ready"] and integrity
        ),
    }


def validate_authorization_review(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("authorization review must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"authorization_review_sha256"}:
        raise ValueError("authorization review fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported authorization review format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected authorization review artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("authorization review source lineage mismatch")

    for field, length in (
        ("evidence_package_sha256", 64),
        ("saved_mutation_review_sha256", 64),
        ("fresh_mutation_review_sha256", 64),
        ("backup_capture_sha256", 64),
        ("plan_sha256", 64),
        ("private_bundle_sha256", 64),
        ("authorization_review_sha256", 64),
    ):
        if not _is_hex_digest(report.get(field), length):
            raise ValueError(f"authorization review {field} is invalid")

    repository = report.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("authorization review production repository is invalid")
    backup_dir = report.get("backup_dir")
    if not isinstance(backup_dir, str) or not backup_dir.startswith("/var/tmp/"):
        raise ValueError("authorization review backup directory is invalid")

    for field in (
        "backup_dir_private",
        "fresh_review_matches_saved",
        "fresh_review_ready",
        "backup_capture_ready",
        "all_scope_operations_ready",
        "backup_integrity_ready",
        "authorization_review_ready",
        "authorization_granted",
        "requires_explicit_human_authorization",
        "requires_fresh_execution_recheck",
        "requires_separate_mutation_authorization",
        "production_file_modified",
        "production_repository_git_mutated",
        *AUTHORIZATION_FIELDS,
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(f"authorization review {field} must be boolean")

    scope = report.get("operation_scope")
    if not isinstance(scope, list):
        raise ValueError("authorization review operation scope must be a list")
    if report.get("operation_count") != len(scope):
        raise ValueError("authorization review operation count mismatch")

    for expected_index, entry in enumerate(scope):
        if not isinstance(entry, dict) or set(entry) != set(SCOPE_ENTRY_FIELDS):
            raise ValueError("authorization review scope entry schema mismatch")
        if entry["index"] != expected_index:
            raise ValueError("authorization review scope indexes are not contiguous")
        if not isinstance(entry.get("backup_required"), bool):
            raise ValueError("authorization review backup_required must be boolean")
        if not isinstance(entry.get("backup_integrity_matches"), bool):
            raise ValueError("authorization review backup integrity must be boolean")
        if not isinstance(entry.get("operation_ready"), bool):
            raise ValueError("authorization review operation_ready must be boolean")
        if not _is_hex_digest(entry.get("plan_operation_sha256"), 64):
            raise ValueError("authorization review operation digest is invalid")
        for field in ("expected_current_blob", "target_blob"):
            value = entry.get(field)
            if value is not None and not _is_hex_digest(value, 40):
                raise ValueError(f"authorization review {field} is invalid")
        rollback_blob = entry.get("rollback_blob")
        if rollback_blob is not None and not _is_hex_digest(rollback_blob, 40):
            raise ValueError("authorization review rollback blob is invalid")

        if entry["backup_required"]:
            for field, length in (
                ("backup_git_blob", 40),
                ("backup_sha256", 64),
                ("backup_observed_blob", 40),
                ("backup_observed_sha256", 64),
            ):
                if not _is_hex_digest(entry.get(field), length):
                    raise ValueError(f"authorization review {field} is invalid")
            for field in (
                "backup_size",
                "backup_mode",
                "backup_observed_size",
                "backup_observed_mode",
            ):
                value = entry.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"authorization review {field} is invalid")
            if not isinstance(entry.get("backup_relative_path"), str):
                raise ValueError("authorization review backup path is invalid")
        else:
            for field in (
                "backup_relative_path",
                "backup_git_blob",
                "backup_sha256",
                "backup_size",
                "backup_mode",
                "backup_observed_blob",
                "backup_observed_sha256",
                "backup_observed_size",
                "backup_observed_mode",
            ):
                if entry.get(field) is not None:
                    raise ValueError(
                        f"authorization review non-backup {field} must be null"
                    )
            if entry.get("backup_observed_state") != "NOT_REQUIRED":
                raise ValueError(
                    "authorization review non-backup state must be NOT_REQUIRED"
                )

        expected_entry_ready = bool(entry["backup_integrity_matches"])
        if entry["operation_ready"] is not expected_entry_ready:
            raise ValueError("authorization review operation readiness mismatch")

    expected_all_scope = all(entry["operation_ready"] for entry in scope)
    expected_backup_integrity = bool(
        report["backup_dir_private"]
        and all(entry["backup_integrity_matches"] for entry in scope)
    )
    if report["all_scope_operations_ready"] is not expected_all_scope:
        raise ValueError("authorization review operation aggregate mismatch")
    if report["backup_integrity_ready"] is not expected_backup_integrity:
        raise ValueError("authorization review backup integrity aggregate mismatch")

    expected_ready = bool(
        report["fresh_review_matches_saved"]
        and report["fresh_review_ready"]
        and report["backup_capture_ready"]
        and expected_all_scope
        and expected_backup_integrity
    )
    if report["authorization_review_ready"] is not expected_ready:
        raise ValueError("authorization review ready flag mismatch")

    if report["authorization_granted"] is not False:
        raise ValueError("authorization review must not grant authorization")
    if report["requires_explicit_human_authorization"] is not True:
        raise ValueError("authorization review requires explicit human authorization")
    if report["requires_fresh_execution_recheck"] is not True:
        raise ValueError("authorization review requires fresh execution recheck")
    if report["requires_separate_mutation_authorization"] is not True:
        raise ValueError("authorization review requires separate mutation authorization")
    if report["production_file_modified"] is not False:
        raise ValueError("authorization review must not modify production files")
    if report["production_repository_git_mutated"] is not False:
        raise ValueError("authorization review must not mutate production Git state")
    for field in AUTHORIZATION_FIELDS:
        if report[field] is not False:
            raise ValueError(f"authorization review requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["authorization_review_sha256"] != expected_digest:
        raise ValueError("authorization review digest mismatch")


def build_authorization_review(
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
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if production.is_symlink() or not production.is_dir():
        raise ValueError("production repository root is invalid")

    evidence_module, review_module, backup_module = _verify_reviewed_source(source)

    handoff = _load_json(handoff_path)
    plan = _load_json(plan_path)
    gate = _load_json(gate_path)
    saved_review = _load_json(mutation_review_path)
    evidence = _load_json(evidence_package_path)
    backup = _load_json(backup_capture_path)

    evidence_module.validate_evidence_package(evidence)
    review_module.validate_preserved_mutation_review(saved_review)
    backup_module.validate_backup_capture(backup)

    if evidence.get("evidence_complete") is not True:
        raise ValueError("preserved evidence package is not complete")
    if saved_review.get("review_ready") is not True:
        raise ValueError("saved preserved mutation review is not ready")
    if backup.get("backup_capture_ready") is not True:
        raise ValueError("preserved rollback backup capture is not ready")

    if evidence.get("mutation_review_sha256") != saved_review.get("review_sha256"):
        raise ValueError("authorization review evidence/review binding mismatch")
    if backup.get("evidence_package_sha256") != evidence.get("package_sha256"):
        raise ValueError("authorization review backup/evidence binding mismatch")
    if backup.get("mutation_review_sha256") != saved_review.get("review_sha256"):
        raise ValueError("authorization review backup/review binding mismatch")
    if backup.get("plan_sha256") != saved_review.get("plan_sha256"):
        raise ValueError("authorization review backup/plan binding mismatch")
    if backup.get("private_bundle_sha256") != saved_review.get("private_bundle_sha256"):
        raise ValueError("authorization review backup/private-bundle binding mismatch")
    if Path(str(backup["production_repository"])).resolve() != production:
        raise ValueError("authorization review production repository binding mismatch")

    for artifact_name, artifact in (
        ("evidence", evidence),
        ("saved review", saved_review),
        ("backup capture", backup),
    ):
        for field in AUTHORIZATION_FIELDS:
            if artifact.get(field) is not False:
                raise ValueError(
                    f"authorization review {artifact_name} requires {field}=false"
                )

    fresh_review = review_module.build_live_preserved_mutation_review(
        repository=production,
        source_tree=source,
        private_bundle_report_path=private_bundle_report_path,
        saved_handoff=handoff,
        saved_plan=plan,
        saved_gate=gate,
    )
    review_module.validate_preserved_mutation_review(fresh_review)

    fresh_matches_saved = fresh_review == saved_review
    fresh_ready = bool(fresh_review.get("review_ready"))

    backup_root, backup_dir_private = _existing_private_backup_dir(
        backup["backup_dir"]
    )
    if str(backup_root) != str(Path(str(backup["backup_dir"])).resolve()):
        raise ValueError("authorization review backup directory binding mismatch")

    review_operations = saved_review["operation_checks"]
    backup_entries = backup["operation_backups"]
    if len(review_operations) != len(backup_entries):
        raise ValueError("authorization review operation/backup count mismatch")

    scope = [
        _scope_entry(
            review_operation=operation,
            backup_entry=backup_entry,
            backup_root=backup_root,
            backup_module=backup_module,
        )
        for operation, backup_entry in zip(
            review_operations,
            backup_entries,
            strict=True,
        )
    ]

    all_scope_ready = all(item["operation_ready"] for item in scope)
    backup_integrity_ready = bool(
        backup_dir_private
        and all(item["backup_integrity_matches"] for item in scope)
    )
    authorization_review_ready = bool(
        fresh_matches_saved
        and fresh_ready
        and backup["backup_capture_ready"]
        and all_scope_ready
        and backup_integrity_ready
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
        "evidence_package_sha256": evidence["package_sha256"],
        "saved_mutation_review_sha256": saved_review["review_sha256"],
        "fresh_mutation_review_sha256": fresh_review["review_sha256"],
        "backup_capture_sha256": backup["backup_capture_sha256"],
        "plan_sha256": saved_review["plan_sha256"],
        "private_bundle_sha256": saved_review["private_bundle_sha256"],
        "production_repository": str(production),
        "backup_dir": str(backup_root),
        "backup_dir_private": backup_dir_private,
        "fresh_review_matches_saved": fresh_matches_saved,
        "fresh_review_ready": fresh_ready,
        "backup_capture_ready": bool(backup["backup_capture_ready"]),
        "operation_scope": scope,
        "operation_count": len(scope),
        "all_scope_operations_ready": all_scope_ready,
        "backup_integrity_ready": backup_integrity_ready,
        "authorization_review_ready": authorization_review_ready,
        "authorization_granted": False,
        "requires_explicit_human_authorization": True,
        "requires_fresh_execution_recheck": True,
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
    report = {
        **identity,
        "authorization_review_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_authorization_review(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Perform the final read-only preserved PAPER authorization review. "
            "The tool re-runs the live preserved mutation review, requires exact "
            "equality with the saved review, re-reads every rollback backup with "
            "no-follow checks, and emits the exact operation/rollback scope. "
            "It never grants authorization and still requires a fresh "
            "execution-time recheck plus explicit separate human authorization."
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
    args = parser.parse_args()

    report = build_authorization_review(
        repository=args.repo,
        source_tree=args.source_tree,
        private_bundle_report_path=args.private_bundle_report,
        handoff_path=args.handoff,
        plan_path=args.plan,
        gate_path=args.gate,
        mutation_review_path=args.mutation_review,
        evidence_package_path=args.evidence_package,
        backup_capture_path=args.backup_capture,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["authorization_review_ready"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
