#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
CHECK_TOOL = TOOLS_DIR / "check_phase2_isolated_runtime.py"
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


def _load_captured_check(
    path: Path,
    name: str,
) -> tuple[Any, _CapturedFile]:
    label = "reviewed isolated-runtime validator"
    captured = _capture_regular_file(path, label=label)
    spec = importlib.util.spec_from_file_location(name, captured.path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {captured.path}")
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


CHECK, _CHECK_CAPTURE = _load_captured_check(
    CHECK_TOOL,
    "phase2_isolated_runtime_stage_check",
)
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
        raise ValueError("cannot resolve reviewed stage source commit")
    commit = completed.stdout.strip()
    if len(commit) not in {40, 64}:
        raise ValueError("reviewed stage source commit is invalid")
    return commit


def _check_source_identity() -> tuple[str, str]:
    _assert_file_path_stable(
        _CHECK_CAPTURE.path,
        _CHECK_CAPTURE.opened,
        label="reviewed isolated-runtime validator",
    )
    commit = _repo_head()
    historical = subprocess.run(
        ["git", "show", f"{commit}:{CHECK_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed isolated-runtime validator is not present at reviewed source commit"
        )
    if historical.stdout != _CHECK_CAPTURE.encoded:
        raise ValueError(
            "reviewed isolated-runtime validator bytes do not match reviewed source commit"
        )
    _assert_file_path_stable(
        _CHECK_CAPTURE.path,
        _CHECK_CAPTURE.opened,
        label="reviewed isolated-runtime validator",
    )
    return commit, _CHECK_SHA256


@dataclass(frozen=True)
class IsolatedRuntimeStageReport:
    source_tree: str
    destination_root: str
    release_path: str
    current_link: str
    pinned_source_head: str
    reviewed_check_commit: str
    reviewed_check_sha256: str
    source_runtime_ready: bool
    source_executor_sha256: str
    source_watch_executor_sha256: str
    release_executor_sha256: str | None
    release_watch_executor_sha256: str | None
    existing_release_reused: bool
    applied: bool
    current_target: str | None
    production_tree_modified: bool
    rpc_called: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _raw_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def _resolved(value: str | Path) -> Path:
    return _raw_path(value).resolve()


def _assert_outside_production(path: Path, *, label: str) -> None:
    production = Path("/opt/pio").resolve()
    if path == production or production in path.parents:
        raise ValueError(f"{label} must not be inside /opt/pio")


def _source(value: str | Path) -> Path:
    raw = _raw_path(value)
    if raw.is_symlink():
        raise ValueError("source tree must not be a symlink")
    source = raw.resolve()
    _assert_outside_production(source, label="source tree")
    if not source.is_dir():
        raise ValueError(f"prepared runtime source is missing: {source}")
    return source


def _destination(value: str | Path) -> Path:
    raw = _raw_path(value)
    if raw.is_symlink():
        raise ValueError("destination root must not be a symlink")
    destination = raw.resolve()
    _assert_outside_production(destination, label="destination root")
    return destination


def _release_path(destination: Path) -> Path:
    return destination / "releases" / CHECK.PINNED_SOURCE_HEAD


def _current_target(current: Path) -> str | None:
    if not current.is_symlink():
        return None
    return os.readlink(current)


def _binary_identity(report: Any) -> tuple[str, str]:
    executor_sha = getattr(report, "executor_sha256", None)
    watcher_sha = getattr(report, "watch_executor_sha256", None)
    if not isinstance(executor_sha, str) or len(executor_sha) != 64:
        raise ValueError("runtime executor SHA-256 is missing or invalid")
    if not isinstance(watcher_sha, str) or len(watcher_sha) != 64:
        raise ValueError("runtime account-watch SHA-256 is missing or invalid")
    return executor_sha, watcher_sha


def _validate_source(source: Path) -> Any:
    report = CHECK.inspect_runtime(source)
    if not report.runtime_ready:
        raise ValueError("source runtime failed reviewed validation")
    _binary_identity(report)
    return report


def _validate_existing_release(release: Path) -> Any:
    report = CHECK.inspect_runtime(release)
    if not report.runtime_ready:
        raise ValueError(
            "existing pinned release failed reviewed validation"
        )
    _binary_identity(report)
    return report


def _replace_current_target(
    destination: Path,
    target: str | None,
) -> None:
    current = destination / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("current path exists and is not a symlink")

    if target is None:
        if current.is_symlink():
            current.unlink()
        return

    temp = destination / f".current.{os.getpid()}.tmp"
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    temp.symlink_to(target)
    try:
        os.replace(temp, current)
    finally:
        if temp.exists() or temp.is_symlink():
            temp.unlink()


def _atomic_current_link(destination: Path, release: Path) -> None:
    relative_target = os.path.relpath(release, destination)
    _replace_current_target(destination, relative_target)


def stage_runtime(
    *,
    source_tree: str | Path,
    destination_root: str | Path = "/opt/pio-phase2-runtime",
    apply: bool = False,
) -> IsolatedRuntimeStageReport:
    check_identity = _check_source_identity()
    source = _source(source_tree)
    destination = _destination(destination_root)
    source_report = _validate_source(source)
    source_binary_identity = _binary_identity(source_report)

    release = _release_path(destination)
    current = destination / "current"
    existing_release = release.exists()
    release_binary_identity: tuple[str, str] | None = None

    if existing_release:
        if release.is_symlink() or not release.is_dir():
            raise ValueError(
                "existing pinned release is not a regular directory"
            )
        release_report = _validate_existing_release(release)
        release_binary_identity = _binary_identity(release_report)
        if release_binary_identity != source_binary_identity:
            raise ValueError(
                "existing pinned release binary identity does not match prepared source"
            )

    if not apply:
        if _check_source_identity() != check_identity:
            raise ValueError(
                "reviewed isolated-runtime validator changed during stage preflight"
            )
        return IsolatedRuntimeStageReport(
            source_tree=str(source),
            destination_root=str(destination),
            release_path=str(release),
            current_link=str(current),
            pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
            reviewed_check_commit=check_identity[0],
            reviewed_check_sha256=check_identity[1],
            source_runtime_ready=True,
            source_executor_sha256=source_binary_identity[0],
            source_watch_executor_sha256=source_binary_identity[1],
            release_executor_sha256=(
                release_binary_identity[0]
                if release_binary_identity is not None
                else None
            ),
            release_watch_executor_sha256=(
                release_binary_identity[1]
                if release_binary_identity is not None
                else None
            ),
            existing_release_reused=existing_release,
            applied=False,
            current_target=_current_target(current),
            production_tree_modified=False,
            rpc_called=False,
            service_control_performed=False,
        )

    if _check_source_identity() != check_identity:
        raise ValueError(
            "reviewed isolated-runtime validator changed before stage apply"
        )
    if _binary_identity(_validate_source(source)) != source_binary_identity:
        raise ValueError(
            "prepared source binary identity changed before stage apply"
        )

    if current.exists() and not current.is_symlink():
        raise ValueError("current path exists and is not a symlink")
    previous_current_target = _current_target(current)

    destination.mkdir(parents=True, exist_ok=True)
    releases = destination / "releases"
    releases.mkdir(parents=True, exist_ok=True)

    reused = release.exists()
    if reused:
        release_report = _validate_existing_release(release)
        release_binary_identity = _binary_identity(release_report)
        if release_binary_identity != source_binary_identity:
            raise ValueError(
                "existing pinned release binary identity does not match prepared source"
            )
    else:
        staging = releases / (
            f".staging-{CHECK.PINNED_SOURCE_HEAD}.{os.getpid()}"
        )
        if staging.exists() or staging.is_symlink():
            raise ValueError(f"staging path already exists: {staging}")
        try:
            shutil.copytree(
                source,
                staging,
                symlinks=True,
                copy_function=shutil.copy2,
            )
            if _binary_identity(_validate_source(source)) != source_binary_identity:
                raise ValueError(
                    "prepared source binary identity changed during stage copy"
                )
            staging_report = _validate_existing_release(staging)
            staging_identity = _binary_identity(staging_report)
            if staging_identity != source_binary_identity:
                raise ValueError(
                    "staged runtime binary identity does not match prepared source"
                )
            if _check_source_identity() != check_identity:
                raise ValueError(
                    "reviewed isolated-runtime validator changed before release publish"
                )
            os.replace(staging, release)
            release_binary_identity = staging_identity
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    release_report = _validate_existing_release(release)
    release_binary_identity = _binary_identity(release_report)
    if release_binary_identity != source_binary_identity:
        raise ValueError(
            "release binary identity changed before current-link update"
        )
    if _binary_identity(_validate_source(source)) != source_binary_identity:
        raise ValueError(
            "prepared source binary identity changed before current-link update"
        )
    if _check_source_identity() != check_identity:
        raise ValueError(
            "reviewed isolated-runtime validator changed before current-link update"
        )

    _atomic_current_link(destination, release)
    try:
        if _binary_identity(_validate_existing_release(release)) != source_binary_identity:
            raise ValueError(
                "release binary identity changed after current-link update"
            )
        if _check_source_identity() != check_identity:
            raise ValueError(
                "reviewed isolated-runtime validator changed after current-link update"
            )
    except Exception:
        _replace_current_target(destination, previous_current_target)
        raise

    return IsolatedRuntimeStageReport(
        source_tree=str(source),
        destination_root=str(destination),
        release_path=str(release),
        current_link=str(current),
        pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
        reviewed_check_commit=check_identity[0],
        reviewed_check_sha256=check_identity[1],
        source_runtime_ready=True,
        source_executor_sha256=source_binary_identity[0],
        source_watch_executor_sha256=source_binary_identity[1],
        release_executor_sha256=release_binary_identity[0],
        release_watch_executor_sha256=release_binary_identity[1],
        existing_release_reused=reused,
        applied=True,
        current_target=_current_target(current),
        production_tree_modified=False,
        rpc_called=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or atomically stage a reviewed prepared Phase-2 "
            "runtime outside /opt/pio. This tool never controls systemd "
            "or makes RPC calls."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument(
        "--destination-root",
        default="/opt/pio-phase2-runtime",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = stage_runtime(
        source_tree=args.source_tree,
        destination_root=args.destination_root,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))


if __name__ == "__main__":
    main()
