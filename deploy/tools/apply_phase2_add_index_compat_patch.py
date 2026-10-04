#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any


PATCH_PATH = (
    Path(__file__).resolve().parents[1]
    / "patches"
    / "phase2-production-add-index-compat.patch"
)
EXPECTED_PATCH_BLOB = "2b494423d864afa2610043774afaf55022f88a83"
_MAX_PATCH_BYTES = 4 * 1024 * 1024

FILE_CONTRACT = {
    "python-learner/src/meteora_learner/calibration_queue.py": (
        "d22786a5d4f40c993b1f818aecd2cc777820c832",
        "d0c5721cac805dbd80ecacef21f81ac654185212",
    ),
    "python-learner/src/meteora_learner/composition_prestate.py": (
        "feb13b8ea90af0875f85f6a4b955ee145193d4e4",
        "85ab78d188fec772278771bfcb1091cab8c3304c",
    ),
    "python-learner/src/meteora_learner/research_store.py": (
        "c9b9de5838d95a86bddffa6166b4d7a62e91cf16",
        "d3ffb8815e6efa949b6bf7a6f33ba60099f68357",
    ),
}


@dataclass(frozen=True)
class CapturedPatch:
    path: Path
    encoded: bytes
    opened: os.stat_result
    blob_sha1: str
    sha256: str


@dataclass(frozen=True)
class CompatFile:
    path: str
    expected_base_blob: str
    expected_target_blob: str
    current_blob: str | None
    status: str


@dataclass(frozen=True)
class CompatPatchReport:
    source_tree: str
    patch: str
    patch_blob_sha1: str
    patch_sha256: str
    status: str
    ready: bool
    applied: bool
    files: tuple[CompatFile, ...]
    production_tree_modified: bool
    service_control_performed: bool
    rpc_called: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _git_blob_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def git_blob_sha(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    payload = path.read_bytes()
    return _git_blob_bytes(payload)


def _assert_patch_path_stable(patch: CapturedPatch) -> None:
    try:
        current = os.stat(patch.path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("reviewed compatibility patch path changed after capture") from exc
    before = patch.opened
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != before.st_dev
        or current.st_ino != before.st_ino
        or current.st_mode != before.st_mode
        or current.st_size != before.st_size
        or current.st_mtime_ns != before.st_mtime_ns
        or current.st_ctime_ns != before.st_ctime_ns
    ):
        raise ValueError("reviewed compatibility patch path changed after capture")


def _capture_patch() -> CapturedPatch:
    raw = Path(PATCH_PATH).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed compatibility patch must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            f"reviewed compatibility patch is missing: {raw}"
        ) from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        raise ValueError(
            "reviewed compatibility patch cannot be opened safely"
        ) from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("reviewed compatibility patch must be a regular file")
        if before.st_size <= 0 or before.st_size > _MAX_PATCH_BYTES:
            raise ValueError("reviewed compatibility patch size is invalid")

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
            raise ValueError("reviewed compatibility patch changed while reading")
    finally:
        os.close(fd)

    if len(encoded) != before.st_size:
        raise ValueError("reviewed compatibility patch changed while reading")

    captured = CapturedPatch(
        path=resolved,
        encoded=encoded,
        opened=before,
        blob_sha1=_git_blob_bytes(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )
    _assert_patch_path_stable(captured)
    if captured.blob_sha1 != EXPECTED_PATCH_BLOB:
        raise ValueError(
            "reviewed compatibility patch bytes do not match pinned Git blob"
        )
    return captured


def _source_tree(value: str | Path) -> Path:
    source = Path(value).resolve()
    if source == Path("/opt/pio").resolve():
        raise ValueError("compatibility patch must not be applied directly to /opt/pio")
    if not source.is_dir():
        raise ValueError(f"source tree is missing: {source}")
    return source


def _inspect(source: Path) -> tuple[CompatFile, ...]:
    rows = []
    for relative, (base_blob, target_blob) in FILE_CONTRACT.items():
        path = source / relative
        if path.is_symlink():
            status = "SOURCE_SYMLINK"
            current = None
        elif not path.is_file():
            status = "SOURCE_MISSING"
            current = None
        else:
            current = git_blob_sha(path)
            if current == target_blob:
                status = "ALREADY_TARGET"
            elif current == base_blob:
                status = "READY_APPLY"
            else:
                status = "SOURCE_DRIFT"
        rows.append(
            CompatFile(
                path=relative,
                expected_base_blob=base_blob,
                expected_target_blob=target_blob,
                current_blob=current,
                status=status,
            )
        )
    return tuple(rows)


def _git_apply(
    source: Path,
    patch_bytes: bytes,
    *,
    check: bool,
) -> None:
    command = ["git", "apply"]
    if check:
        command.append("--check")
    command.extend(["--whitespace=error-all", "-"])
    proc = subprocess.run(
        command,
        cwd=str(source),
        input=patch_bytes,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        mode = "preflight" if check else "apply"
        raise ValueError(f"compatibility patch {mode} failed")


def _report(
    *,
    source: Path,
    patch: CapturedPatch,
    status: str,
    ready: bool,
    applied: bool,
    files: tuple[CompatFile, ...],
) -> CompatPatchReport:
    return CompatPatchReport(
        source_tree=str(source),
        patch=str(patch.path),
        patch_blob_sha1=patch.blob_sha1,
        patch_sha256=patch.sha256,
        status=status,
        ready=ready,
        applied=applied,
        files=files,
        production_tree_modified=False,
        service_control_performed=False,
        rpc_called=False,
    )


def evaluate(*, source_tree: str | Path, apply: bool = False) -> CompatPatchReport:
    source = _source_tree(source_tree)
    patch = _capture_patch()
    files = _inspect(source)
    statuses = {item.status for item in files}

    if statuses == {"ALREADY_TARGET"}:
        _assert_patch_path_stable(patch)
        return _report(
            source=source,
            patch=patch,
            status="ALREADY_TARGET",
            ready=True,
            applied=apply,
            files=files,
        )

    if statuses != {"READY_APPLY"}:
        _assert_patch_path_stable(patch)
        return _report(
            source=source,
            patch=patch,
            status="SOURCE_DRIFT",
            ready=False,
            applied=False,
            files=files,
        )

    _assert_patch_path_stable(patch)
    _git_apply(source, patch.encoded, check=True)
    _assert_patch_path_stable(patch)
    if not apply:
        return _report(
            source=source,
            patch=patch,
            status="READY_APPLY",
            ready=True,
            applied=False,
            files=files,
        )

    _git_apply(source, patch.encoded, check=False)
    _assert_patch_path_stable(patch)
    updated = _inspect(source)
    if {item.status for item in updated} != {"ALREADY_TARGET"}:
        raise ValueError("post-apply compatibility blob verification failed")
    return _report(
        source=source,
        patch=patch,
        status="APPLIED",
        ready=True,
        applied=True,
        files=updated,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the production-derived Phase-2 add-index compatibility "
            "patch only to an isolated reviewed source tree."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = evaluate(source_tree=args.source_tree, apply=args.apply)
    print(json.dumps(report.to_record(), indent=2))
    if not report.ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
