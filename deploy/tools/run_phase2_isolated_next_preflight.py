#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
RENDER_TOOL = TOOLS_DIR / "render_phase2_isolated_next_command.py"
RENDER_TOOL_RELATIVE = "deploy/tools/render_phase2_isolated_next_command.py"
_MAX_TOOL_BYTES = 4 * 1024 * 1024

REVIEWED_PREFLIGHT_TOOLS = frozenset(
    {
        "bootstrap_phase2_isolated_source.py",
        "prepare_phase2_isolated_runtime.py",
        "stage_phase2_isolated_runtime.py",
        "install_phase2_isolated_systemd_units.py",
        "upgrade_phase2_isolated_systemd_units.py",
        "activate_phase2_isolated_detector.py",
        "run_phase2_isolated_smoke.py",
        "activate_phase2_isolated_timer.py",
        "autopause_phase2_isolated_timer.py",
        "check_phase2_isolated_operator_status.py",
        "check_phase2_isolated_activation.py",
        "check_phase2_isolated_smoke_readiness.py",
    }
)

_MUTATION_FLAGS = frozenset({"--apply", "--prepare"})
_SENSITIVE_ENV_KEYS = frozenset(
    {
        "SOLANA_RPC_URL",
        "SOLANA_WS_URL",
        "JUPITER_API_KEY",
        "HELIUS_API_KEY",
    }
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
    max_bytes: int = _MAX_TOOL_BYTES,
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


def _load_captured_renderer(
    path: Path,
    name: str,
) -> tuple[Any, Path, bytes, os.stat_result]:
    label = "reviewed lifecycle renderer"
    resolved, encoded, opened = _capture_regular_file(
        path,
        label=label,
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
    RENDER,
    _RENDER_TOOL_PATH_AT_LOAD,
    _RENDER_TOOL_BYTES_AT_LOAD,
    _RENDER_TOOL_STAT_AT_LOAD,
) = _load_captured_renderer(
    RENDER_TOOL,
    "phase2_readonly_preflight_renderer",
)
_RENDER_TOOL_SHA256_AT_LOAD = hashlib.sha256(
    _RENDER_TOOL_BYTES_AT_LOAD
).hexdigest()


@dataclass(frozen=True)
class Phase2ReadOnlyPreflightRun:
    state: str
    next_action: str
    next_tool: str | None
    command_rendered: bool
    command_executed: bool
    preflight_argv: tuple[str, ...] | None
    mutation_flag: str | None
    mutation_flag_appended: bool
    exit_code: int | None
    result_json_valid: bool
    result: dict[str, Any] | list[Any] | None
    failure_category: str | None
    lifecycle: dict[str, Any]
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    daemon_reload_performed: bool
    production_tree_modified: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _preview_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "mutation_flag_appended", True)
    )


_EXPLICIT_BOUNDARY_FLAGS = (
    "rpc_called",
    "database_write_performed",
    "service_control_performed",
    "daemon_reload_performed",
    "production_tree_modified",
)

_TOP_LEVEL_MUTATION_FLAGS = (
    "applied",
    "prepared",
    "build_requested",
    "build_succeeded",
)


def _nested_boundary_ok(value: Any) -> bool:
    if isinstance(value, dict):
        if "read_only" in value and not bool(value["read_only"]):
            return False
        if any(bool(value.get(key, False)) for key in _EXPLICIT_BOUNDARY_FLAGS):
            return False
        return all(_nested_boundary_ok(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_nested_boundary_ok(item) for item in value)
    return True


def _result_boundary_ok(result: Any) -> bool:
    if not _nested_boundary_ok(result):
        return False
    if not isinstance(result, dict):
        return True
    return not any(
        bool(result.get(key, False))
        for key in _TOP_LEVEL_MUTATION_FLAGS
    )


def _reviewed_argv(preview: Any) -> tuple[str, ...] | None:
    raw = getattr(preview, "preflight_argv", None)
    if raw is None:
        return None
    if not isinstance(raw, (tuple, list)) or len(raw) < 2:
        raise ValueError("rendered preflight argv is invalid")

    argv = tuple(str(value) for value in raw)
    if argv[0] != sys.executable:
        raise ValueError("preflight interpreter is not the current Python executable")

    tool = Path(argv[1])
    if tool.name not in REVIEWED_PREFLIGHT_TOOLS:
        raise ValueError("rendered preflight tool is not allowlisted")
    if tool.is_symlink() or not tool.is_file():
        raise ValueError("rendered preflight tool is missing or symlinked")
    try:
        tool.resolve().relative_to(TOOLS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("rendered preflight tool resolves outside reviewed tools") from exc

    if any(value in _MUTATION_FLAGS for value in argv[2:]):
        raise ValueError("rendered preflight contains a mutation flag")
    return argv


def _repo_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = completed.stdout.strip()
    if len(commit) not in {40, 64}:
        raise ValueError("reviewed source commit is invalid")
    return commit


def _renderer_source_identity() -> tuple[str, str]:
    raw = Path(RENDER_TOOL).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed lifecycle renderer is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError("reviewed lifecycle renderer is missing or symlinked") from exc
    if resolved != _RENDER_TOOL_PATH_AT_LOAD:
        raise ValueError("reviewed lifecycle renderer path changed after module load")
    _assert_file_path_stable(
        _RENDER_TOOL_PATH_AT_LOAD,
        _RENDER_TOOL_STAT_AT_LOAD,
        label="reviewed lifecycle renderer",
    )

    commit = _repo_head()
    historical = subprocess.run(
        ["git", "show", f"{commit}:{RENDER_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed lifecycle renderer is not present at reviewed source commit"
        )
    if historical.stdout != _RENDER_TOOL_BYTES_AT_LOAD:
        raise ValueError(
            "reviewed lifecycle renderer bytes do not match reviewed source commit"
        )
    _assert_file_path_stable(
        _RENDER_TOOL_PATH_AT_LOAD,
        _RENDER_TOOL_STAT_AT_LOAD,
        label="reviewed lifecycle renderer",
    )
    return commit, _RENDER_TOOL_SHA256_AT_LOAD


def _capture_preflight_tool(
    argv: tuple[str, ...],
    *,
    source_commit: str,
) -> tuple[Path, bytes, os.stat_result, int]:
    raw = Path(argv[1])
    if raw.is_symlink():
        raise ValueError("rendered preflight tool is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError("rendered preflight tool is missing or symlinked") from exc
    try:
        resolved.relative_to(TOOLS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("rendered preflight tool resolves outside reviewed tools") from exc
    if resolved.name not in REVIEWED_PREFLIGHT_TOOLS:
        raise ValueError("rendered preflight tool is not allowlisted")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        raise ValueError("rendered preflight tool cannot be opened safely") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("rendered preflight tool must be a regular file")
        if before.st_size <= 0 or before.st_size > _MAX_TOOL_BYTES:
            raise ValueError("rendered preflight tool size is invalid")

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
            raise ValueError("rendered preflight tool changed while reading")
        if len(encoded) != before.st_size:
            raise ValueError("rendered preflight tool changed while reading")

        relative = resolved.relative_to(REPO_ROOT).as_posix()
        historical = subprocess.run(
            ["git", "show", f"{source_commit}:{relative}"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=False,
        )
        if historical.returncode != 0 or historical.stdout != encoded:
            raise ValueError(
                "rendered preflight tool bytes do not match reviewed source commit"
            )

        os.lseek(fd, 0, os.SEEK_SET)
        _assert_file_path_stable(
            resolved,
            before,
            label="rendered preflight tool",
        )
        return resolved, encoded, before, fd
    except Exception:
        os.close(fd)
        raise


_EXACT_PREFLIGHT_BOOTSTRAP = (
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


def _captured_preflight_command(
    *,
    argv: tuple[str, ...],
    tool_path: Path,
    tool_fd: int,
) -> list[str]:
    return [
        sys.executable,
        "-c",
        _EXACT_PREFLIGHT_BOOTSTRAP,
        str(tool_fd),
        str(tool_path),
        *argv[2:],
    ]


def _scrubbed_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _SENSITIVE_ENV_KEYS:
        env.pop(key, None)
    return env


def run_next_read_only_preflight(
    *,
    timeout_seconds: int = 60,
    runner: Runner = subprocess.run,
    **handoff_kwargs: Any,
) -> Phase2ReadOnlyPreflightRun:
    if timeout_seconds <= 0 or timeout_seconds > 300:
        raise ValueError("timeout_seconds must be between 1 and 300")

    renderer_before = _renderer_source_identity()
    preview = RENDER.render_lifecycle_command(
        python_executable=sys.executable,
        **handoff_kwargs,
    )
    renderer_after = _renderer_source_identity()
    if renderer_after != renderer_before:
        raise ValueError("reviewed lifecycle renderer changed during render")
    if not _preview_boundary_ok(preview):
        raise ValueError("Phase-2 preflight preview crossed the read-only boundary")

    mutation_flag = getattr(preview, "mutation_flag", None)
    if mutation_flag is not None and mutation_flag not in _MUTATION_FLAGS:
        raise ValueError("unexpected lifecycle mutation flag")

    argv = _reviewed_argv(preview)
    if argv is None:
        return Phase2ReadOnlyPreflightRun(
            state=str(preview.state),
            next_action=str(preview.next_action),
            next_tool=preview.next_tool,
            command_rendered=False,
            command_executed=False,
            preflight_argv=None,
            mutation_flag=mutation_flag,
            mutation_flag_appended=False,
            exit_code=None,
            result_json_valid=False,
            result=None,
            failure_category=None,
            lifecycle=preview.lifecycle,
            read_only=True,
            rpc_called=False,
            database_write_performed=False,
            service_control_performed=False,
            daemon_reload_performed=False,
            production_tree_modified=False,
        )

    (
        preflight_tool_path,
        _preflight_tool_bytes,
        preflight_tool_stat,
        preflight_tool_fd,
    ) = _capture_preflight_tool(
        argv,
        source_commit=renderer_before[0],
    )
    try:
        _assert_file_path_stable(
            preflight_tool_path,
            preflight_tool_stat,
            label="rendered preflight tool",
        )
        command = _captured_preflight_command(
            argv=argv,
            tool_path=preflight_tool_path,
            tool_fd=preflight_tool_fd,
        )
        completed = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            env=_scrubbed_env(),
            pass_fds=(preflight_tool_fd,),
        )
    finally:
        os.close(preflight_tool_fd)

    parsed: dict[str, Any] | list[Any] | None = None
    json_valid = False
    try:
        candidate = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        candidate = None
    if isinstance(candidate, (dict, list)):
        parsed = candidate
        json_valid = True

    if json_valid and not _result_boundary_ok(parsed):
        raise ValueError("executed Phase-2 preflight crossed the read-only boundary")

    if not json_valid:
        failure_category = "INVALID_PREFLIGHT_JSON"
    elif completed.returncode != 0:
        failure_category = "PREFLIGHT_NONZERO_EXIT"
    else:
        failure_category = None

    return Phase2ReadOnlyPreflightRun(
        state=str(preview.state),
        next_action=str(preview.next_action),
        next_tool=preview.next_tool,
        command_rendered=True,
        command_executed=True,
        preflight_argv=argv,
        mutation_flag=mutation_flag,
        mutation_flag_appended=False,
        exit_code=int(completed.returncode),
        result_json_valid=json_valid,
        result=parsed,
        failure_category=failure_category,
        lifecycle=preview.lifecycle,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        daemon_reload_performed=False,
        production_tree_modified=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute only the rendered read-only preflight for the next "
            "isolated Phase-2 lifecycle step. Mutation flags are never "
            "appended or executed."
        )
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
        default=RENDER.HANDOFF.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()

    report = run_next_read_only_preflight(
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
    if report.command_executed and (
        not report.result_json_valid or report.exit_code != 0
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
