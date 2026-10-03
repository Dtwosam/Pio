#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
SNAPSHOT_VERIFY_TOOL = (
    TOOLS_DIR
    / "check_phase2_mutation_post_audit_handoff_bundle_archive_handoff_snapshot.py"
)
ARCHIVE_VERIFY_TOOL = (
    TOOLS_DIR
    / "check_phase2_mutation_post_audit_handoff_bundle_archive.py"
)


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SNAPSHOT = _load(
    SNAPSHOT_VERIFY_TOOL,
    "phase2_archive_handoff_snapshot_for_fresh_verify",
)
ARCHIVE = _load(
    ARCHIVE_VERIFY_TOOL,
    "phase2_portable_archive_for_handoff_fresh_verify",
)


@dataclass(frozen=True)
class Phase2PortableArchiveHandoffFreshReverification:
    snapshot_path: str
    snapshot_sha256: str
    snapshot_verified: bool
    archive_path: str
    archive_sha256: str
    archive_verified: bool
    snapshot_archive_sha256: str
    source_bundle_sha256: str
    snapshot_source_bundle_sha256: str
    archive_identity_matches: bool
    source_bundle_identity_matches: bool
    archive_paths_ignored_for_identity: bool
    archive_reverified: bool
    fresh_reverification_verified: bool
    recorded_current_state: str
    recorded_current_next_action: str
    recorded_current_next_tool: str | None
    recorded_current_next_mutation_flag: str | None
    historical_handoff_only: bool
    current_lifecycle_rechecked: bool
    authorizes_next_action: bool
    requires_fresh_separate_mutation_authorization: bool
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _snapshot_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "handoff_snapshot_verified", False)
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


def _archive_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "archive_verified", False)
        and getattr(report, "source_bundle_verified", False)
        and getattr(report, "deterministic_metadata_valid", False)
        and getattr(report, "temporary_extraction_performed", False)
        and not getattr(report, "authorizes_next_action", True)
        and getattr(
            report,
            "requires_fresh_separate_mutation_authorization",
            False,
        )
        and not getattr(report, "production_file_modified", True)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "mutation_executed", True)
    )


def _capture_snapshot_identity(
    snapshot_path: str | Path,
) -> tuple[Path, str, Any]:
    path = SNAPSHOT._private_json_file(snapshot_path)
    payload, opened_stat = SNAPSHOT._read_snapshot_bytes(path)
    return path, hashlib.sha256(payload).hexdigest(), opened_stat


def _capture_archive_identity(
    archive_path: str | Path,
) -> tuple[Path, str, int, Any]:
    path = ARCHIVE._archive_file(archive_path)
    payload, opened_stat = ARCHIVE._read_archive_snapshot(path)
    return path, hashlib.sha256(payload).hexdigest(), len(payload), opened_stat


def freshly_reverify_phase2_portable_archive_handoff(
    *,
    snapshot_path: str | Path,
    archive_path: str | Path,
    repository_root: str | Path = SNAPSHOT.REPO_ROOT,
) -> Phase2PortableArchiveHandoffFreshReverification:
    SNAPSHOT._assert_credential_minimal(str(archive_path))

    (
        captured_snapshot_path,
        captured_snapshot_sha256,
        captured_snapshot_stat,
    ) = _capture_snapshot_identity(snapshot_path)
    (
        captured_archive_path,
        captured_archive_sha256,
        captured_archive_size,
        captured_archive_stat,
    ) = _capture_archive_identity(archive_path)

    snapshot = SNAPSHOT.verify_phase2_portable_archive_current_handoff_snapshot(
        snapshot_path=captured_snapshot_path,
        repository_root=repository_root,
    )
    if str(snapshot.snapshot_sha256) != captured_snapshot_sha256:
        raise ValueError(
            "archive handoff snapshot child verification does not match "
            "the orchestrator byte snapshot"
        )
    if not _snapshot_boundary_ok(snapshot):
        raise ValueError(
            "archive handoff snapshot crossed the historical evidence boundary"
        )

    archive = ARCHIVE.verify_phase2_portable_bundle_archive(
        archive_path=captured_archive_path,
        repository_root=repository_root,
    )
    if (
        str(archive.archive_sha256) != captured_archive_sha256
        or int(archive.archive_size) != captured_archive_size
    ):
        raise ValueError(
            "portable Phase-2 archive child verification does not match "
            "the orchestrator byte snapshot"
        )
    if not _archive_boundary_ok(archive):
        raise ValueError(
            "portable Phase-2 archive crossed the historical evidence boundary"
        )

    archive_identity_matches = bool(
        str(snapshot.archive_sha256) == str(archive.archive_sha256)
    )
    source_bundle_identity_matches = bool(
        str(snapshot.source_bundle_sha256)
        == str(archive.source_bundle_sha256)
    )
    fresh_verified = bool(
        snapshot.handoff_snapshot_verified
        and archive.archive_verified
        and archive.source_bundle_verified
        and archive_identity_matches
        and source_bundle_identity_matches
    )

    SNAPSHOT._assert_snapshot_path_stable(
        captured_snapshot_path,
        captured_snapshot_stat,
    )
    ARCHIVE._assert_archive_path_stable(
        captured_archive_path,
        captured_archive_stat,
    )

    return Phase2PortableArchiveHandoffFreshReverification(
        snapshot_path=str(snapshot.snapshot_path),
        snapshot_sha256=str(snapshot.snapshot_sha256),
        snapshot_verified=bool(snapshot.handoff_snapshot_verified),
        archive_path=str(archive.archive_path),
        archive_sha256=str(archive.archive_sha256),
        archive_verified=bool(archive.archive_verified),
        snapshot_archive_sha256=str(snapshot.archive_sha256),
        source_bundle_sha256=str(archive.source_bundle_sha256),
        snapshot_source_bundle_sha256=str(snapshot.source_bundle_sha256),
        archive_identity_matches=archive_identity_matches,
        source_bundle_identity_matches=source_bundle_identity_matches,
        archive_paths_ignored_for_identity=True,
        archive_reverified=True,
        fresh_reverification_verified=fresh_verified,
        recorded_current_state=str(snapshot.recorded_current_state),
        recorded_current_next_action=str(
            snapshot.recorded_current_next_action
        ),
        recorded_current_next_tool=(
            str(snapshot.recorded_current_next_tool)
            if snapshot.recorded_current_next_tool is not None
            else None
        ),
        recorded_current_next_mutation_flag=(
            str(snapshot.recorded_current_next_mutation_flag)
            if snapshot.recorded_current_next_mutation_flag is not None
            else None
        ),
        historical_handoff_only=True,
        current_lifecycle_rechecked=False,
        authorizes_next_action=False,
        requires_fresh_separate_mutation_authorization=True,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshly reverify a portable Phase-2 bundle archive against an "
            "immutable archive + lifecycle handoff snapshot. Archive relocation "
            "is ignored; archive and source-bundle identity drift fail closed. "
            "The recorded lifecycle is not rerun or authorized."
        )
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument(
        "--repository-root",
        default=str(SNAPSHOT.REPO_ROOT),
    )
    args = parser.parse_args()

    report = freshly_reverify_phase2_portable_archive_handoff(
        snapshot_path=args.snapshot,
        archive_path=args.archive,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.fresh_reverification_verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
