#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
BUNDLE_VERIFY_TOOL = (
    TOOLS_DIR / "check_phase2_mutation_post_audit_handoff_bundle.py"
)
FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE2_MUTATION_POST_AUDIT_HANDOFF_BUNDLE_ARCHIVE_V1"
_MAX_FILE_BYTES = 8 * 1024 * 1024
_PROTECTED_ROOTS = (
    Path("/opt/pio"),
    Path("/opt/pio/data"),
    Path("/opt/pio-phase2-runtime"),
    Path("/etc/pio"),
    Path("/etc/systemd/system"),
)


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUNDLE = _load(
    BUNDLE_VERIFY_TOOL,
    "phase2_portable_bundle_for_archive",
)


@dataclass(frozen=True)
class Phase2PortableBundleArchiveReport:
    archive_path: str
    artifact_type: str
    archive_sha256: str
    archive_size: int
    source_bundle_sha256: str
    members_archived: int
    deterministic_metadata: bool
    source_bundle_verified: bool
    archive_write_performed: bool
    authorizes_next_action: bool
    requires_fresh_separate_mutation_authorization: bool
    production_file_modified: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _output_file(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("portable bundle archive output must not be a symlink")
    path = raw.resolve(strict=False)
    if any(_under(path, root.resolve()) for root in _PROTECTED_ROOTS):
        raise ValueError("portable bundle archive output is inside a protected production path")
    if path.exists():
        raise ValueError("portable bundle archive output already exists")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("portable bundle archive parent must be an existing directory")
    return path


def _bundle_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "bundle_verified", False)
        and getattr(report, "historical_handoff_only", False)
        and not getattr(report, "current_lifecycle_rechecked", True)
        and not getattr(report, "authorizes_next_action", True)
        and getattr(
            report,
            "requires_fresh_separate_mutation_authorization",
            False,
        )
        and getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "mutation_executed", True)
    )


def _bundle_root(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("portable bundle root must not be a symlink")
    root = raw.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("portable bundle root must be a directory")
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError("portable bundle root permissions must be 0700")
    return root


def _read_member_snapshot(
    path: Path,
    *,
    label: str,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} cannot be opened safely") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if before.st_size <= 0 or before.st_size > _MAX_FILE_BYTES:
            raise ValueError(f"{label} size is invalid")
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise ValueError(f"{label} permissions must be 0600")

        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)

        after = os.fstat(fd)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or not stat.S_ISREG(after.st_mode)
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            raise ValueError(f"{label} changed while reading")
    finally:
        os.close(fd)

    if len(payload) != before.st_size:
        raise ValueError(f"{label} changed while reading")
    return payload, before


def _assert_path_stable(
    path: Path,
    opened: os.stat_result,
    *,
    label: str,
    directory: bool = False,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} path changed during archive build") from exc

    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    expected_mode = 0o700 if directory else 0o600
    if (
        not expected_type(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
        or stat.S_IMODE(current.st_mode) != expected_mode
    ):
        raise ValueError(f"{label} path changed during archive build")


def _bundle_files(root: Path) -> tuple[tuple[str, Path, int], ...]:
    fixed = (
        ("manifest.json", root / "manifest.json", 0o600),
        ("handoff.snapshot.json", root / "handoff.snapshot.json", 0o600),
        ("catalog.snapshot.json", root / "catalog.snapshot.json", 0o600),
    )
    post_root = root / "post-audits"
    if post_root.is_symlink() or not post_root.is_dir():
        raise ValueError("portable bundle post-audit directory is invalid")

    rows: list[tuple[str, Path, int]] = list(fixed)
    rows.append(("post-audits", post_root, 0o700))
    for path in sorted(post_root.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            raise ValueError("portable bundle contains invalid post-audit entry")
        rows.append((f"post-audits/{path.name}", path, 0o600))

    for relative, path, expected_mode in rows:
        if path.is_symlink():
            raise ValueError(f"portable bundle member is symlinked: {relative}")
        if relative == "post-audits":
            if not path.is_dir():
                raise ValueError("portable bundle post-audit member is not a directory")
        else:
            if not path.is_file():
                raise ValueError(f"portable bundle member is not a file: {relative}")
            size = path.stat().st_size
            if size <= 0 or size > _MAX_FILE_BYTES:
                raise ValueError(f"portable bundle member size is invalid: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != expected_mode:
            raise ValueError(f"portable bundle member mode is invalid: {relative}")

    expected_top = {
        "manifest.json",
        "handoff.snapshot.json",
        "catalog.snapshot.json",
        "post-audits",
    }
    if {path.name for path in root.iterdir()} != expected_top:
        raise ValueError("portable bundle contains unexpected top-level entries")
    return tuple(rows)


def _capture_bundle_members(
    root: Path,
) -> tuple[
    tuple[tuple[str, Path, int, bytes | None, os.stat_result], ...],
    os.stat_result,
]:
    root_stat = os.stat(root, follow_symlinks=False)
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise ValueError("portable bundle root permissions must be 0700")

    captured: list[tuple[str, Path, int, bytes | None, os.stat_result]] = []
    for relative, path, mode in _bundle_files(root):
        if path.is_dir():
            opened = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) != mode
            ):
                raise ValueError(
                    f"portable bundle member mode is invalid: {relative}"
                )
            captured.append((relative, path, mode, None, opened))
            continue

        payload, opened = _read_member_snapshot(
            path,
            label=f"portable bundle member {relative}",
        )
        captured.append((relative, path, mode, payload, opened))
    return tuple(captured), root_stat


def _assert_bundle_snapshot_stable(
    root: Path,
    *,
    captured: tuple[
        tuple[str, Path, int, bytes | None, os.stat_result], ...
    ],
    root_stat: os.stat_result,
) -> None:
    _assert_path_stable(
        root,
        root_stat,
        label="portable bundle root",
        directory=True,
    )

    current_rows = _bundle_files(root)
    if tuple(row[0] for row in current_rows) != tuple(
        row[0] for row in captured
    ):
        raise ValueError("portable bundle member set changed during archive build")

    for relative, path, _mode, _payload, opened in captured:
        _assert_path_stable(
            path,
            opened,
            label=f"portable bundle member {relative}",
            directory=relative == "post-audits",
        )


def _tar_info(name: str, *, mode: int, is_dir: bool, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.size = size
    info.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
    return info


def build_phase2_portable_bundle_archive(
    *,
    bundle_directory: str | Path,
    output_path: str | Path,
    repository_root: str | Path = BUNDLE.HANDOFF_VERIFY.REPO_ROOT,
) -> Phase2PortableBundleArchiveReport:
    output = _output_file(output_path)
    root = _bundle_root(bundle_directory)
    captured_members, root_stat = _capture_bundle_members(root)

    manifest_entry = next(
        (
            item
            for item in captured_members
            if item[0] == "manifest.json"
        ),
        None,
    )
    if manifest_entry is None or manifest_entry[3] is None:
        raise ValueError("portable bundle manifest snapshot is missing")
    manifest_bytes = manifest_entry[3]
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("portable bundle manifest snapshot is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("portable bundle manifest snapshot must be an object")
    captured_bundle_sha = manifest.get("bundle_sha256")
    if (
        not isinstance(captured_bundle_sha, str)
        or len(captured_bundle_sha) != 64
        or any(ch not in "0123456789abcdef" for ch in captured_bundle_sha)
    ):
        raise ValueError("portable bundle manifest digest is invalid")

    bundle = BUNDLE.verify_phase2_portable_handoff_bundle(
        bundle_directory=root,
        repository_root=repository_root,
    )
    if not _bundle_boundary_ok(bundle):
        raise ValueError("portable bundle is not verified and non-authorizing")
    if Path(bundle.bundle_directory).resolve(strict=True) != root:
        raise ValueError("portable bundle verifier changed bundle root identity")
    if str(bundle.bundle_sha256) != captured_bundle_sha:
        raise ValueError(
            "portable bundle changed during nested verification"
        )

    _assert_bundle_snapshot_stable(
        root,
        captured=captured_members,
        root_stat=root_stat,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=str(output.parent),
    )
    os.close(fd)
    temp = Path(temp_name)
    try:
        os.chmod(temp, 0o600)
        with tarfile.open(temp, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for relative, _path, mode, payload, _opened in captured_members:
                if payload is None:
                    archive.addfile(
                        _tar_info(relative, mode=mode, is_dir=True)
                    )
                    continue
                info = _tar_info(
                    relative,
                    mode=mode,
                    is_dir=False,
                    size=len(payload),
                )
                archive.addfile(info, io.BytesIO(payload))

        sync_fd = os.open(temp, os.O_RDONLY)
        try:
            os.fsync(sync_fd)
        finally:
            os.close(sync_fd)

        raw = temp.read_bytes()
        temp_stat = os.stat(temp, follow_symlinks=False)
        if output.exists() or output.is_symlink():
            raise ValueError("portable bundle archive output appeared before publish")
        try:
            os.link(
                temp,
                output,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise ValueError(
                "portable bundle archive output appeared before publish"
            ) from exc

        published_stat = os.stat(output, follow_symlinks=False)
        if (
            published_stat.st_dev != temp_stat.st_dev
            or published_stat.st_ino != temp_stat.st_ino
            or published_stat.st_size != len(raw)
            or not stat.S_ISREG(published_stat.st_mode)
            or stat.S_IMODE(published_stat.st_mode) != 0o600
        ):
            try:
                output.unlink()
            except OSError:
                pass
            raise ValueError(
                "portable bundle archive publication identity mismatch"
            )

        temp.unlink()
        temp = None  # type: ignore[assignment]

        directory_fd = os.open(
            output.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

        current = os.stat(output, follow_symlinks=False)
        if (
            current.st_dev != published_stat.st_dev
            or current.st_ino != published_stat.st_ino
            or current.st_size != len(raw)
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise ValueError(
                "portable bundle archive path changed after publish"
            )
    finally:
        if isinstance(temp, Path) and temp.exists():
            temp.unlink()

    return Phase2PortableBundleArchiveReport(
        archive_path=str(output),
        artifact_type=ARTIFACT_TYPE,
        archive_sha256=hashlib.sha256(raw).hexdigest(),
        archive_size=len(raw),
        source_bundle_sha256=captured_bundle_sha,
        members_archived=len(captured_members),
        deterministic_metadata=True,
        source_bundle_verified=True,
        archive_write_performed=True,
        authorizes_next_action=False,
        requires_fresh_separate_mutation_authorization=True,
        production_file_modified=False,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create a deterministic private tar archive of a verified portable "
            "Phase-2 handoff bundle. The archive is evidence only and authorizes "
            "no mutation."
        )
    )
    parser.add_argument("--bundle-directory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--repository-root",
        default=str(BUNDLE.HANDOFF_VERIFY.REPO_ROOT),
    )
    args = parser.parse_args()

    report = build_phase2_portable_bundle_archive(
        bundle_directory=args.bundle_directory,
        output_path=args.output,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))


if __name__ == "__main__":
    main()
