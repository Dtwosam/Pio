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
import tempfile
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
CHECK_TOOL = TOOLS_DIR / "check_phase2_isolated_runtime.py"
CHECK_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_runtime.py"
_MAX_TOOL_BYTES = 4 * 1024 * 1024
_MAX_UNIT_BYTES = 1024 * 1024


def _assert_regular_path_stable(
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


@dataclass(frozen=True)
class _CapturedFile:
    path: Path
    encoded: bytes
    opened: os.stat_result
    blob_sha1: str


@dataclass(frozen=True)
class _PathState:
    kind: str
    stat_result: os.stat_result | None
    symlink_target: str | None


def _path_state(path: Path) -> _PathState:
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return _PathState("ABSENT", None, None)
    except OSError as exc:
        raise ValueError(f"cannot inspect path state: {path}") from exc

    if stat.S_ISLNK(current.st_mode):
        try:
            target = os.readlink(path)
        except OSError as exc:
            raise ValueError(f"cannot inspect symlink target: {path}") from exc
        return _PathState("SYMLINK", current, target)
    if stat.S_ISREG(current.st_mode):
        return _PathState("REGULAR", current, None)
    return _PathState("OTHER", current, None)


def _assert_path_state_stable(
    path: Path,
    expected: _PathState,
    *,
    label: str,
) -> None:
    current = _path_state(path)
    if current.kind != expected.kind:
        raise ValueError(f"{label} path changed after preflight")
    if expected.kind == "ABSENT":
        return
    before = expected.stat_result
    after = current.stat_result
    assert before is not None and after is not None
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_mode != after.st_mode
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or expected.symlink_target != current.symlink_target
    ):
        raise ValueError(f"{label} path changed after preflight")


def _capture_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    allow_missing: bool = False,
) -> _CapturedFile | None:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        if allow_missing:
            return None
        raise ValueError(f"{label} must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ValueError(f"{label} is missing")
    except OSError as exc:
        raise ValueError(f"{label} cannot be resolved") from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ValueError(f"{label} is missing")
    except OSError as exc:
        raise ValueError(f"{label} cannot be opened safely") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            if allow_missing:
                return None
            raise ValueError(f"{label} must be a regular file")
        if before.st_size < 0 or before.st_size > max_bytes:
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
    _assert_regular_path_stable(resolved, before, label=label)
    header = f"blob {len(encoded)}\0".encode()
    blob = hashlib.sha1(header + encoded).hexdigest()
    return _CapturedFile(
        path=resolved,
        encoded=encoded,
        opened=before,
        blob_sha1=blob,
    )


def _load_captured_runtime_checker(
    path: Path,
    name: str,
) -> tuple[Any, _CapturedFile]:
    captured = _capture_regular_file(
        path,
        label="reviewed runtime checker",
        max_bytes=_MAX_TOOL_BYTES,
    )
    assert captured is not None

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
    _assert_regular_path_stable(
        captured.path,
        captured.opened,
        label="reviewed runtime checker",
    )
    return module, captured


CHECK, _CHECK_CAPTURE = _load_captured_runtime_checker(
    CHECK_TOOL,
    "phase2_isolated_systemd_runtime_check",
)
_CHECK_SHA256 = hashlib.sha256(_CHECK_CAPTURE.encoded).hexdigest()

UNIT_CONTRACT = {
    "pio-phase2-isolated-prestate-stream@.service":
        "4270a0c7b3aec8844ae9f06c6a073aa7b9d247af",
    "pio-phase2-isolated-add-detector.service":
        "fdb5334cca2174421e084813b42052f5399c435b",
    "pio-phase2-isolated-evidence-cycle.service":
        "46fb8d90bbc008702711e14c1de643aecb41c57c",
    "pio-phase2-isolated-evidence-cycle.timer":
        "9b0e757ba3857c2b33d2cecfe94be98f0913a72f",
    "pio-phase2-isolated-rate-limit-pause.service":
        "07e8d4a58243537bea74226da4ab6548bf212496",
}


@dataclass(frozen=True)
class UnitStatus:
    name: str
    expected_blob: str
    source_blob: str | None
    installed_blob: str | None
    status: str


@dataclass(frozen=True)
class InstallReport:
    source_tree: str
    runtime_current: str
    destination: str
    runtime_ready: bool
    ready: bool
    applied: bool
    units: tuple[UnitStatus, ...]
    daemon_reload_performed: bool
    service_control_performed: bool
    services_enabled: bool
    services_started: bool
    rpc_called: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _UnitSnapshot:
    name: str
    expected_blob: str
    source: _CapturedFile | None
    source_state: _PathState
    target: _CapturedFile | None
    target_state: _PathState
    status: str


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


def _runtime_checker_source_identity() -> tuple[str, str]:
    raw = Path(CHECK_TOOL).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed runtime checker is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError("reviewed runtime checker is missing or symlinked") from exc
    if resolved != _CHECK_CAPTURE.path:
        raise ValueError("reviewed runtime checker path changed after load")
    _assert_regular_path_stable(
        _CHECK_CAPTURE.path,
        _CHECK_CAPTURE.opened,
        label="reviewed runtime checker",
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
            "reviewed runtime checker is not present at reviewed source commit"
        )
    if historical.stdout != _CHECK_CAPTURE.encoded:
        raise ValueError(
            "reviewed runtime checker bytes do not match reviewed source commit"
        )
    _assert_regular_path_stable(
        _CHECK_CAPTURE.path,
        _CHECK_CAPTURE.opened,
        label="reviewed runtime checker",
    )
    return commit, _CHECK_SHA256


def git_blob_sha(path: Path) -> str | None:
    captured = _capture_regular_file(
        path,
        label=f"unit file {path}",
        max_bytes=_MAX_UNIT_BYTES,
        allow_missing=True,
    )
    return captured.blob_sha1 if captured is not None else None


def _regular_directory(value: str | Path, *, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = raw.resolve()
    if not resolved.is_dir():
        raise ValueError(f"{label} is missing: {resolved}")
    return resolved


def _symlink_identity(path: Path) -> tuple[os.stat_result, str]:
    if not path.is_symlink():
        raise ValueError("isolated runtime current path must be a symlink")
    before = os.lstat(path)
    try:
        target = os.readlink(path)
    except OSError as exc:
        raise ValueError("cannot read isolated runtime current symlink") from exc
    return before, target


def _assert_symlink_stable(
    path: Path,
    opened: os.stat_result,
    target: str,
) -> None:
    try:
        current = os.lstat(path)
        current_target = os.readlink(path)
    except OSError as exc:
        raise ValueError("isolated runtime current path changed during validation") from exc
    if (
        not stat.S_ISLNK(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
        or current_target != target
    ):
        raise ValueError("isolated runtime current path changed during validation")


def _validate_runtime(runtime_root: str | Path) -> Path:
    root = Path(runtime_root).expanduser()
    if root.is_symlink():
        raise ValueError("runtime root must not be a symlink")
    root = root.resolve()
    current = root / "current"
    current_stat, current_target = _symlink_identity(current)
    resolved = current.resolve(strict=True)
    releases = (root / "releases").resolve()
    try:
        resolved.relative_to(releases)
    except ValueError as exc:
        raise ValueError(
            "isolated runtime current target is outside releases"
        ) from exc

    source_before = _runtime_checker_source_identity()
    report = CHECK.inspect_runtime(resolved)
    source_after = _runtime_checker_source_identity()
    if source_after != source_before:
        raise ValueError("reviewed runtime checker changed during validation")
    _assert_symlink_stable(current, current_stat, current_target)
    if current.resolve(strict=True) != resolved:
        raise ValueError("isolated runtime current target changed during validation")
    if not report.runtime_ready:
        raise ValueError("isolated runtime failed reviewed validation")
    return current


def _snapshot_unit(
    *,
    source: Path,
    destination: Path,
    name: str,
    expected: str,
) -> _UnitSnapshot:
    source_path = source / "deploy" / "systemd" / name
    target_path = destination / name
    source_state = _path_state(source_path)
    target_state = _path_state(target_path)

    source_capture = _capture_regular_file(
        source_path,
        label=f"reviewed source unit {name}",
        max_bytes=_MAX_UNIT_BYTES,
        allow_missing=True,
    )
    target_capture = _capture_regular_file(
        target_path,
        label=f"installed unit {name}",
        max_bytes=_MAX_UNIT_BYTES,
        allow_missing=True,
    )

    source_blob = (
        source_capture.blob_sha1 if source_capture is not None else None
    )
    target_blob = (
        target_capture.blob_sha1 if target_capture is not None else None
    )

    if source_blob != expected:
        status = "SOURCE_DRIFT"
    elif target_state.kind == "SYMLINK":
        status = "CONFLICT_SYMLINK"
    elif target_state.kind == "OTHER":
        status = "CONFLICT_MODIFIED"
    elif target_blob is None:
        status = "READY_CREATE"
    elif target_blob == expected:
        status = "ALREADY_TARGET"
    else:
        status = "CONFLICT_MODIFIED"

    return _UnitSnapshot(
        name=name,
        expected_blob=expected,
        source=source_capture,
        source_state=source_state,
        target=target_capture,
        target_state=target_state,
        status=status,
    )


def _assert_snapshot_stable(
    snapshot: _UnitSnapshot,
    *,
    source: Path,
    destination: Path,
) -> None:
    source_path = source / "deploy" / "systemd" / snapshot.name
    target_path = destination / snapshot.name
    _assert_path_state_stable(
        source_path,
        snapshot.source_state,
        label=f"reviewed source unit {snapshot.name}",
    )
    _assert_path_state_stable(
        target_path,
        snapshot.target_state,
        label=f"installed unit {snapshot.name}",
    )
    if snapshot.source is not None:
        _assert_regular_path_stable(
            snapshot.source.path,
            snapshot.source.opened,
            label=f"reviewed source unit {snapshot.name}",
        )
    if snapshot.target is not None:
        _assert_regular_path_stable(
            snapshot.target.path,
            snapshot.target.opened,
            label=f"installed unit {snapshot.name}",
        )


def _inspect_install_snapshot(
    *,
    source_tree: str | Path,
    runtime_root: str | Path,
    destination: str | Path,
) -> tuple[InstallReport, tuple[_UnitSnapshot, ...]]:
    source = _regular_directory(source_tree, label="source tree")
    runtime_current = _validate_runtime(runtime_root)
    destination_path = _regular_directory(
        destination,
        label="systemd destination",
    )

    runtime_target_before = runtime_current.resolve(strict=True)
    snapshots = tuple(
        _snapshot_unit(
            source=source,
            destination=destination_path,
            name=name,
            expected=expected,
        )
        for name, expected in UNIT_CONTRACT.items()
    )

    for snapshot in snapshots:
        _assert_snapshot_stable(
            snapshot,
            source=source,
            destination=destination_path,
        )
    if runtime_current.resolve(strict=True) != runtime_target_before:
        raise ValueError("isolated runtime current target changed during install preflight")

    rows = tuple(
        UnitStatus(
            name=snapshot.name,
            expected_blob=snapshot.expected_blob,
            source_blob=(
                snapshot.source.blob_sha1
                if snapshot.source is not None
                else None
            ),
            installed_blob=(
                snapshot.target.blob_sha1
                if snapshot.target is not None
                else None
            ),
            status=snapshot.status,
        )
        for snapshot in snapshots
    )
    ready = all(
        row.status in {"READY_CREATE", "ALREADY_TARGET"}
        for row in rows
    )
    report = InstallReport(
        source_tree=str(source),
        runtime_current=str(runtime_current),
        destination=str(destination_path),
        runtime_ready=True,
        ready=ready,
        applied=False,
        units=rows,
        daemon_reload_performed=False,
        service_control_performed=False,
        services_enabled=False,
        services_started=False,
        rpc_called=False,
    )
    return report, snapshots


def inspect_install(
    *,
    source_tree: str | Path,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    destination: str | Path = "/etc/systemd/system",
) -> InstallReport:
    report, _snapshots = _inspect_install_snapshot(
        source_tree=source_tree,
        runtime_root=runtime_root,
        destination=destination,
    )
    return report


def _write_exact_new_unit(
    *,
    destination: Path,
    name: str,
    encoded: bytes,
    expected_blob: str,
) -> os.stat_result:
    target = destination / name
    if _path_state(target).kind != "ABSENT":
        raise ValueError(f"unit target changed after preflight: {name}")

    fd: int | None = None
    temp: Path | None = None
    opened: os.stat_result | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{name}.",
            suffix=".tmp",
            dir=str(destination),
        )
        temp = Path(temp_name)
        os.fchmod(fd, 0o644)

        view = memoryview(encoded)
        offset = 0
        while offset < len(view):
            written = os.write(fd, view[offset:])
            if written <= 0:
                raise ValueError(f"failed writing staged unit: {name}")
            offset += written
        os.fsync(fd)

        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size != len(encoded)
            or stat.S_IMODE(opened.st_mode) != 0o644
        ):
            raise ValueError(f"staged unit metadata mismatch: {name}")

        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        staged = b"".join(chunks)
        header = f"blob {len(staged)}\0".encode()
        if hashlib.sha1(header + staged).hexdigest() != expected_blob:
            raise ValueError(f"staged unit blob mismatch: {name}")

        temp_stat = os.stat(temp, follow_symlinks=False)
        if (
            temp_stat.st_dev != opened.st_dev
            or temp_stat.st_ino != opened.st_ino
            or temp_stat.st_size != opened.st_size
            or temp_stat.st_mtime_ns != opened.st_mtime_ns
            or temp_stat.st_ctime_ns != opened.st_ctime_ns
        ):
            raise ValueError(f"staged unit path changed before publish: {name}")

        try:
            os.link(temp, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError(
                f"unit target appeared before publish: {name}"
            ) from exc

        published = os.stat(target, follow_symlinks=False)
        if (
            published.st_dev != opened.st_dev
            or published.st_ino != opened.st_ino
            or not stat.S_ISREG(published.st_mode)
            or published.st_size != opened.st_size
            or stat.S_IMODE(published.st_mode) != 0o644
        ):
            raise ValueError(f"published unit identity mismatch: {name}")

        temp.unlink()
        temp = None

        directory_fd = os.open(
            destination,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return published
    finally:
        if fd is not None:
            os.close(fd)
        if temp is not None and temp.exists():
            temp.unlink()


def _unlink_if_same(path: Path, opened: os.stat_result) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        current.st_dev == opened.st_dev
        and current.st_ino == opened.st_ino
        and stat.S_ISREG(current.st_mode)
    ):
        path.unlink(missing_ok=True)


def install_units(
    *,
    source_tree: str | Path,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    destination: str | Path = "/etc/systemd/system",
    apply: bool = False,
) -> InstallReport:
    report, snapshots = _inspect_install_snapshot(
        source_tree=source_tree,
        runtime_root=runtime_root,
        destination=destination,
    )
    if not apply:
        return report
    if not report.ready:
        raise ValueError("isolated Phase-2 systemd install preflight is not ready")

    source = Path(report.source_tree)
    destination_path = Path(report.destination)
    created: list[tuple[Path, os.stat_result]] = []

    try:
        for snapshot in snapshots:
            _assert_snapshot_stable(
                snapshot,
                source=source,
                destination=destination_path,
            )
            if snapshot.status == "ALREADY_TARGET":
                continue
            if snapshot.status != "READY_CREATE" or snapshot.source is None:
                raise ValueError(
                    f"unit preflight changed before apply: {snapshot.name}"
                )
            published = _write_exact_new_unit(
                destination=destination_path,
                name=snapshot.name,
                encoded=snapshot.source.encoded,
                expected_blob=snapshot.expected_blob,
            )
            created.append(
                (destination_path / snapshot.name, published)
            )
    except Exception:
        for path, opened in reversed(created):
            _unlink_if_same(path, opened)
        raise

    final = inspect_install(
        source_tree=source,
        runtime_root=runtime_root,
        destination=destination_path,
    )
    if not final.ready or any(
        row.status != "ALREADY_TARGET" for row in final.units
    ):
        for path, opened in reversed(created):
            _unlink_if_same(path, opened)
        raise ValueError("post-install unit validation failed")

    return InstallReport(
        source_tree=final.source_tree,
        runtime_current=final.runtime_current,
        destination=final.destination,
        runtime_ready=True,
        ready=True,
        applied=True,
        units=final.units,
        daemon_reload_performed=False,
        service_control_performed=False,
        services_enabled=False,
        services_started=False,
        rpc_called=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or install exact isolated Phase-2 systemd unit files. "
            "This tool never daemon-reloads, enables, starts, stops, or "
            "restarts services and never makes RPC calls."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument(
        "--runtime-root",
        default="/opt/pio-phase2-runtime",
    )
    parser.add_argument(
        "--destination",
        default="/etc/systemd/system",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = install_units(
        source_tree=args.source_tree,
        runtime_root=args.runtime_root,
        destination=args.destination,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
