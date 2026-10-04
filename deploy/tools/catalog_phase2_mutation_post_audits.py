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
VERIFY_TOOL = TOOLS_DIR / "check_phase2_isolated_mutation_post_audit.py"
VERIFY_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_mutation_post_audit.py"
_DEFAULT_PATTERN = "*.post-audit.json"
_MAX_ARTIFACTS = 1000
_MAX_TOOL_BYTES = 4 * 1024 * 1024


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
    label = "reviewed mutation post-audit verifier"
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
    VERIFY,
    _VERIFY_TOOL_PATH_AT_LOAD,
    _VERIFY_TOOL_BYTES_AT_LOAD,
    _VERIFY_TOOL_STAT_AT_LOAD,
) = _load_captured(
    VERIFY_TOOL,
    "phase2_post_audit_catalog_verify",
)
_VERIFY_TOOL_SHA256_AT_LOAD = hashlib.sha256(
    _VERIFY_TOOL_BYTES_AT_LOAD
).hexdigest()


def _run_git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )


def _verifier_source_identity() -> tuple[str, str]:
    raw = Path(VERIFY_TOOL).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed mutation post-audit verifier is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            "reviewed mutation post-audit verifier is missing or symlinked"
        ) from exc
    if resolved != _VERIFY_TOOL_PATH_AT_LOAD:
        raise ValueError(
            "mutation post-audit verifier path changed after module load"
        )
    _assert_tool_path_stable(
        _VERIFY_TOOL_PATH_AT_LOAD,
        _VERIFY_TOOL_STAT_AT_LOAD,
        label="reviewed mutation post-audit verifier",
    )

    head = _run_git("rev-parse", "HEAD")
    if head.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = head.stdout.decode("utf-8").strip()
    if not commit:
        raise ValueError("reviewed source commit is invalid")

    historical = _run_git("show", f"{commit}:{VERIFY_TOOL_RELATIVE}")
    if historical.returncode != 0:
        raise ValueError(
            "mutation post-audit verifier is not present at reviewed source commit"
        )
    if historical.stdout != _VERIFY_TOOL_BYTES_AT_LOAD:
        raise ValueError(
            "mutation post-audit verifier bytes do not match reviewed source commit"
        )

    _assert_tool_path_stable(
        _VERIFY_TOOL_PATH_AT_LOAD,
        _VERIFY_TOOL_STAT_AT_LOAD,
        label="reviewed mutation post-audit verifier",
    )
    return commit, _VERIFY_TOOL_SHA256_AT_LOAD


@dataclass(frozen=True)
class Phase2PostAuditCatalogEntry:
    artifact_path: str
    artifact_sha256: str | None
    verification_status: str
    failure_category: str | None
    audit_source_commit: str | None
    execution_receipt_sha256: str | None
    prior_state: str | None
    current_state: str | None
    current_next_action: str | None
    current_next_tool: str | None
    current_next_mutation_flag: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase2PostAuditCatalogReport:
    artifact_directory: str
    pattern: str
    artifacts_seen: int
    artifacts_verified: int
    artifacts_failed: int
    duplicate_artifact_hashes: int
    duplicate_receipt_hashes: int
    entries: tuple[Phase2PostAuditCatalogEntry, ...]
    catalog_stable_during_scan: bool
    all_verified: bool
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _artifact_directory(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("post-audit catalog directory must not be a symlink")
    path = raw.resolve(strict=True)
    if not path.is_dir():
        raise ValueError("post-audit catalog path must be a directory")
    return path


def _candidate_files(directory: Path, pattern: str) -> tuple[Path, ...]:
    if not pattern or "/" in pattern or "\\" in pattern:
        raise ValueError("post-audit catalog pattern must be a simple filename glob")
    files = []
    for path in sorted(directory.glob(pattern), key=lambda item: item.name):
        files.append(path)
        if len(files) > _MAX_ARTIFACTS:
            raise ValueError("post-audit catalog exceeds artifact safety limit")
    return tuple(files)


def _candidate_identity(path: Path) -> tuple[str, bool, int, int, int, int, int]:
    st = path.lstat()
    return (
        path.name,
        path.is_symlink(),
        stat.S_IMODE(st.st_mode),
        int(st.st_size),
        int(st.st_mtime_ns),
        int(st.st_dev),
        int(st.st_ino),
    )


def build_phase2_post_audit_catalog(
    *,
    artifact_directory: str | Path,
    pattern: str = _DEFAULT_PATTERN,
    repository_root: str | Path = VERIFY.REPO_ROOT,
) -> Phase2PostAuditCatalogReport:
    source_before = _verifier_source_identity()
    directory = _artifact_directory(artifact_directory)
    candidates = _candidate_files(directory, pattern)
    identities_before = tuple(_candidate_identity(path) for path in candidates)
    entries: list[Phase2PostAuditCatalogEntry] = []

    artifact_hash_counts: dict[str, int] = {}
    receipt_hash_counts: dict[str, int] = {}

    for path in candidates:
        try:
            report = VERIFY.verify_saved_mutation_post_audit(
                artifact_path=path,
                repository_root=repository_root,
            )
        except (OSError, RuntimeError, ValueError):
            entries.append(
                Phase2PostAuditCatalogEntry(
                    artifact_path=str(path),
                    artifact_sha256=None,
                    verification_status="FAILED",
                    failure_category="ARTIFACT_VERIFICATION_FAILED",
                    audit_source_commit=None,
                    execution_receipt_sha256=None,
                    prior_state=None,
                    current_state=None,
                    current_next_action=None,
                    current_next_tool=None,
                    current_next_mutation_flag=None,
                )
            )
            continue

        verified = bool(report.static_audit_verified)
        artifact_sha = str(report.artifact_sha256)
        receipt_sha = str(report.execution_receipt_sha256)
        artifact_hash_counts[artifact_sha] = artifact_hash_counts.get(artifact_sha, 0) + 1
        receipt_hash_counts[receipt_sha] = receipt_hash_counts.get(receipt_sha, 0) + 1
        entries.append(
            Phase2PostAuditCatalogEntry(
                artifact_path=str(path),
                artifact_sha256=artifact_sha,
                verification_status="VERIFIED" if verified else "FAILED",
                failure_category=None if verified else "STATIC_AUDIT_NOT_VERIFIED",
                audit_source_commit=str(report.audit_source_commit),
                execution_receipt_sha256=receipt_sha,
                prior_state=str(report.prior_state),
                current_state=str(report.current_state),
                current_next_action=str(report.current_next_action),
                current_next_tool=(
                    str(report.current_next_tool)
                    if report.current_next_tool is not None
                    else None
                ),
                current_next_mutation_flag=(
                    str(report.current_next_mutation_flag)
                    if report.current_next_mutation_flag is not None
                    else None
                ),
            )
        )

    verified_count = sum(item.verification_status == "VERIFIED" for item in entries)
    failed_count = len(entries) - verified_count
    duplicate_artifacts = sum(count - 1 for count in artifact_hash_counts.values() if count > 1)
    duplicate_receipts = sum(count - 1 for count in receipt_hash_counts.values() if count > 1)

    candidates_after = _candidate_files(directory, pattern)
    identities_after = tuple(_candidate_identity(path) for path in candidates_after)
    stable = identities_after == identities_before
    if not stable:
        raise ValueError("post-audit catalog changed during verification")

    source_after = _verifier_source_identity()
    if source_after != source_before:
        raise ValueError(
            "reviewed mutation post-audit verifier source changed during catalog"
        )

    return Phase2PostAuditCatalogReport(
        artifact_directory=str(directory),
        pattern=pattern,
        artifacts_seen=len(entries),
        artifacts_verified=verified_count,
        artifacts_failed=failed_count,
        duplicate_artifact_hashes=duplicate_artifacts,
        duplicate_receipt_hashes=duplicate_receipts,
        entries=tuple(entries),
        catalog_stable_during_scan=True,
        all_verified=bool(entries) and failed_count == 0,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Catalog saved Phase-2 mutation post-audits by historically "
            "verifying each private artifact. This tool is read-only and "
            "makes no RPC calls."
        )
    )
    parser.add_argument("--artifact-directory", required=True)
    parser.add_argument("--pattern", default=_DEFAULT_PATTERN)
    parser.add_argument("--repository-root", default=str(VERIFY.REPO_ROOT))
    args = parser.parse_args()

    report = build_phase2_post_audit_catalog(
        artifact_directory=args.artifact_directory,
        pattern=args.pattern,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.all_verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
