#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
EXECUTOR_TOOL = TOOLS_DIR / "execute_phase2_isolated_mutation_preview.py"
EXECUTOR_TOOL_RELATIVE = (
    "deploy/tools/execute_phase2_isolated_mutation_preview.py"
)
_MAX_TOOL_BYTES = 4 * 1024 * 1024
_PROTECTED_ROOTS = (
    Path("/opt/pio"),
    Path("/opt/pio/data"),
    Path("/opt/pio-phase2-runtime"),
    Path("/etc/pio"),
    Path("/etc/systemd/system"),
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _assert_tool_path_stable(
    path: Path,
    opened: os.stat_result,
    *,
    label: str,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} path changed after load") from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} path changed after load")


def _capture_tool(
    path: Path,
    *,
    label: str,
) -> tuple[Path, bytes, os.stat_result]:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} is missing") from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        raise ValueError(f"{label} cannot be opened safely") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if before.st_size <= 0 or before.st_size > _MAX_TOOL_BYTES:
            raise ValueError(f"{label} size is invalid")

        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)

        after = os.fstat(fd)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or not stat.S_ISREG(after.st_mode)
        ):
            raise ValueError(f"{label} changed while reading")
    finally:
        os.close(fd)

    if len(encoded) != before.st_size:
        raise ValueError(f"{label} changed while reading")
    _assert_tool_path_stable(resolved, before, label=label)
    return resolved, encoded, before


def _load_captured(
    path: Path,
    name: str,
) -> tuple[Any, Path, bytes, os.stat_result]:
    label = "reviewed mutation execution tool"
    resolved, encoded, opened = _capture_tool(path, label=label)
    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {resolved}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        code = compile(encoded, str(resolved), "exec")
        exec(code, module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    _assert_tool_path_stable(resolved, opened, label=label)
    return module, resolved, encoded, opened


(
    EXEC,
    _EXECUTOR_TOOL_PATH_AT_LOAD,
    _EXECUTOR_TOOL_BYTES_AT_LOAD,
    _EXECUTOR_TOOL_STAT_AT_LOAD,
) = _load_captured(
    EXECUTOR_TOOL,
    "phase2_mutation_receipt_executor",
)
_EXECUTOR_TOOL_SHA256_AT_LOAD = hashlib.sha256(
    _EXECUTOR_TOOL_BYTES_AT_LOAD
).hexdigest()


@dataclass(frozen=True)
class Phase2MutationReceiptRun:
    preview_path: str
    receipt_path: str
    execution_requested: bool
    preview_ready: bool
    pending_receipt_written: bool
    final_receipt_written: bool
    receipt_status: str | None
    mutation_launched: bool
    mutation_completed: bool
    mutation_succeeded: bool
    exit_code: int | None
    failure_category: str | None
    receipt_sha256: str | None
    execution_report_sha256: str | None
    mutation_outcome_summary: dict[str, Any] | None
    shell_used: bool
    raw_stderr_exposed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _receipt_path(
    *,
    preview_path: str | Path,
    receipt_path: str | Path | None,
) -> Path:
    if receipt_path is None:
        preview = Path(preview_path).expanduser().resolve(strict=False)
        raw = preview.with_name(f"{preview.name}.execution.json")
    else:
        raw = Path(receipt_path).expanduser()

    if raw.is_symlink():
        raise ValueError("mutation execution receipt must not be a symlink")
    path = raw.resolve(strict=False)
    if any(_under(path, root.resolve()) for root in _PROTECTED_ROOTS):
        raise ValueError("mutation execution receipt is inside a protected production path")
    if path.exists():
        raise ValueError("mutation execution receipt already exists")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("mutation execution receipt parent must be an existing directory")
    return path


def _run_git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )


def _executor_source_identity() -> tuple[str, str]:
    raw = Path(EXECUTOR_TOOL).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed mutation execution tool is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            "reviewed mutation execution tool is missing or symlinked"
        ) from exc
    if resolved != _EXECUTOR_TOOL_PATH_AT_LOAD:
        raise ValueError("mutation execution tool path changed after module load")
    _assert_tool_path_stable(
        _EXECUTOR_TOOL_PATH_AT_LOAD,
        _EXECUTOR_TOOL_STAT_AT_LOAD,
        label="reviewed mutation execution tool",
    )

    head = _run_git("rev-parse", "HEAD")
    if head.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = head.stdout.decode("utf-8").strip()
    if not commit:
        raise ValueError("reviewed source commit is invalid")

    historical = _run_git("show", f"{commit}:{EXECUTOR_TOOL_RELATIVE}")
    if historical.returncode != 0:
        raise ValueError(
            "mutation execution tool is not present at reviewed source commit"
        )
    if historical.stdout != _EXECUTOR_TOOL_BYTES_AT_LOAD:
        raise ValueError(
            "mutation execution tool bytes do not match reviewed source commit"
        )

    _assert_tool_path_stable(
        _EXECUTOR_TOOL_PATH_AT_LOAD,
        _EXECUTOR_TOOL_STAT_AT_LOAD,
        label="reviewed mutation execution tool",
    )
    return commit, _EXECUTOR_TOOL_SHA256_AT_LOAD


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_new_or_replace(
    path: Path,
    payload: dict[str, Any],
    *,
    allow_replace: bool,
) -> str:
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    payload_sha256 = hashlib.sha256(encoded).hexdigest()

    if path.is_symlink():
        raise ValueError("mutation execution receipt must not be a symlink")
    if path.exists() and not allow_replace:
        raise ValueError("mutation execution receipt already exists")
    if path.exists() and not path.is_file():
        raise ValueError("mutation execution receipt is no longer a regular file")

    fd: int | None = None
    temp_path: Path | None = None
    temp_stat: os.stat_result | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        temp_path = Path(temp_name)
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temp_stat = os.fstat(handle.fileno())

        assert temp_stat is not None
        if (
            not stat.S_ISREG(temp_stat.st_mode)
            or temp_stat.st_size != len(encoded)
            or stat.S_IMODE(temp_stat.st_mode) != 0o600
        ):
            raise ValueError(
                "mutation execution receipt temp payload changed before publish"
            )

        if path.is_symlink():
            raise ValueError("mutation execution receipt became a symlink")
        if path.exists() and not path.is_file():
            raise ValueError("mutation execution receipt changed type during write")

        if allow_replace:
            os.replace(temp_path, path)
            temp_path = None
        else:
            try:
                os.link(
                    temp_path,
                    path,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise ValueError(
                    "mutation execution receipt appeared before publish"
                ) from exc

            published = os.stat(path, follow_symlinks=False)
            if (
                published.st_dev != temp_stat.st_dev
                or published.st_ino != temp_stat.st_ino
            ):
                raise ValueError(
                    "mutation execution receipt publication identity mismatch"
                )
            temp_path.unlink()
            temp_path = None

        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

        current = os.stat(path, follow_symlinks=False)
        if (
            current.st_dev != temp_stat.st_dev
            or current.st_ino != temp_stat.st_ino
            or not stat.S_ISREG(current.st_mode)
            or current.st_size != len(encoded)
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise ValueError(
                "mutation execution receipt path changed after publish"
            )
    finally:
        if fd is not None:
            os.close(fd)
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    return payload_sha256


def _identity_from_ready(ready: Any) -> dict[str, Any]:
    return {
        "preview_path": str(ready.preview_path),
        "preview_sha256": str(ready.preview_sha256),
        "reviewed_source_commit": str(ready.reviewed_source_commit),
        "deploy_surface_sha256": str(ready.deploy_surface_sha256),
        "deploy_surface_files": int(ready.deploy_surface_files),
        "mutation_fingerprint": str(ready.mutation_fingerprint),
        "mutation_tool_sha256": str(ready.mutation_tool_sha256),
        "mutation_argv_sha256": _canonical_sha256(list(ready.mutation_argv)),
    }


def _execution_failure_evidence(execution: Any) -> dict[str, Any] | None:
    category = getattr(execution, "failure_category", None)
    exit_code = getattr(execution, "exit_code", None)
    if (
        not isinstance(category, str)
        or not category
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code == 0
        or not bool(getattr(execution, "result_json_valid", False))
        or not bool(getattr(execution, "result_secret_safe", False))
    ):
        return None

    result = getattr(execution, "result", None)
    if not isinstance(result, dict):
        return None

    evidence: dict[str, Any] = {}
    for key in (
        "applied",
        "failure_step",
        "rollback_performed",
        "rollback_succeeded",
        "files_updated",
        "ready",
        "upgrade_needed",
        "installer_needed",
    ):
        if key not in result:
            continue
        value = result[key]
        if key == "failure_step":
            if value is not None and (
                not isinstance(value, str) or not value
            ):
                continue
        elif key == "files_updated":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                continue
        elif not isinstance(value, bool):
            continue
        evidence[key] = value

    return evidence or None


_SUCCESS_OUTCOME_BOOL_FIELDS = (
    "applied",
    "ready",
    "upgrade_needed",
    "installer_needed",
    "daemon_reload_performed",
    "service_control_performed",
    "rpc_called",
)
_SUCCESS_OUTCOME_INT_FIELDS = ("files_updated",)


def _successful_mutation_outcome_summary(
    execution: Any,
) -> dict[str, Any] | None:
    exit_code = getattr(execution, "exit_code", None)
    if (
        not bool(getattr(execution, "mutation_executed", False))
        or not bool(getattr(execution, "result_json_valid", False))
        or not bool(getattr(execution, "result_secret_safe", False))
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code != 0
        or getattr(execution, "failure_category", None) is not None
    ):
        return None

    result = getattr(execution, "result", None)
    if not isinstance(result, dict):
        return None

    summary: dict[str, Any] = {}
    for key in _SUCCESS_OUTCOME_BOOL_FIELDS:
        value = result.get(key)
        if isinstance(value, bool):
            summary[key] = value

    for key in _SUCCESS_OUTCOME_INT_FIELDS:
        value = result.get(key)
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        ):
            summary[key] = value

    return summary or None


def _pending_payload(
    *,
    ready: Any,
    started_at: str,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "receipt_type": "PHASE2_REVIEWED_MUTATION_EXECUTION_V1",
        "status": "PENDING",
        "started_at": started_at,
        "completed_at": None,
        **_identity_from_ready(ready),
        "mutation_launched": False,
        "mutation_completed": False,
        "mutation_succeeded": False,
        "outcome_known": False,
        "exit_code": None,
        "failure_category": None,
        "execution_report_sha256": None,
        "execution_failure_evidence": None,
        "mutation_outcome_summary": None,
        "raw_stderr_persisted": False,
    }


def _final_payload(
    *,
    pending: dict[str, Any],
    status: str,
    completed_at: str,
    mutation_launched: bool,
    execution: Any | None,
    failure_category: str | None,
) -> dict[str, Any]:
    payload = dict(pending)
    payload["status"] = status
    payload["completed_at"] = completed_at
    payload["mutation_launched"] = mutation_launched
    payload["failure_category"] = failure_category

    if execution is None:
        payload["mutation_completed"] = False
        payload["mutation_succeeded"] = False
        payload["outcome_known"] = status == "ABORTED_BEFORE_LAUNCH"
        payload["exit_code"] = None
        payload["execution_report_sha256"] = None
        return payload

    record = execution.to_record()
    payload["mutation_completed"] = True
    payload["outcome_known"] = True
    payload["exit_code"] = execution.exit_code
    payload["mutation_succeeded"] = bool(
        execution.mutation_executed
        and execution.result_json_valid
        and execution.exit_code == 0
        and execution.failure_category is None
    )
    payload["execution_report_sha256"] = _canonical_sha256(record)
    payload["result_json_valid"] = bool(execution.result_json_valid)
    payload["result_secret_safe"] = bool(execution.result_secret_safe)
    payload["execution_failure_category"] = execution.failure_category
    payload["execution_failure_evidence"] = _execution_failure_evidence(
        execution
    )
    payload["mutation_outcome_summary"] = (
        _successful_mutation_outcome_summary(execution)
    )
    return payload


def run_mutation_with_receipt(
    *,
    preview_path: str | Path,
    expected_preview_sha256: str,
    execution_receipt_path: str | Path | None = None,
    execute: bool = False,
    timeout_seconds: int = 1800,
    runner: Runner = subprocess.run,
    now: Callable[[], str] = _utc_now,
    **handoff_kwargs: Any,
) -> Phase2MutationReceiptRun:
    source_before = _executor_source_identity()
    ready = EXEC.execute_fresh_mutation_preview(
        preview_path=preview_path,
        expected_preview_sha256=expected_preview_sha256,
        execute=False,
        timeout_seconds=timeout_seconds,
        **handoff_kwargs,
    )
    source_after_ready = _executor_source_identity()
    if source_after_ready != source_before:
        raise ValueError("reviewed mutation execution source changed during readiness")
    if ready.execution_requested or ready.mutation_executed or not ready.preview_current:
        raise ValueError("mutation execution readiness crossed the non-mutating boundary")

    receipt = _receipt_path(
        preview_path=preview_path,
        receipt_path=execution_receipt_path,
    )

    if not execute:
        return Phase2MutationReceiptRun(
            preview_path=str(ready.preview_path),
            receipt_path=str(receipt),
            execution_requested=False,
            preview_ready=True,
            pending_receipt_written=False,
            final_receipt_written=False,
            receipt_status=None,
            mutation_launched=False,
            mutation_completed=False,
            mutation_succeeded=False,
            exit_code=None,
            failure_category=None,
            receipt_sha256=None,
            execution_report_sha256=None,
            mutation_outcome_summary=None,
            shell_used=False,
            raw_stderr_exposed=False,
        )

    started_at = now()
    pending = _pending_payload(ready=ready, started_at=started_at)
    _atomic_write_new_or_replace(receipt, pending, allow_replace=False)
    pending_written = True
    launched = False

    def tracked_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal launched
        launched = True
        return runner(command, **kwargs)

    execution = None
    try:
        source_before_execute = _executor_source_identity()
        if source_before_execute != source_before:
            raise ValueError(
                "reviewed mutation execution source changed before launch"
            )
        execution = EXEC.execute_fresh_mutation_preview(
            preview_path=preview_path,
            expected_preview_sha256=expected_preview_sha256,
            execute=True,
            timeout_seconds=timeout_seconds,
            runner=tracked_runner,
            **handoff_kwargs,
        )
        source_after_execute = _executor_source_identity()
        if source_after_execute != source_before:
            raise ValueError(
                "reviewed mutation execution source changed during execution"
            )
    except Exception:
        status = (
            "OUTCOME_UNKNOWN_AFTER_LAUNCH"
            if launched
            else "ABORTED_BEFORE_LAUNCH"
        )
        category = (
            "MUTATION_RUNNER_FAILED_OUTCOME_UNKNOWN"
            if launched
            else "EXECUTION_GUARD_FAILED_BEFORE_LAUNCH"
        )
        final = _final_payload(
            pending=pending,
            status=status,
            completed_at=now(),
            mutation_launched=launched,
            execution=None,
            failure_category=category,
        )
        receipt_sha = _atomic_write_new_or_replace(
            receipt,
            final,
            allow_replace=True,
        )
        return Phase2MutationReceiptRun(
            preview_path=str(ready.preview_path),
            receipt_path=str(receipt),
            execution_requested=True,
            preview_ready=True,
            pending_receipt_written=pending_written,
            final_receipt_written=True,
            receipt_status=status,
            mutation_launched=launched,
            mutation_completed=False,
            mutation_succeeded=False,
            exit_code=None,
            failure_category=category,
            receipt_sha256=receipt_sha,
            execution_report_sha256=None,
            mutation_outcome_summary=None,
            shell_used=False,
            raw_stderr_exposed=False,
        )

    final = _final_payload(
        pending=pending,
        status="COMPLETED",
        completed_at=now(),
        mutation_launched=launched,
        execution=execution,
        failure_category=execution.failure_category,
    )
    receipt_sha = _atomic_write_new_or_replace(
        receipt,
        final,
        allow_replace=True,
    )
    execution_sha = final["execution_report_sha256"]

    return Phase2MutationReceiptRun(
        preview_path=str(ready.preview_path),
        receipt_path=str(receipt),
        execution_requested=True,
        preview_ready=True,
        pending_receipt_written=pending_written,
        final_receipt_written=True,
        receipt_status="COMPLETED",
        mutation_launched=launched,
        mutation_completed=True,
        mutation_succeeded=bool(final["mutation_succeeded"]),
        exit_code=execution.exit_code,
        failure_category=execution.failure_category,
        receipt_sha256=receipt_sha,
        execution_report_sha256=execution_sha,
        mutation_outcome_summary=final["mutation_outcome_summary"],
        shell_used=False,
        raw_stderr_exposed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute a fresh reviewed Phase-2 mutation with a private two-phase "
            "PENDING/COMPLETED audit receipt."
        )
    )
    parser.add_argument("--preview", required=True)
    parser.add_argument("--expected-preview-sha256", required=True)
    parser.add_argument("--execution-receipt")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument(
        "--receipt-path",
        default="/opt/pio/data/phase2-isolated-smoke-receipt.json",
    )
    parser.add_argument("--max-receipt-age-seconds", type=int, default=1800)
    parser.add_argument("--source-tree")
    parser.add_argument(
        "--repository-url",
        default=EXEC.FRESH.RENDER.RUNNER.RENDER.HANDOFF.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()

    report = run_mutation_with_receipt(
        preview_path=args.preview,
        expected_preview_sha256=args.expected_preview_sha256,
        execution_receipt_path=args.execution_receipt,
        execute=args.execute,
        timeout_seconds=args.timeout_seconds,
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt_path,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
        source_tree=args.source_tree,
        repository_url=args.repository_url,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.execution_requested and not report.mutation_succeeded:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
