from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import sys
from typing import Any, Iterator


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_FILE_MUTATION_RECEIPT_V1"

EXECUTION_PRECHECK_TOOL = Path(
    "deploy/tools/check_manual_market_paper_preserved_execution_precheck.py"
)
MUTATION_REVIEW_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_mutation_review.py"
)
BACKUP_CAPTURE_TOOL = Path(
    "deploy/tools/capture_manual_market_paper_preserved_backups.py"
)

REVIEWED_SOURCE_BLOBS = {
    EXECUTION_PRECHECK_TOOL: "0e30043609b960e1fea9a2289bff777ce1d8290d",
    MUTATION_REVIEW_TOOL: "f4692a8208cd213f1b95d99f55d32b5bf7b7208c",
    BACKUP_CAPTURE_TOOL: "6c69bdf05d6621d38a0db33e1b97e123c1f2e6ca",
}

WRITER_LOCK = Path("/var/tmp/pio-manual-paper-preserved-file-mutation.lock")
CREATE_FILE_MODE = 0o644


class OperationRollbackError(RuntimeError):
    pass


RESULT_FIELDS = (
    "index",
    "layer",
    "operation",
    "path",
    "expected_current_blob",
    "before_blob",
    "target_blob",
    "after_blob",
    "target_mode",
    "immediate_expected_state_recheck_passed",
    "source_bytes_reverified",
    "rollback_material_reverified",
    "write_applied",
    "post_write_verified",
)

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "execution_precheck_sha256",
    "production_repository",
    "approver_principal",
    "approval_id",
    "approval_expires_at",
    "operation_results",
    "operation_count",
    "all_operations_applied",
    "preserved_file_mutation_authorization_present",
    "file_mutation_completed",
    "requires_post_mutation_validation",
    "rollback_performed",
    "production_repository_git_mutated",
    "service_restart_performed",
    "detector_cursor_moved",
    "paper_timer_enabled",
    "transaction_signed",
    "transaction_submitted",
    "live_capital_deployed",
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


def _safe_relative_path(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError("preserved writer path is invalid")
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise ValueError("preserved writer path must be relative")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("preserved writer path is unsafe")
    if "/".join(pure.parts) != raw:
        raise ValueError("preserved writer path is not normalized")
    return Path(*pure.parts)


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any]:
    modules: dict[Path, Any] = {}
    for index, (relative, expected_blob) in enumerate(
        sorted(REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0]))
    ):
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"reviewed writer dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"reviewed writer dependency mismatch: {relative}")
        modules[relative] = _load_module(
            path,
            f"manual_market_paper_preserved_file_writer_{index}",
        )
    return (
        modules[EXECUTION_PRECHECK_TOOL],
        modules[MUTATION_REVIEW_TOOL],
        modules[BACKUP_CAPTURE_TOOL],
    )


@contextmanager
def _writer_lock() -> Iterator[None]:
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)

    fd = os.open(WRITER_LOCK, flags, 0o600)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("preserved writer lock is not a regular file")
        if stat.S_IMODE(opened.st_mode) & 0o077:
            raise ValueError("preserved writer lock permissions are not private")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another preserved file writer is active") from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _parent_dir(
    *,
    backup_module: Any,
    root: Path,
    relative: Path,
) -> Path:
    safe, state = backup_module._parent_safety(root, relative)
    if not safe:
        raise ValueError(f"preserved writer parent is unsafe: {state}")
    parent = root / relative.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("preserved writer parent directory is invalid")
    return parent


def _read_regular(
    *,
    backup_module: Any,
    root: Path,
    relative: Path,
    label: str,
) -> tuple[bytes, int]:
    state, payload, mode = backup_module._read_regular_no_follow(root, relative)
    if state != "BLOB_READ" or payload is None or mode is None:
        raise ValueError(f"{label} is not a stable regular file: {state}")
    return payload, mode


def _inspect_expected_state(
    *,
    backup_module: Any,
    production: Path,
    relative: Path,
    expected_blob: str | None,
    expected_mode: int | None,
) -> tuple[str | None, int | None]:
    if expected_blob is None:
        state, absent = backup_module._inspect_absent(production, relative)
        if state != "ABSENT_AS_EXPECTED" or not absent:
            raise ValueError(
                f"preserved writer expected absent target changed: {relative}"
            )
        return None, None

    payload, mode = _read_regular(
        backup_module=backup_module,
        root=production,
        relative=relative,
        label=f"production target {relative}",
    )
    observed = _git_blob_sha_bytes(payload)
    if observed != expected_blob:
        raise ValueError(
            f"preserved writer expected-current blob changed: {relative}"
        )
    if expected_mode is not None and mode != expected_mode:
        raise ValueError(
            f"preserved writer expected-current mode changed: {relative}"
        )
    return observed, mode


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("preserved writer made no write progress")
        view = view[written:]


def _stage_temp_file(
    *,
    parent: Path,
    payload: bytes,
    mode: int,
) -> Path:
    for _attempt in range(32):
        name = f".pio-preserved-write.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        path = parent / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            continue
        try:
            _write_all(fd, payload)
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
        return path
    raise ValueError("preserved writer could not allocate a private temp file")


def _fsync_directory(parent: Path) -> None:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(parent, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _unlink_temp(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _approval_not_expired(raw: str) -> bool:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        return False
    try:
        expiry = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    return datetime.now(timezone.utc) <= expiry


def _prepare_operation(
    *,
    operation: dict[str, Any],
    source: Path,
    private_bundle: Path,
    backup_root: Path,
    backup_module: Any,
) -> dict[str, Any]:
    relative = _safe_relative_path(operation["path"])
    operation_type = operation["operation"]

    if operation_type == "UPDATE_PRESERVED_FILE":
        source_root = private_bundle
    elif operation_type in {"UPDATE_FILE", "CREATE_FILE"}:
        source_root = source
    else:
        raise ValueError("preserved writer operation type is invalid")

    source_payload, source_mode = _read_regular(
        backup_module=backup_module,
        root=source_root,
        relative=relative,
        label=f"reviewed source {relative}",
    )
    source_blob = _git_blob_sha_bytes(source_payload)
    if source_blob != operation["target_blob"]:
        raise ValueError(f"preserved writer source blob mismatch: {relative}")

    backup_payload: bytes | None
    backup_mode: int | None

    if operation["backup_required"]:
        backup_relative = backup_module._safe_backup_relative_path(
            operation["backup_relative_path"]
        )
        backup_payload, backup_mode = _read_regular(
            backup_module=backup_module,
            root=backup_root,
            relative=backup_relative,
            label=f"rollback backup {relative}",
        )
        if _git_blob_sha_bytes(backup_payload) != operation["backup_git_blob"]:
            raise ValueError(f"preserved writer backup Git blob mismatch: {relative}")
        if _sha256_bytes(backup_payload) != operation["backup_sha256"]:
            raise ValueError(f"preserved writer backup SHA-256 mismatch: {relative}")
        if len(backup_payload) != operation["backup_size"]:
            raise ValueError(f"preserved writer backup size mismatch: {relative}")
        if backup_mode != operation["backup_mode"]:
            raise ValueError(f"preserved writer backup mode mismatch: {relative}")
        if operation["backup_git_blob"] != operation["expected_current_blob"]:
            raise ValueError(
                f"preserved writer backup/current binding mismatch: {relative}"
            )
        target_mode = backup_mode
    else:
        backup_payload = None
        backup_mode = None
        if operation_type != "CREATE_FILE":
            raise ValueError("preserved writer non-backup operation is not a create")
        if source_mode != CREATE_FILE_MODE:
            raise ValueError(
                f"preserved writer create source mode is not 0644: {relative}"
            )
        target_mode = CREATE_FILE_MODE

    return {
        "operation": operation,
        "relative": relative,
        "source_payload": source_payload,
        "source_mode": source_mode,
        "source_blob": source_blob,
        "backup_payload": backup_payload,
        "backup_mode": backup_mode,
        "target_mode": target_mode,
    }


def _install_operation(
    *,
    prepared: dict[str, Any],
    production: Path,
    backup_module: Any,
) -> dict[str, Any]:
    operation = prepared["operation"]
    relative: Path = prepared["relative"]
    parent = _parent_dir(
        backup_module=backup_module,
        root=production,
        relative=relative,
    )
    target = production / relative
    expected_blob = operation["expected_current_blob"]
    expected_mode = (
        operation["backup_mode"]
        if operation["backup_required"]
        else None
    )

    before_blob, _before_mode = _inspect_expected_state(
        backup_module=backup_module,
        production=production,
        relative=relative,
        expected_blob=expected_blob,
        expected_mode=expected_mode,
    )

    temp: Path | None = None
    target_changed = False
    try:
        temp = _stage_temp_file(
            parent=parent,
            payload=prepared["source_payload"],
            mode=prepared["target_mode"],
        )

        # This is the required immediate per-operation state check. It occurs
        # after target bytes are staged but directly before the target path is
        # changed.
        _inspect_expected_state(
            backup_module=backup_module,
            production=production,
            relative=relative,
            expected_blob=expected_blob,
            expected_mode=expected_mode,
        )

        if operation["operation"] == "CREATE_FILE":
            try:
                os.link(temp, target)
            except FileExistsError as exc:
                raise ValueError(
                    f"preserved writer create target appeared before install: {relative}"
                ) from exc
            target_changed = True
            temp.unlink()
            temp = None
        else:
            os.replace(temp, target)
            target_changed = True
            temp = None

        _fsync_directory(parent)

        after_payload, after_mode = _read_regular(
            backup_module=backup_module,
            root=production,
            relative=relative,
            label=f"post-write target {relative}",
        )
        after_blob = _git_blob_sha_bytes(after_payload)
        if after_blob != operation["target_blob"]:
            raise ValueError(
                f"preserved writer post-write target blob mismatch: {relative}"
            )
        if after_mode != prepared["target_mode"]:
            raise ValueError(
                f"preserved writer post-write target mode mismatch: {relative}"
            )

        return {
            "index": operation["index"],
            "layer": operation["layer"],
            "operation": operation["operation"],
            "path": operation["path"],
            "expected_current_blob": expected_blob,
            "before_blob": before_blob,
            "target_blob": operation["target_blob"],
            "after_blob": after_blob,
            "target_mode": prepared["target_mode"],
            "immediate_expected_state_recheck_passed": True,
            "source_bytes_reverified": True,
            "rollback_material_reverified": bool(
                operation["backup_required"]
            ),
            "write_applied": True,
            "post_write_verified": True,
        }
    except Exception as exc:
        if target_changed:
            try:
                _rollback_operation(
                    prepared=prepared,
                    production=production,
                    backup_module=backup_module,
                )
            except Exception as rollback_exc:
                raise OperationRollbackError(
                    f"current operation rollback incomplete: {relative}"
                ) from rollback_exc
        raise
    finally:
        _unlink_temp(temp)


def _rollback_operation(
    *,
    prepared: dict[str, Any],
    production: Path,
    backup_module: Any,
) -> None:
    operation = prepared["operation"]
    relative: Path = prepared["relative"]
    parent = _parent_dir(
        backup_module=backup_module,
        root=production,
        relative=relative,
    )
    target = production / relative

    if operation["operation"] == "CREATE_FILE":
        current, _mode = _inspect_expected_state(
            backup_module=backup_module,
            production=production,
            relative=relative,
            expected_blob=operation["target_blob"],
            expected_mode=prepared["target_mode"],
        )
        if current != operation["target_blob"]:
            raise ValueError(
                f"preserved writer rollback create target drifted: {relative}"
            )
        target.unlink()
        _fsync_directory(parent)
        state, absent = backup_module._inspect_absent(production, relative)
        if state != "ABSENT_AS_EXPECTED" or not absent:
            raise ValueError(
                f"preserved writer rollback could not remove create: {relative}"
            )
        return

    backup_payload = prepared["backup_payload"]
    backup_mode = prepared["backup_mode"]
    if backup_payload is None or backup_mode is None:
        raise ValueError("preserved writer rollback backup is unavailable")

    _inspect_expected_state(
        backup_module=backup_module,
        production=production,
        relative=relative,
        expected_blob=operation["target_blob"],
        expected_mode=prepared["target_mode"],
    )

    temp: Path | None = None
    try:
        temp = _stage_temp_file(
            parent=parent,
            payload=backup_payload,
            mode=backup_mode,
        )
        _inspect_expected_state(
            backup_module=backup_module,
            production=production,
            relative=relative,
            expected_blob=operation["target_blob"],
            expected_mode=prepared["target_mode"],
        )
        os.replace(temp, target)
        temp = None
        _fsync_directory(parent)

        restored_payload, restored_mode = _read_regular(
            backup_module=backup_module,
            root=production,
            relative=relative,
            label=f"rolled-back target {relative}",
        )
        if _git_blob_sha_bytes(restored_payload) != operation[
            "expected_current_blob"
        ]:
            raise ValueError(
                f"preserved writer rollback blob mismatch: {relative}"
            )
        if restored_mode != backup_mode:
            raise ValueError(
                f"preserved writer rollback mode mismatch: {relative}"
            )
    finally:
        _unlink_temp(temp)


def validate_mutation_receipt(receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("preserved mutation receipt must be a JSON object")
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError("preserved mutation receipt fields do not match schema")
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preserved mutation receipt format")
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preserved mutation receipt artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("preserved mutation receipt source lineage mismatch")
    if not _is_hex_digest(receipt.get("execution_precheck_sha256"), 64):
        raise ValueError("preserved mutation receipt precheck digest is invalid")
    if not _is_hex_digest(receipt.get("receipt_sha256"), 64):
        raise ValueError("preserved mutation receipt digest is invalid")

    repository = receipt.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("preserved mutation receipt repository is invalid")
    for field in ("approver_principal", "approval_id", "approval_expires_at"):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(f"preserved mutation receipt {field} is invalid")

    results = receipt.get("operation_results")
    if not isinstance(results, list) or not results:
        raise ValueError("preserved mutation receipt results must be non-empty")
    if receipt.get("operation_count") != len(results):
        raise ValueError("preserved mutation receipt operation count mismatch")

    seen: set[str] = set()
    for expected_index, item in enumerate(results):
        if not isinstance(item, dict) or set(item) != set(RESULT_FIELDS):
            raise ValueError("preserved mutation receipt result schema mismatch")
        if item.get("index") != expected_index:
            raise ValueError("preserved mutation receipt indexes are not contiguous")
        path = item.get("path")
        _safe_relative_path(path)
        if path in seen:
            raise ValueError("preserved mutation receipt path is duplicated")
        seen.add(path)

        if item.get("operation") not in {
            "UPDATE_PRESERVED_FILE",
            "UPDATE_FILE",
            "CREATE_FILE",
        }:
            raise ValueError("preserved mutation receipt operation is invalid")
        if not _is_hex_digest(item.get("target_blob"), 40):
            raise ValueError("preserved mutation receipt target blob is invalid")
        if item.get("after_blob") != item.get("target_blob"):
            raise ValueError("preserved mutation receipt after blob mismatch")

        expected_current = item.get("expected_current_blob")
        before_blob = item.get("before_blob")
        if item["operation"] == "CREATE_FILE":
            if expected_current is not None or before_blob is not None:
                raise ValueError("preserved mutation receipt create state is invalid")
        else:
            if not _is_hex_digest(expected_current, 40):
                raise ValueError(
                    "preserved mutation receipt update expected blob is invalid"
                )
            if before_blob != expected_current:
                raise ValueError("preserved mutation receipt before blob mismatch")

        mode = item.get("target_mode")
        if (
            not isinstance(mode, int)
            or isinstance(mode, bool)
            or mode < 0
            or mode > 0o7777
        ):
            raise ValueError("preserved mutation receipt target mode is invalid")
        for field in (
            "immediate_expected_state_recheck_passed",
            "source_bytes_reverified",
            "write_applied",
            "post_write_verified",
        ):
            if item.get(field) is not True:
                raise ValueError(
                    f"preserved mutation receipt requires {field}=true"
                )
        rollback_reverified = item.get("rollback_material_reverified")
        if not isinstance(rollback_reverified, bool):
            raise ValueError(
                "preserved mutation receipt rollback-material flag is invalid"
            )
        expected_rollback_reverified = item["operation"] != "CREATE_FILE"
        if rollback_reverified is not expected_rollback_reverified:
            raise ValueError(
                "preserved mutation receipt rollback-material semantics mismatch"
            )

    for field in (
        "all_operations_applied",
        "preserved_file_mutation_authorization_present",
        "file_mutation_completed",
        "requires_post_mutation_validation",
    ):
        if receipt.get(field) is not True:
            raise ValueError(f"preserved mutation receipt requires {field}=true")

    for field in (
        "rollback_performed",
        "production_repository_git_mutated",
        "service_restart_performed",
        "detector_cursor_moved",
        "paper_timer_enabled",
        "transaction_signed",
        "transaction_submitted",
        "live_capital_deployed",
    ):
        if receipt.get(field) is not False:
            raise ValueError(f"preserved mutation receipt requires {field}=false")

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if receipt["receipt_sha256"] != expected:
        raise ValueError("preserved mutation receipt digest mismatch")


def apply_preserved_file_mutations(
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
    execution_precheck_path: str | Path,
    expected_execution_precheck_sha256: str,
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
    if not _is_hex_digest(expected_execution_precheck_sha256, 64):
        raise ValueError("expected execution-precheck digest is invalid")

    precheck_module, mutation_review_module, backup_module = (
        _load_reviewed_modules(source)
    )

    with _writer_lock():
        saved_precheck = _load_json(
            execution_precheck_path,
            label="saved execution precheck",
        )
        precheck_module.validate_execution_precheck(saved_precheck)
        if saved_precheck.get("execution_precheck_ready") is not True:
            raise ValueError("saved execution precheck is not ready")
        if saved_precheck.get("execution_ready") is not False:
            raise ValueError("saved execution precheck must remain non-executable")
        if saved_precheck.get("requires_immediate_per_operation_recheck") is not True:
            raise ValueError(
                "saved execution precheck lacks immediate-operation recheck"
            )
        if (
            saved_precheck["execution_precheck_sha256"]
            != expected_execution_precheck_sha256
        ):
            raise ValueError("execution-precheck digest pin mismatch")
        if Path(str(saved_precheck["production_repository"])).resolve() != production:
            raise ValueError("execution-precheck repository binding mismatch")

        fresh_precheck = precheck_module.build_execution_precheck(
            repository=production,
            source_tree=source,
            private_bundle_report_path=private_bundle_report_path,
            handoff_path=handoff_path,
            plan_path=plan_path,
            gate_path=gate_path,
            mutation_review_path=mutation_review_path,
            evidence_package_path=evidence_package_path,
            backup_capture_path=backup_capture_path,
            authorization_review_path=authorization_review_path,
            authorization_request_path=authorization_request_path,
            signed_authorization_verification_path=(
                signed_authorization_verification_path
            ),
            signed_payload_path=signed_payload_path,
            signature_path=signature_path,
            allowed_signers_path=allowed_signers_path,
            expected_allowed_signers_sha256=expected_allowed_signers_sha256,
        )
        precheck_module.validate_execution_precheck(fresh_precheck)
        if fresh_precheck != saved_precheck:
            raise ValueError(
                "fresh execution precheck differs from saved execution precheck"
            )

        mutation_review = _load_json(
            mutation_review_path,
            label="preserved mutation review",
        )
        mutation_review_module.validate_preserved_mutation_review(mutation_review)
        if mutation_review.get("review_ready") is not True:
            raise ValueError("preserved mutation review is not ready")

        bundle_report = _load_json(
            private_bundle_report_path,
            label="private preserved-source bundle report",
        )
        _gate_module, plan_module, _handoff_module = (
            mutation_review_module._load_reviewed_modules(source)
        )
        private_bundle = mutation_review_module._private_bundle_dir(
            source=source,
            plan_module=plan_module,
            bundle_report=bundle_report,
            expected_bundle_sha256=mutation_review["private_bundle_sha256"],
        )

        backup_root = Path(str(saved_precheck["backup_dir"]))
        if backup_root.is_symlink():
            raise ValueError("rollback backup directory must not be a symlink")
        backup_root = backup_root.resolve(strict=True)
        var_tmp = Path("/var/tmp").resolve()
        if var_tmp not in backup_root.parents or not backup_root.is_dir():
            raise ValueError("rollback backup directory is outside reviewed scope")

        operations = fresh_precheck["operation_prechecks"]
        prepared = [
            _prepare_operation(
                operation=operation,
                source=source,
                private_bundle=private_bundle,
                backup_root=backup_root,
                backup_module=backup_module,
            )
            for operation in operations
        ]

        if not _approval_not_expired(fresh_precheck["approval_expires_at"]):
            raise ValueError("human authorization expired before mutation start")

        applied: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        try:
            for item in prepared:
                if not _approval_not_expired(
                    fresh_precheck["approval_expires_at"]
                ):
                    raise ValueError(
                        "human authorization expired during file mutation batch"
                    )
                result = _install_operation(
                    prepared=item,
                    production=production,
                    backup_module=backup_module,
                )
                applied.append(item)
                results.append(result)
        except Exception as exc:
            current_rollback_incomplete = isinstance(
                exc,
                OperationRollbackError,
            )
            rollback_errors: list[str] = []
            for item in reversed(applied):
                try:
                    _rollback_operation(
                        prepared=item,
                        production=production,
                        backup_module=backup_module,
                    )
                except Exception as rollback_exc:
                    rollback_errors.append(
                        f"{item['operation']['path']}: {rollback_exc}"
                    )
            if current_rollback_incomplete or rollback_errors:
                details = "; ".join(rollback_errors) if rollback_errors else (
                    "the current operation could not be restored"
                )
                raise RuntimeError(
                    "preserved mutation failed and rollback was incomplete: "
                    f"{details}"
                ) from exc
            raise RuntimeError(
                "preserved mutation failed; all applied operations were rolled back"
            ) from exc

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
            "execution_precheck_sha256": fresh_precheck[
                "execution_precheck_sha256"
            ],
            "production_repository": str(production),
            "approver_principal": fresh_precheck["approver_principal"],
            "approval_id": fresh_precheck["approval_id"],
            "approval_expires_at": fresh_precheck["approval_expires_at"],
            "operation_results": results,
            "operation_count": len(results),
            "all_operations_applied": len(results) == len(operations),
            "preserved_file_mutation_authorization_present": True,
            "file_mutation_completed": len(results) == len(operations),
            "requires_post_mutation_validation": True,
            "rollback_performed": False,
            "production_repository_git_mutated": False,
            "service_restart_performed": False,
            "detector_cursor_moved": False,
            "paper_timer_enabled": False,
            "transaction_signed": False,
            "transaction_submitted": False,
            "live_capital_deployed": False,
        }
        receipt = {
            **identity,
            "receipt_sha256": _sha256_bytes(_canonical_bytes(identity)),
        }
        validate_mutation_receipt(receipt)
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply only the exact preserved file-mutation scope sealed by the "
            "final execution precheck. The writer re-runs the complete precheck "
            "under an exclusive lock, preloads and re-verifies all reviewed source "
            "and rollback material, immediately rechecks each target before its "
            "own write, verifies each result, and rolls earlier writes back if a "
            "later operation fails. It performs no Git, service, cursor, timer, "
            "transaction, or live-capital action."
        )
    )
    parser.add_argument(
        "--apply-exact-preserved-file-mutations",
        action="store_true",
        required=True,
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
    parser.add_argument("--execution-precheck", required=True)
    parser.add_argument("--expected-execution-precheck-sha256", required=True)
    args = parser.parse_args()

    if not args.apply_exact_preserved_file_mutations:
        raise SystemExit(2)

    receipt = apply_preserved_file_mutations(
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
        signed_authorization_verification_path=(
            args.signed_authorization_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
        execution_precheck_path=args.execution_precheck,
        expected_execution_precheck_sha256=(
            args.expected_execution_precheck_sha256
        ),
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
