from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_BACKUP_CAPTURE_V1"

EVIDENCE_PACKAGE_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_evidence_package.py"
)
MUTATION_REVIEW_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_mutation_review.py"
)

REVIEWED_SOURCE_BLOBS = {
    EVIDENCE_PACKAGE_TOOL: "c3c61759246857855cbf28f844b4bc3e64ad8f3b",
    MUTATION_REVIEW_TOOL: "f4692a8208cd213f1b95d99f55d32b5bf7b7208c",
}

AUTHORIZATION_FIELDS = (
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

ENTRY_FIELDS = (
    "index",
    "layer",
    "operation",
    "path",
    "plan_operation_sha256",
    "backup_required",
    "expected_current_blob",
    "rollback_operation",
    "rollback_blob",
    "production_state",
    "production_observed_blob",
    "post_capture_observed_blob",
    "production_matches_expected",
    "post_capture_matches_expected",
    "backup_materialized",
    "backup_relative_path",
    "backup_git_blob",
    "backup_sha256",
    "backup_size",
    "backup_mode",
    "backup_matches_expected",
    "entry_ready",
)

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "evidence_package_sha256",
    "mutation_review_sha256",
    "plan_sha256",
    "private_bundle_sha256",
    "production_repository",
    "backup_dir",
    "backup_dir_under_var_tmp",
    "operation_backups",
    "operation_count",
    "backup_required_count",
    "backup_materialized_count",
    "all_current_files_match_expected",
    "all_required_backups_materialized",
    "all_backup_blobs_match_expected",
    "rollback_material_complete",
    "backup_capture_ready",
    "production_file_modified",
    "production_repository_git_mutated",
    "requires_fresh_execution_recheck",
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
    payload = path.read_bytes()
    return _git_blob_sha_bytes(payload)


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


def _verify_reviewed_source(source: Path) -> tuple[Any, Any]:
    modules = []
    for index, (relative, expected_blob) in enumerate(REVIEWED_SOURCE_BLOBS.items()):
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"reviewed backup-capture artifact is missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"reviewed backup-capture artifact mismatch: {relative}")
        modules.append(
            _load_module(path, f"manual_market_paper_preserved_backup_capture_{index}")
        )
    return modules[0], modules[1]


def _safe_relative_path(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError("backup-capture path is invalid")
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise ValueError(f"backup-capture path must be relative: {raw}")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"backup-capture path is unsafe: {raw}")
    if "/".join(pure.parts) != raw:
        raise ValueError(f"backup-capture path is not normalized: {raw}")
    return Path(*pure.parts)


def _parent_safety(root: Path, relative: Path) -> tuple[bool, str | None]:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            return False, "PARENT_SYMLINK"
        if not current.exists():
            return False, "PARENT_MISSING"
        if not current.is_dir():
            return False, "PARENT_NOT_DIRECTORY"
    return True, None


def _read_regular_no_follow(
    root: Path,
    relative: Path,
) -> tuple[str, bytes | None, int | None]:
    parent_safe, state = _parent_safety(root, relative)
    if not parent_safe:
        return str(state), None, None

    target = root / relative
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return "MISSING", None, None

    if stat.S_ISLNK(st.st_mode):
        return "TARGET_SYMLINK", None, None
    if not stat.S_ISREG(st.st_mode):
        return "NOT_REGULAR_FILE", None, None

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)

    try:
        fd = os.open(target, flags)
    except FileNotFoundError:
        return "MISSING", None, None
    except OSError as exc:
        if getattr(exc, "errno", None) == getattr(__import__("errno"), "ELOOP"):
            return "TARGET_SYMLINK", None, None
        raise

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            return "NOT_REGULAR_FILE", None, None
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        if len(payload) != opened.st_size:
            return "SIZE_CHANGED_DURING_READ", None, None
        return "BLOB_READ", payload, stat.S_IMODE(opened.st_mode)
    finally:
        os.close(fd)


def _inspect_absent(root: Path, relative: Path) -> tuple[str, bool]:
    parent_safe, state = _parent_safety(root, relative)
    if not parent_safe:
        return str(state), False
    target = root / relative
    try:
        os.lstat(target)
    except FileNotFoundError:
        return "ABSENT_AS_EXPECTED", True
    return "UNEXPECTED_PRESENT", False


def _safe_backup_dir(raw: str | Path) -> Path:
    output = Path(raw).expanduser()
    if not output.is_absolute():
        raise ValueError("backup output directory must be absolute")
    resolved = output.resolve(strict=False)
    var_tmp = Path("/var/tmp").resolve()
    if resolved == var_tmp or var_tmp not in resolved.parents:
        raise ValueError("backup output directory must be under /var/tmp")
    if output.exists():
        raise ValueError("backup output directory already exists")
    parent = output.parent
    if parent.is_symlink() or not parent.exists() or not parent.is_dir():
        raise ValueError("backup output parent is unsafe")
    return resolved


def _write_private_backup(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("backup write made no progress")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _capture_entry(
    *,
    operation: dict[str, Any],
    production: Path,
    backup_root: Path,
) -> dict[str, Any]:
    relative = _safe_relative_path(operation["path"])
    operation_type = operation["operation"]
    backup_required = bool(operation["backup_required"])
    expected_current = operation.get("expected_current_blob")
    rollback_operation = operation["rollback_operation"]
    rollback_blob = operation.get("rollback_blob")

    if operation_type == "CREATE_FILE":
        if backup_required or expected_current is not None:
            raise ValueError(f"create backup semantics are invalid: {operation['path']}")
        if rollback_operation != "DELETE_CREATED_FILE" or rollback_blob is not None:
            raise ValueError(f"create rollback semantics are invalid: {operation['path']}")
        production_state, current_matches = _inspect_absent(production, relative)
        return {
            "index": operation["index"],
            "layer": operation["layer"],
            "operation": operation_type,
            "path": operation["path"],
            "plan_operation_sha256": operation["plan_operation_sha256"],
            "backup_required": False,
            "expected_current_blob": None,
            "rollback_operation": rollback_operation,
            "rollback_blob": None,
            "production_state": production_state,
            "production_observed_blob": None,
            "post_capture_observed_blob": None,
            "production_matches_expected": current_matches,
            "post_capture_matches_expected": current_matches,
            "backup_materialized": False,
            "backup_relative_path": None,
            "backup_git_blob": None,
            "backup_sha256": None,
            "backup_size": None,
            "backup_mode": None,
            "backup_matches_expected": True,
            "entry_ready": current_matches,
        }

    if operation_type not in {"UPDATE_FILE", "UPDATE_PRESERVED_FILE"}:
        raise ValueError(f"backup-capture operation type is invalid: {operation_type}")
    if backup_required is not True or not _is_hex_digest(expected_current, 40):
        raise ValueError(f"update backup semantics are invalid: {operation['path']}")
    if rollback_operation != "RESTORE_EXPECTED_BLOB" or rollback_blob != expected_current:
        raise ValueError(f"update rollback semantics are invalid: {operation['path']}")

    production_state, payload, mode = _read_regular_no_follow(production, relative)
    observed_blob = _git_blob_sha_bytes(payload) if payload is not None else None
    current_matches = observed_blob == expected_current

    backup_relative_path: str | None = None
    backup_blob: str | None = None
    backup_sha: str | None = None
    backup_size: int | None = None
    materialized = False
    backup_matches = False

    if current_matches and payload is not None and mode is not None:
        backup_relative = Path("files") / relative
        backup_path = backup_root / backup_relative
        _write_private_backup(backup_path, payload)
        copied = backup_path.read_bytes()
        backup_blob = _git_blob_sha_bytes(copied)
        backup_sha = _sha256_bytes(copied)
        backup_size = len(copied)
        backup_relative_path = backup_relative.as_posix()
        materialized = True
        backup_matches = (
            backup_blob == expected_current
            and copied == payload
            and backup_size == len(payload)
        )

    post_state, post_payload, _post_mode = _read_regular_no_follow(production, relative)
    post_blob = _git_blob_sha_bytes(post_payload) if post_payload is not None else None
    post_matches = post_blob == expected_current

    entry_ready = bool(
        current_matches
        and post_matches
        and materialized
        and backup_matches
    )

    return {
        "index": operation["index"],
        "layer": operation["layer"],
        "operation": operation_type,
        "path": operation["path"],
        "plan_operation_sha256": operation["plan_operation_sha256"],
        "backup_required": True,
        "expected_current_blob": expected_current,
        "rollback_operation": rollback_operation,
        "rollback_blob": rollback_blob,
        "production_state": production_state if production_state == post_state else "STATE_CHANGED",
        "production_observed_blob": observed_blob,
        "post_capture_observed_blob": post_blob,
        "production_matches_expected": current_matches,
        "post_capture_matches_expected": post_matches,
        "backup_materialized": materialized,
        "backup_relative_path": backup_relative_path,
        "backup_git_blob": backup_blob,
        "backup_sha256": backup_sha,
        "backup_size": backup_size,
        "backup_mode": mode,
        "backup_matches_expected": backup_matches,
        "entry_ready": entry_ready,
    }


def validate_backup_capture(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("backup-capture report must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"backup_capture_sha256"}:
        raise ValueError("backup-capture fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported backup-capture format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected backup-capture artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("backup-capture source lineage mismatch")

    for field in (
        "evidence_package_sha256",
        "mutation_review_sha256",
        "plan_sha256",
        "private_bundle_sha256",
        "backup_capture_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"backup-capture {field} is invalid")

    repository = report.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("backup-capture production repository must be absolute")
    backup_dir = report.get("backup_dir")
    if not isinstance(backup_dir, str) or not backup_dir.startswith("/var/tmp/"):
        raise ValueError("backup-capture directory is invalid")
    if report.get("backup_dir_under_var_tmp") is not True:
        raise ValueError("backup-capture directory scope is invalid")

    entries = report.get("operation_backups")
    if not isinstance(entries, list):
        raise ValueError("backup-capture entries must be a list")
    operation_count = report.get("operation_count")
    if (
        not isinstance(operation_count, int)
        or isinstance(operation_count, bool)
        or operation_count < 0
        or operation_count != len(entries)
    ):
        raise ValueError("backup-capture operation count mismatch")

    for expected_index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != set(ENTRY_FIELDS):
            raise ValueError("backup-capture entry schema mismatch")
        if entry.get("index") != expected_index:
            raise ValueError("backup-capture indexes are not contiguous")
        _safe_relative_path(entry.get("path"))
        if not _is_hex_digest(entry.get("plan_operation_sha256"), 64):
            raise ValueError("backup-capture plan operation digest is invalid")
        if not isinstance(entry.get("backup_required"), bool):
            raise ValueError("backup-capture backup_required must be boolean")
        if not isinstance(entry.get("production_matches_expected"), bool):
            raise ValueError("backup-capture production match flag is invalid")
        if not isinstance(entry.get("post_capture_matches_expected"), bool):
            raise ValueError("backup-capture post-capture match flag is invalid")
        if not isinstance(entry.get("backup_materialized"), bool):
            raise ValueError("backup-capture materialized flag is invalid")
        if not isinstance(entry.get("backup_matches_expected"), bool):
            raise ValueError("backup-capture backup match flag is invalid")
        if not isinstance(entry.get("entry_ready"), bool):
            raise ValueError("backup-capture entry_ready must be boolean")

        expected_current = entry.get("expected_current_blob")
        if entry["backup_required"]:
            if not _is_hex_digest(expected_current, 40):
                raise ValueError("backup-capture update expected-current blob is invalid")
            if entry.get("rollback_operation") != "RESTORE_EXPECTED_BLOB":
                raise ValueError("backup-capture update rollback operation is invalid")
            if entry.get("rollback_blob") != expected_current:
                raise ValueError("backup-capture update rollback blob mismatch")
            backup_relative = entry.get("backup_relative_path")
            if not isinstance(backup_relative, str) or not backup_relative.startswith("files/"):
                raise ValueError("backup-capture backup path is invalid")
            for field, length in (
                ("production_observed_blob", 40),
                ("post_capture_observed_blob", 40),
                ("backup_git_blob", 40),
                ("backup_sha256", 64),
            ):
                if not _is_hex_digest(entry.get(field), length):
                    raise ValueError(f"backup-capture {field} is invalid")
            if entry["backup_git_blob"] != expected_current:
                raise ValueError("backup-capture backup blob mismatch")
            size = entry.get("backup_size")
            mode = entry.get("backup_mode")
            if (
                not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or not isinstance(mode, int)
                or isinstance(mode, bool)
                or mode < 0
            ):
                raise ValueError("backup-capture backup metadata is invalid")
            expected_ready = bool(
                entry["production_matches_expected"]
                and entry["post_capture_matches_expected"]
                and entry["backup_materialized"]
                and entry["backup_matches_expected"]
            )
        else:
            if expected_current is not None:
                raise ValueError("backup-capture create expected-current must be null")
            if entry.get("rollback_operation") != "DELETE_CREATED_FILE":
                raise ValueError("backup-capture create rollback operation is invalid")
            if entry.get("rollback_blob") is not None:
                raise ValueError("backup-capture create rollback blob must be null")
            for field in (
                "production_observed_blob",
                "post_capture_observed_blob",
                "backup_relative_path",
                "backup_git_blob",
                "backup_sha256",
                "backup_size",
                "backup_mode",
            ):
                if entry.get(field) is not None:
                    raise ValueError(f"backup-capture create {field} must be null")
            if entry["backup_materialized"] is not False:
                raise ValueError("backup-capture create must not materialize backup")
            if entry["backup_matches_expected"] is not True:
                raise ValueError("backup-capture create backup match must be true")
            expected_ready = bool(
                entry["production_matches_expected"]
                and entry["post_capture_matches_expected"]
            )

        if entry["entry_ready"] is not expected_ready:
            raise ValueError("backup-capture entry readiness mismatch")

    backup_required_count = sum(1 for entry in entries if entry["backup_required"])
    backup_materialized_count = sum(
        1 for entry in entries if entry["backup_materialized"]
    )
    if report.get("backup_required_count") != backup_required_count:
        raise ValueError("backup-capture required count mismatch")
    if report.get("backup_materialized_count") != backup_materialized_count:
        raise ValueError("backup-capture materialized count mismatch")

    all_current = all(
        entry["production_matches_expected"]
        and entry["post_capture_matches_expected"]
        for entry in entries
    )
    all_materialized = all(
        (not entry["backup_required"]) or entry["backup_materialized"]
        for entry in entries
    )
    all_backup_match = all(entry["backup_matches_expected"] for entry in entries)
    rollback_complete = bool(
        all_current
        and all_materialized
        and all_backup_match
        and backup_materialized_count == backup_required_count
    )
    if report.get("all_current_files_match_expected") is not all_current:
        raise ValueError("backup-capture current-file aggregate mismatch")
    if report.get("all_required_backups_materialized") is not all_materialized:
        raise ValueError("backup-capture materialization aggregate mismatch")
    if report.get("all_backup_blobs_match_expected") is not all_backup_match:
        raise ValueError("backup-capture backup-blob aggregate mismatch")
    if report.get("rollback_material_complete") is not rollback_complete:
        raise ValueError("backup-capture rollback completeness mismatch")
    if report.get("backup_capture_ready") is not rollback_complete:
        raise ValueError("backup-capture ready flag mismatch")

    if report.get("production_file_modified") is not False:
        raise ValueError("backup-capture must not modify production files")
    if report.get("production_repository_git_mutated") is not False:
        raise ValueError("backup-capture must not mutate production Git state")
    if report.get("requires_fresh_execution_recheck") is not True:
        raise ValueError("backup-capture must require fresh execution recheck")
    if report.get("requires_separate_mutation_authorization") is not True:
        raise ValueError("backup-capture must require separate mutation authorization")
    for field in AUTHORIZATION_FIELDS:
        if report.get(field) is not False:
            raise ValueError(f"backup-capture requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["backup_capture_sha256"] != expected_digest:
        raise ValueError("backup-capture digest mismatch")


def build_backup_capture(
    *,
    source_tree: str | Path,
    evidence_package_path: str | Path,
    mutation_review_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    evidence_module, review_module = _verify_reviewed_source(source)
    evidence = _load_json(evidence_package_path)
    review = _load_json(mutation_review_path)

    evidence_module.validate_evidence_package(evidence)
    review_module.validate_preserved_mutation_review(review)

    if evidence.get("evidence_complete") is not True:
        raise ValueError("preserved evidence package is not complete")
    if review.get("review_ready") is not True:
        raise ValueError("preserved mutation review is not ready")
    if evidence.get("mutation_review_sha256") != review.get("review_sha256"):
        raise ValueError("backup-capture mutation review is not bound to evidence package")
    if evidence.get("plan_sha256") != review.get("plan_sha256"):
        raise ValueError("backup-capture plan digest mismatch")
    if evidence.get("private_bundle_sha256") != review.get("private_bundle_sha256"):
        raise ValueError("backup-capture private-bundle digest mismatch")
    if review.get("requires_backup_capture_before_mutation") is not True:
        raise ValueError("preserved mutation review does not require backup capture")
    if review.get("backup_material_captured") is not False:
        raise ValueError("preserved mutation review unexpectedly claims backup capture")
    for field in AUTHORIZATION_FIELDS:
        if evidence.get(field) is not False or review.get(field) is not False:
            raise ValueError(f"backup-capture input requires {field}=false")

    production = Path(str(review["production_repository"]))
    if production.is_symlink() or not production.is_dir():
        raise ValueError("production repository root is invalid")

    output = _safe_backup_dir(output_dir)
    output.mkdir(mode=0o700)

    try:
        entries = [
            _capture_entry(
                operation=operation,
                production=production,
                backup_root=output,
            )
            for operation in review["operation_checks"]
        ]

        backup_required_count = sum(
            1 for entry in entries if entry["backup_required"]
        )
        backup_materialized_count = sum(
            1 for entry in entries if entry["backup_materialized"]
        )
        all_current = all(
            entry["production_matches_expected"]
            and entry["post_capture_matches_expected"]
            for entry in entries
        )
        all_materialized = all(
            (not entry["backup_required"]) or entry["backup_materialized"]
            for entry in entries
        )
        all_backup_match = all(
            entry["backup_matches_expected"] for entry in entries
        )
        rollback_complete = bool(
            all_current
            and all_materialized
            and all_backup_match
            and backup_materialized_count == backup_required_count
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
            "mutation_review_sha256": review["review_sha256"],
            "plan_sha256": review["plan_sha256"],
            "private_bundle_sha256": review["private_bundle_sha256"],
            "production_repository": str(production.resolve()),
            "backup_dir": str(output),
            "backup_dir_under_var_tmp": True,
            "operation_backups": entries,
            "operation_count": len(entries),
            "backup_required_count": backup_required_count,
            "backup_materialized_count": backup_materialized_count,
            "all_current_files_match_expected": all_current,
            "all_required_backups_materialized": all_materialized,
            "all_backup_blobs_match_expected": all_backup_match,
            "rollback_material_complete": rollback_complete,
            "backup_capture_ready": rollback_complete,
            "production_file_modified": False,
            "production_repository_git_mutated": False,
            "requires_fresh_execution_recheck": True,
            "requires_separate_mutation_authorization": True,
            "production_deployment_authorized": False,
            "mutation_authorized": False,
            "service_restart_authorized": False,
            "detector_cursor_movement_authorized": False,
            "paper_timer_enable_authorized": False,
            "live_capital_authorized": False,
        }
        report = {
            **identity,
            "backup_capture_sha256": hashlib.sha256(
                _canonical_bytes(identity)
            ).hexdigest(),
        }
        validate_backup_capture(report)
        if not report["backup_capture_ready"]:
            raise ValueError("backup capture failed closed")
        return report
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture exact rollback bytes for every backup-required operation in "
            "the sealed preserved mutation review. Production files are opened "
            "read-only with no-follow checks and copied only into a new private "
            "/var/tmp directory. The tool never mutates production or Git state, "
            "and a successful capture still requires fresh execution-time "
            "rechecks plus separate mutation authorization."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--evidence-package", required=True)
    parser.add_argument("--mutation-review", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    report = build_backup_capture(
        source_tree=args.source_tree,
        evidence_package_path=args.evidence_package,
        mutation_review_path=args.mutation_review,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
