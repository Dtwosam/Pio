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
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
COMPAT_TOOL = TOOLS_DIR / "apply_phase2_add_index_compat_patch.py"
CHECK_TOOL = TOOLS_DIR / "check_phase2_isolated_runtime.py"
COMPAT_TOOL_RELATIVE = "deploy/tools/apply_phase2_add_index_compat_patch.py"
CHECK_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_runtime.py"
_MAX_TOOL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _CapturedFile:
    path: Path
    encoded: bytes
    opened: os.stat_result


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
        or current.st_mode != opened.st_mode
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} path changed after capture")


def _capture_regular_file(
    path: Path,
    *,
    label: str,
) -> _CapturedFile:
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
            or after.st_mode != before.st_mode
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
    return _CapturedFile(
        path=resolved,
        encoded=encoded,
        opened=before,
    )


def _load_captured(
    path: Path,
    name: str,
    *,
    label: str,
) -> tuple[Any, _CapturedFile]:
    captured = _capture_regular_file(path, label=label)
    spec = importlib.util.spec_from_file_location(name, captured.path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {label}: {captured.path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        code = compile(captured.encoded, str(captured.path), "exec")
        exec(code, module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    _assert_file_path_stable(
        captured.path,
        captured.opened,
        label=label,
    )
    return module, captured


COMPAT, _COMPAT_CAPTURE = _load_captured(
    COMPAT_TOOL,
    "phase2_isolated_runtime_compat",
    label="reviewed compatibility tool",
)
CHECK, _CHECK_CAPTURE = _load_captured(
    CHECK_TOOL,
    "phase2_isolated_runtime_check",
    label="reviewed isolated-runtime validator",
)
_COMPAT_SHA256 = hashlib.sha256(_COMPAT_CAPTURE.encoded).hexdigest()
_CHECK_SHA256 = hashlib.sha256(_CHECK_CAPTURE.encoded).hexdigest()


def _repo_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("cannot resolve reviewed preparation source commit")
    commit = completed.stdout.strip()
    if len(commit) not in {40, 64}:
        raise ValueError("reviewed preparation source commit is invalid")
    return commit


def _dependency_source_identity() -> tuple[str, str, str]:
    captures = (
        (
            _COMPAT_CAPTURE,
            COMPAT_TOOL_RELATIVE,
            "reviewed compatibility tool",
        ),
        (
            _CHECK_CAPTURE,
            CHECK_TOOL_RELATIVE,
            "reviewed isolated-runtime validator",
        ),
    )
    for captured, _relative, label in captures:
        _assert_file_path_stable(
            captured.path,
            captured.opened,
            label=label,
        )

    commit = _repo_head()
    for captured, relative, label in captures:
        historical = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=False,
        )
        if historical.returncode != 0:
            raise ValueError(
                f"{label} is not present at reviewed source commit"
            )
        if historical.stdout != captured.encoded:
            raise ValueError(
                f"{label} bytes do not match reviewed source commit"
            )
        _assert_file_path_stable(
            captured.path,
            captured.opened,
            label=label,
        )

    return commit, _COMPAT_SHA256, _CHECK_SHA256


@dataclass(frozen=True)
class IsolatedRuntimePreparationReport:
    source_tree: str
    pinned_source_head: str
    reviewed_dependency_commit: str
    reviewed_compat_sha256: str
    reviewed_check_sha256: str
    observed_source_head: str | None
    initial_tracked_clean: bool
    compat_status: str
    compat_ready: bool
    compat_applied: bool
    build_requested: bool
    build_succeeded: bool
    runtime_ready: bool
    executor_path: str
    executor_sha256: str | None
    watch_executor_path: str
    watch_executor_sha256: str | None
    production_tree_modified: bool
    rpc_called: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _source(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("isolated runtime source must not be a symlink")
    source = raw.resolve()
    production = Path("/opt/pio").resolve()
    if source == production or production in source.parents:
        raise ValueError(
            "isolated runtime source must not be inside /opt/pio"
        )
    if not source.is_dir() or not (source / ".git").exists():
        raise ValueError(f"isolated runtime Git tree is missing: {source}")
    return source


def _head(source: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(source),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _tracked_dirty(source: Path) -> tuple[str, ...]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=str(source),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError("cannot inspect isolated runtime Git status")
    return tuple(line for line in proc.stdout.splitlines() if line.strip())


def _build_executor(source: Path) -> None:
    manifest = source / "rust-executor" / "Cargo.toml"
    if not manifest.is_file():
        raise ValueError("isolated runtime Cargo.toml is missing")
    proc = subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            str(manifest),
        ],
        cwd=str(source / "rust-executor"),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(
            f"isolated runtime cargo build failed with status {proc.returncode}"
        )


def prepare_runtime(
    *,
    source_tree: str | Path,
    prepare: bool = False,
) -> IsolatedRuntimePreparationReport:
    dependency_identity = _dependency_source_identity()
    source = _source(source_tree)
    observed_head = _head(source)
    if observed_head != CHECK.PINNED_SOURCE_HEAD:
        raise ValueError(
            "isolated runtime source HEAD does not match reviewed pinned HEAD"
        )

    dirty = _tracked_dirty(source)
    compat_report = COMPAT.evaluate(source_tree=source, apply=False)

    if not prepare:
        if _dependency_source_identity() != dependency_identity:
            raise ValueError(
                "reviewed preparation dependencies changed during preflight"
            )
        return IsolatedRuntimePreparationReport(
            source_tree=str(source),
            pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
            reviewed_dependency_commit=dependency_identity[0],
            reviewed_compat_sha256=dependency_identity[1],
            reviewed_check_sha256=dependency_identity[2],
            observed_source_head=observed_head,
            initial_tracked_clean=not dirty,
            compat_status=compat_report.status,
            compat_ready=compat_report.ready,
            compat_applied=False,
            build_requested=False,
            build_succeeded=False,
            runtime_ready=False,
            executor_path=str(source / CHECK.EXECUTOR_RELATIVE),
            executor_sha256=None,
            watch_executor_path=str(source / CHECK.WATCH_EXECUTOR_RELATIVE),
            watch_executor_sha256=None,
            production_tree_modified=False,
            rpc_called=False,
            service_control_performed=False,
        )

    if dirty:
        raise ValueError("isolated runtime source has unexpected tracked changes")
    if compat_report.status != "READY_APPLY" or not compat_report.ready:
        raise ValueError(
            f"compatibility preflight is not ready: {compat_report.status}"
        )

    if _dependency_source_identity() != dependency_identity:
        raise ValueError(
            "reviewed preparation dependencies changed before compatibility apply"
        )

    applied = COMPAT.evaluate(source_tree=source, apply=True)
    if not applied.applied:
        raise ValueError("compatibility patch was not applied")
    if _dependency_source_identity() != dependency_identity:
        raise ValueError(
            "reviewed preparation dependencies changed after compatibility apply"
        )

    _build_executor(source)
    if _dependency_source_identity() != dependency_identity:
        raise ValueError(
            "reviewed preparation dependencies changed during executor build"
        )
    final = CHECK.inspect_runtime(source)
    if not final.runtime_ready:
        raise ValueError("prepared isolated runtime failed final validation")
    if _dependency_source_identity() != dependency_identity:
        raise ValueError(
            "reviewed preparation dependencies changed during final validation"
        )

    return IsolatedRuntimePreparationReport(
        source_tree=str(source),
        pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
        reviewed_dependency_commit=dependency_identity[0],
        reviewed_compat_sha256=dependency_identity[1],
        reviewed_check_sha256=dependency_identity[2],
        observed_source_head=observed_head,
        initial_tracked_clean=True,
        compat_status=applied.status,
        compat_ready=applied.ready,
        compat_applied=True,
        build_requested=True,
        build_succeeded=True,
        runtime_ready=True,
        executor_path=final.executor_path,
        executor_sha256=final.executor_sha256,
        watch_executor_path=final.watch_executor_path,
        watch_executor_sha256=final.watch_executor_sha256,
        production_tree_modified=False,
        rpc_called=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or prepare a pinned isolated Phase-2 runtime. "
            "Preparation only mutates the supplied side source tree."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="apply the reviewed compatibility patch and build the Rust executor",
    )
    args = parser.parse_args()

    report = prepare_runtime(
        source_tree=args.source_tree,
        prepare=args.prepare,
    )
    print(json.dumps(report.to_record(), indent=2))
    if args.prepare and not report.runtime_ready:
        raise SystemExit(2)
    if not args.prepare and not report.compat_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
