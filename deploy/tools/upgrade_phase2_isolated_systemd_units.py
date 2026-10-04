#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


UNIT_TRANSITIONS = {
    "pio-phase2-isolated-evidence-cycle.service": (
        "3a82fc92e9785f6dcbde8cfd9c3458a71e34c8e3",
        "46fb8d90bbc008702711e14c1de643aecb41c57c",
    ),
    "pio-phase2-isolated-evidence-cycle.timer": (
        "e6781b6e7f4d235ec170d7f08d8f9c25414100ef",
        "9b0e757ba3857c2b33d2cecfe94be98f0913a72f",
    ),
}


@dataclass(frozen=True)
class UnitUpgradeStatus:
    name: str
    expected_previous_blob: str
    expected_target_blob: str
    source_blob: str | None
    installed_blob: str | None
    status: str


@dataclass(frozen=True)
class UnitUpgradeReport:
    source_tree: str
    destination: str
    backup_root: str | None
    ready: bool
    upgrade_needed: bool
    installer_needed: bool
    applied: bool
    units: tuple[UnitUpgradeStatus, ...]
    files_updated: int
    daemon_reload_performed: bool
    service_control_performed: bool
    rpc_called: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _fsync_directory(path: Path) -> None:
    fd = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_regular(
    path: Path,
    *,
    allow_missing: bool,
) -> tuple[bytes | None, os.stat_result | None]:
    raw = Path(path)
    if raw.is_symlink():
        raise ValueError(f"path must not be a symlink: {raw}")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(raw, flags)
    except FileNotFoundError:
        if allow_missing:
            return None, None
        raise ValueError(f"required file is missing: {raw}")
    except OSError as exc:
        raise ValueError(f"path cannot be opened safely: {raw}") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"path must be a regular file: {raw}")
        if before.st_size < 0 or before.st_size > 4 * 1024 * 1024:
            raise ValueError(f"file size is invalid: {raw}")

        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)

        after_fd = os.fstat(fd)
        if (
            after_fd.st_dev != before.st_dev
            or after_fd.st_ino != before.st_ino
            or after_fd.st_size != before.st_size
            or after_fd.st_mtime_ns != before.st_mtime_ns
            or after_fd.st_ctime_ns != before.st_ctime_ns
        ):
            raise ValueError(f"path changed while reading: {raw}")
    finally:
        os.close(fd)

    if len(payload) != before.st_size:
        raise ValueError(f"path changed while reading: {raw}")
    after_path = os.stat(raw, follow_symlinks=False)
    if (
        not stat.S_ISREG(after_path.st_mode)
        or after_path.st_dev != before.st_dev
        or after_path.st_ino != before.st_ino
        or after_path.st_size != before.st_size
        or after_path.st_mtime_ns != before.st_mtime_ns
        or after_path.st_ctime_ns != before.st_ctime_ns
    ):
        raise ValueError(f"path changed while reading: {raw}")
    return payload, before


def _inspect(
    *,
    source_tree: Path,
    destination: Path,
) -> tuple[UnitUpgradeStatus, ...]:
    rows: list[UnitUpgradeStatus] = []
    for name, (previous_blob, target_blob) in UNIT_TRANSITIONS.items():
        source = source_tree / "deploy" / "systemd" / name
        source_bytes, _ = _read_regular(source, allow_missing=False)
        assert source_bytes is not None
        source_blob = git_blob_sha_bytes(source_bytes)

        installed = destination / name
        try:
            installed_bytes, _ = _read_regular(installed, allow_missing=True)
        except ValueError:
            rows.append(
                UnitUpgradeStatus(
                    name=name,
                    expected_previous_blob=previous_blob,
                    expected_target_blob=target_blob,
                    source_blob=source_blob,
                    installed_blob=None,
                    status="CONFLICT_INSTALLED_PATH",
                )
            )
            continue

        installed_blob = (
            git_blob_sha_bytes(installed_bytes)
            if installed_bytes is not None
            else None
        )

        if source_blob != target_blob:
            status = "SOURCE_DRIFT"
        elif installed_blob == target_blob:
            status = "ALREADY_TARGET"
        elif installed_blob == previous_blob:
            status = "READY_UPDATE"
        elif installed_blob is None:
            status = "NOT_INSTALLED"
        else:
            status = "CONFLICT_MODIFIED"

        rows.append(
            UnitUpgradeStatus(
                name=name,
                expected_previous_blob=previous_blob,
                expected_target_blob=target_blob,
                source_blob=source_blob,
                installed_blob=installed_blob,
                status=status,
            )
        )
    return tuple(rows)


def inspect_upgrade(
    *,
    source_tree: str | Path,
    destination: str | Path = "/etc/systemd/system",
) -> UnitUpgradeReport:
    source_raw = Path(source_tree).expanduser()
    destination_raw = Path(destination).expanduser()
    if source_raw.is_symlink():
        raise ValueError("unit upgrade source must not be a symlink")
    if destination_raw.is_symlink():
        raise ValueError("systemd destination must not be a symlink")
    source = source_raw.resolve()
    target_dir = destination_raw.resolve()
    if source == Path("/opt/pio").resolve():
        raise ValueError("unit upgrade source must not be /opt/pio")
    if not source.is_dir():
        raise ValueError(f"source tree is missing: {source}")
    if not target_dir.is_dir():
        raise ValueError(f"systemd destination is missing: {target_dir}")

    rows = _inspect(source_tree=source, destination=target_dir)
    allowed = {"READY_UPDATE", "ALREADY_TARGET", "NOT_INSTALLED"}
    ready = all(row.status in allowed for row in rows)
    upgrade_needed = any(row.status == "READY_UPDATE" for row in rows)
    installer_needed = any(row.status == "NOT_INSTALLED" for row in rows)

    return UnitUpgradeReport(
        source_tree=str(source),
        destination=str(target_dir),
        backup_root=None,
        ready=ready,
        upgrade_needed=upgrade_needed,
        installer_needed=installer_needed,
        applied=False,
        units=rows,
        files_updated=0,
        daemon_reload_performed=False,
        service_control_performed=False,
        rpc_called=False,
    )


def _write_target(
    *,
    destination: Path,
    source_bytes: bytes,
    expected_previous_blob: str,
    expected_target_blob: str,
) -> None:
    current_bytes, current_stat = _read_regular(destination, allow_missing=False)
    assert current_bytes is not None and current_stat is not None
    if git_blob_sha_bytes(current_bytes) != expected_previous_blob:
        raise ValueError(f"installed unit changed before update: {destination.name}")

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".upgrade",
        dir=str(destination.parent),
    )
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o644)
        view = memoryview(source_bytes)
        offset = 0
        while offset < len(view):
            written = os.write(fd, view[offset:])
            if written <= 0:
                raise ValueError(f"failed staging unit update: {destination.name}")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = -1

        staged = temp.read_bytes()
        if git_blob_sha_bytes(staged) != expected_target_blob:
            raise ValueError(f"staged unit blob mismatch: {destination.name}")

        latest_bytes, latest_stat = _read_regular(destination, allow_missing=False)
        assert latest_bytes is not None and latest_stat is not None
        if (
            latest_stat.st_dev != current_stat.st_dev
            or latest_stat.st_ino != current_stat.st_ino
            or latest_stat.st_size != current_stat.st_size
            or latest_stat.st_mtime_ns != current_stat.st_mtime_ns
            or latest_stat.st_ctime_ns != current_stat.st_ctime_ns
            or git_blob_sha_bytes(latest_bytes) != expected_previous_blob
        ):
            raise ValueError(f"installed unit changed before publish: {destination.name}")

        os.replace(temp, destination)
        _fsync_directory(destination.parent)
        final_bytes, _ = _read_regular(destination, allow_missing=False)
        assert final_bytes is not None
        if git_blob_sha_bytes(final_bytes) != expected_target_blob:
            raise ValueError(f"post-update unit blob mismatch: {destination.name}")
    finally:
        if fd >= 0:
            os.close(fd)
        if temp.exists():
            temp.unlink()


def upgrade_units(
    *,
    source_tree: str | Path,
    destination: str | Path = "/etc/systemd/system",
    backup_dir: str | Path = "/opt/pio-backups/phase2-systemd",
    apply: bool = False,
) -> UnitUpgradeReport:
    report = inspect_upgrade(
        source_tree=source_tree,
        destination=destination,
    )
    if not apply:
        return report
    if not report.ready:
        raise ValueError("isolated Phase-2 unit upgrade preflight is not ready")

    source = Path(report.source_tree)
    target_dir = Path(report.destination)
    rows = report.units
    to_update = [row for row in rows if row.status == "READY_UPDATE"]

    if not to_update:
        return UnitUpgradeReport(
            source_tree=report.source_tree,
            destination=report.destination,
            backup_root=None,
            ready=True,
            upgrade_needed=False,
            installer_needed=any(
                row.status == "NOT_INSTALLED" for row in rows
            ),
            applied=True,
            units=rows,
            files_updated=0,
            daemon_reload_performed=False,
            service_control_performed=False,
            rpc_called=False,
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = Path(backup_dir).resolve() / stamp
    backup_root.mkdir(parents=True, exist_ok=False)
    backups: dict[str, Path] = {}
    updated: list[UnitUpgradeStatus] = []

    try:
        for row in to_update:
            installed = target_dir / row.name
            source_path = source / "deploy" / "systemd" / row.name

            installed_bytes, _ = _read_regular(installed, allow_missing=False)
            source_bytes, _ = _read_regular(source_path, allow_missing=False)
            assert installed_bytes is not None and source_bytes is not None
            if git_blob_sha_bytes(installed_bytes) != row.expected_previous_blob:
                raise ValueError(f"installed unit changed before backup: {row.name}")
            if git_blob_sha_bytes(source_bytes) != row.expected_target_blob:
                raise ValueError(f"reviewed source unit changed before apply: {row.name}")

            backup = backup_root / row.name
            shutil.copy2(installed, backup)
            backup_bytes, _ = _read_regular(backup, allow_missing=False)
            assert backup_bytes is not None
            if git_blob_sha_bytes(backup_bytes) != row.expected_previous_blob:
                raise ValueError(f"backup blob mismatch: {row.name}")
            backups[row.name] = backup

            try:
                _write_target(
                    destination=installed,
                    source_bytes=source_bytes,
                    expected_previous_blob=row.expected_previous_blob,
                    expected_target_blob=row.expected_target_blob,
                )
            except Exception:
                shutil.copy2(backup, installed)
                _fsync_directory(target_dir)
                raise
            updated.append(row)

        final = inspect_upgrade(
            source_tree=source,
            destination=target_dir,
        )
        if any(
            row.status not in {"ALREADY_TARGET", "NOT_INSTALLED"}
            for row in final.units
        ):
            raise ValueError("post-upgrade unit validation failed")
    except Exception:
        for row in reversed(updated):
            backup = backups.get(row.name)
            if backup is None or not backup.exists():
                continue
            shutil.copy2(backup, target_dir / row.name)
            _fsync_directory(target_dir)
        raise

    final = inspect_upgrade(
        source_tree=source,
        destination=target_dir,
    )
    return UnitUpgradeReport(
        source_tree=final.source_tree,
        destination=final.destination,
        backup_root=str(backup_root),
        ready=True,
        upgrade_needed=False,
        installer_needed=any(
            row.status == "NOT_INSTALLED" for row in final.units
        ),
        applied=True,
        units=final.units,
        files_updated=len(to_update),
        daemon_reload_performed=False,
        service_control_performed=False,
        rpc_called=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or update only the two isolated Phase-2 evidence unit "
            "files from their exact prior reviewed blobs to the exact current "
            "reviewed blobs. No daemon-reload or service control is performed."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--destination", default="/etc/systemd/system")
    parser.add_argument(
        "--backup-dir",
        default="/opt/pio-backups/phase2-systemd",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = upgrade_units(
        source_tree=args.source_tree,
        destination=args.destination,
        backup_dir=args.backup_dir,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
