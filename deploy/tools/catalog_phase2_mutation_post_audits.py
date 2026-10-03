#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import stat
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
VERIFY_TOOL = TOOLS_DIR / "check_phase2_isolated_mutation_post_audit.py"
_DEFAULT_PATTERN = "*.post-audit.json"
_MAX_ARTIFACTS = 1000


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VERIFY = _load(VERIFY_TOOL, "phase2_post_audit_catalog_verify")


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


def build_phase2_post_audit_catalog(
    *,
    artifact_directory: str | Path,
    pattern: str = _DEFAULT_PATTERN,
    repository_root: str | Path = VERIFY.REPO_ROOT,
) -> Phase2PostAuditCatalogReport:
    directory = _artifact_directory(artifact_directory)
    candidates = _candidate_files(directory, pattern)
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

    return Phase2PostAuditCatalogReport(
        artifact_directory=str(directory),
        pattern=pattern,
        artifacts_seen=len(entries),
        artifacts_verified=verified_count,
        artifacts_failed=failed_count,
        duplicate_artifact_hashes=duplicate_artifacts,
        duplicate_receipt_hashes=duplicate_receipts,
        entries=tuple(entries),
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
