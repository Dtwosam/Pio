#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
FRESHNESS_TOOL = TOOLS_DIR / "check_phase2_isolated_mutation_freshness.py"
FRESHNESS_TOOL_RELATIVE = (
    "deploy/tools/check_phase2_isolated_mutation_freshness.py"
)
_MAX_TOOL_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024
_MAX_MUTATION_TOOL_BYTES = 4 * 1024 * 1024
_MUTATION_FLAGS = frozenset({"--apply", "--prepare"})
_SENSITIVE_ENV_KEYS = frozenset(
    {
        "SOLANA_RPC_URL",
        "SOLANA_WS_URL",
        "JUPITER_API_KEY",
        "HELIUS_API_KEY",
    }
)
_PROTECTED_PREVIEW_ROOTS = (
    Path("/opt/pio"),
    Path("/opt/pio/data"),
    Path("/opt/pio-phase2-runtime"),
    Path("/etc/pio"),
    Path("/etc/systemd/system"),
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _assert_file_path_stable(
    path: Path,
    opened: os.stat_result,
    *,
    label: str,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} path changed after capture") from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} path changed after capture")


def _capture_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    required_mode: int | None = None,
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
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise ValueError(f"{label} size is invalid")
        if (
            required_mode is not None
            and stat.S_IMODE(before.st_mode) != required_mode
        ):
            raise ValueError(
                f"{label} permissions must be {required_mode:04o}"
            )

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
    _assert_file_path_stable(resolved, before, label=label)
    return resolved, encoded, before


def _load_captured_freshness(
    path: Path,
    name: str,
) -> tuple[Any, Path, bytes, os.stat_result]:
    label = "reviewed mutation freshness tool"
    resolved, encoded, opened = _capture_regular_file(
        path,
        label=label,
        max_bytes=_MAX_TOOL_BYTES,
    )
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
    _assert_file_path_stable(resolved, opened, label=label)
    return module, resolved, encoded, opened


(
    FRESH,
    _FRESHNESS_TOOL_PATH_AT_LOAD,
    _FRESHNESS_TOOL_BYTES_AT_LOAD,
    _FRESHNESS_TOOL_STAT_AT_LOAD,
) = _load_captured_freshness(
    FRESHNESS_TOOL,
    "phase2_fresh_mutation_executor_guard",
)
_FRESHNESS_TOOL_SHA256_AT_LOAD = hashlib.sha256(
    _FRESHNESS_TOOL_BYTES_AT_LOAD
).hexdigest()


@dataclass(frozen=True)
class Phase2FreshMutationExecution:
    preview_path: str
    preview_sha256: str
    expected_preview_sha256: str
    preview_sha256_matches: bool
    freshness_status: str
    preview_current: bool
    reviewed_source_commit: str
    deploy_surface_sha256: str
    deploy_surface_files: int
    mutation_fingerprint: str
    mutation_tool_sha256: str
    mutation_argv: tuple[str, ...]
    execution_requested: bool
    mutation_executed: bool
    exit_code: int | None
    result_json_valid: bool
    result_secret_safe: bool
    result: dict[str, Any] | list[Any] | None
    failure_category: str | None
    preview_unchanged_before_execution: bool
    shell_used: bool
    raw_stderr_exposed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _capture_private_preview(
    value: str | Path,
) -> tuple[Path, bytes, os.stat_result]:
    path, encoded, opened = _capture_regular_file(
        Path(value),
        label="mutation preview",
        max_bytes=_MAX_PREVIEW_BYTES,
        required_mode=0o600,
    )
    if any(_under(path, root.resolve()) for root in _PROTECTED_PREVIEW_ROOTS):
        raise ValueError("mutation preview is inside a protected production path")
    return path, encoded, opened


def _freshness_source_identity() -> tuple[str, str]:
    raw = Path(FRESHNESS_TOOL).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed mutation freshness tool is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            "reviewed mutation freshness tool is missing or symlinked"
        ) from exc
    if resolved != _FRESHNESS_TOOL_PATH_AT_LOAD:
        raise ValueError("mutation freshness tool path changed after module load")
    _assert_file_path_stable(
        _FRESHNESS_TOOL_PATH_AT_LOAD,
        _FRESHNESS_TOOL_STAT_AT_LOAD,
        label="reviewed mutation freshness tool",
    )

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if head.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = head.stdout.strip()
    if len(commit) not in {40, 64}:
        raise ValueError("reviewed source commit is invalid")

    historical = subprocess.run(
        ["git", "show", f"{commit}:{FRESHNESS_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "mutation freshness tool is not present at reviewed source commit"
        )
    if historical.stdout != _FRESHNESS_TOOL_BYTES_AT_LOAD:
        raise ValueError(
            "mutation freshness tool bytes do not match reviewed source commit"
        )

    _assert_file_path_stable(
        _FRESHNESS_TOOL_PATH_AT_LOAD,
        _FRESHNESS_TOOL_STAT_AT_LOAD,
        label="reviewed mutation freshness tool",
    )
    return commit, _FRESHNESS_TOOL_SHA256_AT_LOAD


def _expected_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError("expected preview SHA256 must be 64 lowercase hex characters")
    return normalized


def _reviewed_mutation_argv(freshness: Any) -> tuple[str, ...]:
    current = getattr(freshness, "current_preview", None)
    if not isinstance(current, dict):
        raise ValueError("freshness result is missing current preview")

    raw = current.get("mutation_argv")
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        raise ValueError("current mutation argv is invalid")
    argv = tuple(str(value) for value in raw)
    if argv[0] != sys.executable:
        raise ValueError("current mutation interpreter is not this Python executable")
    if argv[-1] not in _MUTATION_FLAGS:
        raise ValueError("current mutation argv is missing reviewed mutation flag")
    if any(value in _MUTATION_FLAGS for value in argv[2:-1]):
        raise ValueError("current mutation argv contains an extra mutation flag")

    tool = Path(argv[1])
    if tool.is_symlink() or not tool.is_file():
        raise ValueError("current mutation tool is missing or symlinked")
    resolved = tool.resolve()
    try:
        resolved.relative_to(TOOLS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("current mutation tool resolves outside reviewed tools") from exc
    if resolved.name not in FRESH.RENDER.RUNNER.REVIEWED_PREFLIGHT_TOOLS:
        raise ValueError("current mutation tool is not allowlisted")
    return argv


def _assert_mutation_tool_path_stable(
    path: Path,
    opened: os.stat_result,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("current mutation tool path changed after capture") from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError("current mutation tool path changed after capture")


def _capture_mutation_tool(
    argv: tuple[str, ...],
) -> tuple[Path, bytes, os.stat_result, int]:
    raw = Path(argv[1])
    if raw.is_symlink():
        raise ValueError("current mutation tool is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError("current mutation tool is missing or symlinked") from exc
    try:
        resolved.relative_to(TOOLS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("current mutation tool resolves outside reviewed tools") from exc
    if resolved.name not in FRESH.RENDER.RUNNER.REVIEWED_PREFLIGHT_TOOLS:
        raise ValueError("current mutation tool is not allowlisted")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        raise ValueError("current mutation tool cannot be opened safely") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("current mutation tool must be a regular file")
        if (
            before.st_size <= 0
            or before.st_size > _MAX_MUTATION_TOOL_BYTES
        ):
            raise ValueError("current mutation tool size is invalid")

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
            raise ValueError("current mutation tool changed while reading")
        if len(encoded) != before.st_size:
            raise ValueError("current mutation tool changed while reading")
        os.lseek(fd, 0, os.SEEK_SET)
        _assert_mutation_tool_path_stable(resolved, before)
        return resolved, encoded, before, fd
    except Exception:
        os.close(fd)
        raise


_EXACT_TOOL_BOOTSTRAP = (
    "import os,sys\n"
    "fd=int(sys.argv[1]); path=sys.argv[2]\n"
    "chunks=[]\n"
    "while True:\n"
    "    chunk=os.read(fd,1024*1024)\n"
    "    if not chunk: break\n"
    "    chunks.append(chunk)\n"
    "source=b''.join(chunks)\n"
    "sys.argv=[path,*sys.argv[3:]]\n"
    "scope={'__name__':'__main__','__file__':path,"
    "'__package__':None,'__cached__':None}\n"
    "exec(compile(source,path,'exec'),scope,scope)\n"
)


def _captured_mutation_command(
    *,
    argv: tuple[str, ...],
    tool_path: Path,
    tool_fd: int,
) -> list[str]:
    return [
        sys.executable,
        "-c",
        _EXACT_TOOL_BOOTSTRAP,
        str(tool_fd),
        str(tool_path),
        *argv[2:],
    ]


def _scrubbed_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _SENSITIVE_ENV_KEYS:
        env.pop(key, None)
    return env


def _contains_sensitive_text(value: str) -> bool:
    folded = value.casefold()
    markers = (
        "solana_rpc_url=",
        "solana_ws_url=",
        "jupiter_api_key=",
        "helius_api_key=",
        "api-key=",
        "x-api-key",
    )
    return any(marker in folded for marker in markers)


def _sanitize_result(value: Any) -> tuple[Any, bool]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        safe = True
        for key, item in value.items():
            key_text = str(key)
            key_folded = key_text.casefold()
            if any(
                marker in key_folded
                for marker in (
                    "api_key",
                    "apikey",
                    "rpc_url",
                    "rpc_endpoint",
                    "authorization",
                    "secret",
                    "credential",
                )
            ):
                result[key_text] = "<redacted>"
                safe = False
                continue
            sanitized, item_safe = _sanitize_result(item)
            result[key_text] = sanitized
            safe = safe and item_safe
        return result, safe
    if isinstance(value, list):
        items = []
        safe = True
        for item in value:
            sanitized, item_safe = _sanitize_result(item)
            items.append(sanitized)
            safe = safe and item_safe
        return items, safe
    if isinstance(value, tuple):
        sanitized, safe = _sanitize_result(list(value))
        return sanitized, safe
    if isinstance(value, str) and _contains_sensitive_text(value):
        return "<redacted>", False
    return value, True


def execute_fresh_mutation_preview(
    *,
    preview_path: str | Path,
    expected_preview_sha256: str,
    execute: bool = False,
    timeout_seconds: int = 1800,
    runner: Runner = subprocess.run,
    **handoff_kwargs: Any,
) -> Phase2FreshMutationExecution:
    if timeout_seconds <= 0 or timeout_seconds > 3600:
        raise ValueError("timeout_seconds must be between 1 and 3600")

    path, preview_bytes, preview_stat = _capture_private_preview(
        preview_path
    )
    expected = _expected_sha256(expected_preview_sha256)
    initial_sha = hashlib.sha256(preview_bytes).hexdigest()
    if initial_sha != expected:
        raise ValueError("mutation preview SHA256 does not match explicit expectation")

    freshness_source_before = _freshness_source_identity()
    freshness = FRESH.check_mutation_preview_freshness(
        preview_path=path,
        timeout_seconds=min(timeout_seconds, 300),
        **handoff_kwargs,
    )
    freshness_source_after = _freshness_source_identity()
    if freshness_source_after != freshness_source_before:
        raise ValueError("reviewed mutation freshness source changed during review")
    _assert_file_path_stable(
        path,
        preview_stat,
        label="mutation preview",
    )
    if not getattr(freshness, "preview_current", False):
        raise ValueError(
            f"mutation preview is not current: {getattr(freshness, 'status', 'UNKNOWN')}"
        )

    argv = _reviewed_mutation_argv(freshness)
    (
        mutation_tool_path,
        mutation_tool_bytes,
        mutation_tool_stat,
        mutation_tool_fd,
    ) = _capture_mutation_tool(argv)
    try:
        current_tool_sha = hashlib.sha256(mutation_tool_bytes).hexdigest()
        expected_tool_sha = getattr(
            freshness,
            "current_mutation_tool_sha256",
            None,
        )
        if (
            not isinstance(expected_tool_sha, str)
            or not _SHA256.fullmatch(expected_tool_sha)
            or current_tool_sha != expected_tool_sha
        ):
            raise ValueError("mutation tool bytes changed after freshness review")

        source_commit, deploy_sha, deploy_files = (
            FRESH.RENDER._deploy_surface_identity()
        )
        if source_commit != getattr(
            freshness,
            "current_reviewed_source_commit",
            None,
        ):
            raise ValueError(
                "reviewed source commit changed after freshness review"
            )
        if deploy_sha != getattr(
            freshness,
            "current_deploy_surface_sha256",
            None,
        ):
            raise ValueError(
                "reviewed deploy surface changed after freshness review"
            )
        if deploy_files != getattr(
            freshness,
            "current_deploy_surface_files",
            None,
        ):
            raise ValueError(
                "reviewed deploy surface file count changed after freshness review"
            )

        final_sha = initial_sha
        _assert_file_path_stable(
            path,
            preview_stat,
            label="mutation preview",
        )
        unchanged = final_sha == initial_sha == expected
        if not unchanged:
            raise ValueError("mutation preview changed during freshness review")

        mutation_fp = getattr(
            freshness,
            "current_mutation_fingerprint",
            None,
        )
        if (
            not isinstance(mutation_fp, str)
            or not _SHA256.fullmatch(mutation_fp)
        ):
            raise ValueError("current mutation fingerprint is invalid")

        _assert_mutation_tool_path_stable(
            mutation_tool_path,
            mutation_tool_stat,
        )
        _assert_file_path_stable(
            path,
            preview_stat,
            label="mutation preview",
        )
        freshness_source_before_launch = _freshness_source_identity()
        if freshness_source_before_launch != freshness_source_before:
            raise ValueError(
                "reviewed mutation freshness source changed before launch"
            )

        if not execute:
            return Phase2FreshMutationExecution(
                preview_path=str(path),
                preview_sha256=final_sha,
                expected_preview_sha256=expected,
                preview_sha256_matches=True,
                freshness_status=str(freshness.status),
                preview_current=True,
                reviewed_source_commit=str(source_commit),
                deploy_surface_sha256=str(deploy_sha),
                deploy_surface_files=int(deploy_files),
                mutation_fingerprint=mutation_fp,
                mutation_tool_sha256=current_tool_sha,
                mutation_argv=argv,
                execution_requested=False,
                mutation_executed=False,
                exit_code=None,
                result_json_valid=False,
                result_secret_safe=True,
                result=None,
                failure_category=None,
                preview_unchanged_before_execution=True,
                shell_used=False,
                raw_stderr_exposed=False,
            )

        command = _captured_mutation_command(
            argv=argv,
            tool_path=mutation_tool_path,
            tool_fd=mutation_tool_fd,
        )
        completed = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            env=_scrubbed_env(),
            pass_fds=(mutation_tool_fd,),
        )
    finally:
        os.close(mutation_tool_fd)

    parsed: dict[str, Any] | list[Any] | None = None
    json_valid = False
    result_secret_safe = True
    try:
        candidate = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        candidate = None
    if isinstance(candidate, (dict, list)):
        parsed, result_secret_safe = _sanitize_result(candidate)
        json_valid = True

    if not json_valid:
        failure_category = "INVALID_MUTATION_JSON"
    elif completed.returncode != 0:
        failure_category = "MUTATION_NONZERO_EXIT"
    else:
        failure_category = None

    return Phase2FreshMutationExecution(
        preview_path=str(path),
        preview_sha256=final_sha,
        expected_preview_sha256=expected,
        preview_sha256_matches=True,
        freshness_status=str(freshness.status),
        preview_current=True,
        reviewed_source_commit=str(source_commit),
        deploy_surface_sha256=str(deploy_sha),
        deploy_surface_files=int(deploy_files),
        mutation_fingerprint=mutation_fp,
        mutation_tool_sha256=current_tool_sha,
        mutation_argv=argv,
        execution_requested=True,
        mutation_executed=True,
        exit_code=int(completed.returncode),
        result_json_valid=json_valid,
        result_secret_safe=result_secret_safe,
        result=parsed,
        failure_category=failure_category,
        preview_unchanged_before_execution=True,
        shell_used=False,
        raw_stderr_exposed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshness-check and optionally execute exactly one saved reviewed "
            "Phase-2 mutation preview. No shell is used."
        )
    )
    parser.add_argument("--preview", required=True)
    parser.add_argument("--expected-preview-sha256", required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="execute the exact current reviewed mutation after all checks pass",
    )
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
        default=FRESH.RENDER.RUNNER.RENDER.HANDOFF.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()

    report = execute_fresh_mutation_preview(
        preview_path=args.preview,
        expected_preview_sha256=args.expected_preview_sha256,
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
    if report.execution_requested and (
        not report.result_json_valid or report.exit_code != 0
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
