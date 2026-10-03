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
import shlex
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
HANDOFF_TOOL = TOOLS_DIR / "check_phase2_isolated_lifecycle_handoff.py"
HANDOFF_TOOL_RELATIVE = (
    "deploy/tools/check_phase2_isolated_lifecycle_handoff.py"
)
_MAX_TOOL_BYTES = 4 * 1024 * 1024
_SAFE_PARAMETER = re.compile(r"^[a-z][a-z0-9_]*$")


def _assert_handoff_path_stable(
    path: Path,
    opened: os.stat_result,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("reviewed lifecycle handoff path changed after load") from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError("reviewed lifecycle handoff path changed after load")


def _capture_handoff(
    path: Path,
) -> tuple[Path, bytes, os.stat_result]:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed lifecycle handoff must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError("reviewed lifecycle handoff is missing") from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        raise ValueError("reviewed lifecycle handoff cannot be opened safely") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("reviewed lifecycle handoff must be a regular file")
        if before.st_size <= 0 or before.st_size > _MAX_TOOL_BYTES:
            raise ValueError("reviewed lifecycle handoff size is invalid")

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
            raise ValueError("reviewed lifecycle handoff changed while reading")
    finally:
        os.close(fd)

    if len(encoded) != before.st_size:
        raise ValueError("reviewed lifecycle handoff changed while reading")
    _assert_handoff_path_stable(resolved, before)
    return resolved, encoded, before


def _load_captured_handoff(
    path: Path,
    name: str,
) -> tuple[Any, Path, bytes, os.stat_result]:
    resolved, encoded, opened = _capture_handoff(path)
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
    _assert_handoff_path_stable(resolved, opened)
    return module, resolved, encoded, opened


(
    HANDOFF,
    _HANDOFF_TOOL_PATH_AT_LOAD,
    _HANDOFF_TOOL_BYTES_AT_LOAD,
    _HANDOFF_TOOL_STAT_AT_LOAD,
) = _load_captured_handoff(
    HANDOFF_TOOL,
    "phase2_lifecycle_command_handoff",
)
_HANDOFF_TOOL_SHA256_AT_LOAD = hashlib.sha256(
    _HANDOFF_TOOL_BYTES_AT_LOAD
).hexdigest()


@dataclass(frozen=True)
class Phase2LifecycleCommandPreview:
    state: str
    next_action: str
    next_tool: str | None
    preflight_argv: tuple[str, ...] | None
    preflight_command: str | None
    mutation_flag: str | None
    mutation_flag_appended: bool
    lifecycle: dict[str, Any]
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _handoff_source_identity() -> tuple[str, str]:
    raw = Path(HANDOFF_TOOL).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed lifecycle handoff is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError("reviewed lifecycle handoff is missing or symlinked") from exc
    if resolved != _HANDOFF_TOOL_PATH_AT_LOAD:
        raise ValueError("reviewed lifecycle handoff path changed after module load")
    _assert_handoff_path_stable(
        _HANDOFF_TOOL_PATH_AT_LOAD,
        _HANDOFF_TOOL_STAT_AT_LOAD,
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
        ["git", "show", f"{commit}:{HANDOFF_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed lifecycle handoff is not present at reviewed source commit"
        )
    if historical.stdout != _HANDOFF_TOOL_BYTES_AT_LOAD:
        raise ValueError(
            "reviewed lifecycle handoff bytes do not match reviewed source commit"
        )

    _assert_handoff_path_stable(
        _HANDOFF_TOOL_PATH_AT_LOAD,
        _HANDOFF_TOOL_STAT_AT_LOAD,
    )
    return commit, _HANDOFF_TOOL_SHA256_AT_LOAD


def _boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
    )


def _reviewed_tool(name: str | None) -> Path | None:
    if name is None:
        return None
    if not name or Path(name).name != name:
        raise ValueError("next tool name is not a reviewed local basename")
    candidate = TOOLS_DIR / name
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"reviewed next tool is missing: {name}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(TOOLS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("next tool resolves outside reviewed tools directory") from exc
    return resolved


def _parameter_args(parameters: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for key, value in parameters.items():
        if not isinstance(key, str) or not _SAFE_PARAMETER.fullmatch(key):
            raise ValueError(f"unsafe next-step parameter name: {key!r}")
        if isinstance(value, bool) or value is None:
            raise ValueError(
                f"unsupported next-step parameter value for {key}"
            )
        if not isinstance(value, (str, int, float)):
            raise ValueError(
                f"unsupported next-step parameter type for {key}"
            )
        rendered = str(value)
        if "\x00" in rendered:
            raise ValueError(
                f"next-step parameter contains NUL: {key}"
            )
        args.extend(("--" + key.replace("_", "-"), rendered))
    return args


def render_lifecycle_command(
    *,
    python_executable: str = "python3",
    **handoff_kwargs: Any,
) -> Phase2LifecycleCommandPreview:
    if not python_executable.strip() or "\x00" in python_executable:
        raise ValueError("python_executable is invalid")

    handoff_source_before = _handoff_source_identity()
    handoff = HANDOFF.inspect_lifecycle_handoff(**handoff_kwargs)
    handoff_source_after = _handoff_source_identity()
    if handoff_source_after != handoff_source_before:
        raise ValueError("reviewed lifecycle handoff changed during inspection")
    if not _boundary_ok(handoff):
        raise ValueError("Phase-2 lifecycle command crossed the read-only boundary")

    tool = _reviewed_tool(handoff.next_tool)
    argv: tuple[str, ...] | None = None
    command: str | None = None

    if tool is not None:
        parameters = getattr(handoff, "next_parameters", None)
        if not isinstance(parameters, dict):
            raise ValueError("lifecycle handoff is missing next_parameters")
        raw = [
            python_executable,
            str(tool),
            *_parameter_args(parameters),
        ]
        # Intentionally do not append handoff.next_mutation_flag here.
        argv = tuple(raw)
        command = shlex.join(raw)

    mutation_flag = getattr(handoff, "next_mutation_flag", None)
    if mutation_flag is not None:
        if mutation_flag not in {"--apply", "--prepare"}:
            raise ValueError("unexpected lifecycle mutation flag")

    return Phase2LifecycleCommandPreview(
        state=str(handoff.state),
        next_action=str(handoff.next_action),
        next_tool=handoff.next_tool,
        preflight_argv=argv,
        preflight_command=command,
        mutation_flag=mutation_flag,
        mutation_flag_appended=False,
        lifecycle=handoff.to_record(),
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Render the reviewed read-only preflight command for the next "
            "isolated Phase-2 lifecycle step. Mutation flags are reported "
            "separately and never appended or executed."
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
        default=HANDOFF.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    parser.add_argument("--python", default="python3")
    args = parser.parse_args()

    report = render_lifecycle_command(
        python_executable=args.python,
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


if __name__ == "__main__":
    main()
