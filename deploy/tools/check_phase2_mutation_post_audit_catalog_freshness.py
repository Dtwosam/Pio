#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
SNAPSHOT_VERIFY_TOOL = TOOLS_DIR / "check_phase2_mutation_post_audit_catalog.py"
CATALOG_TOOL = TOOLS_DIR / "catalog_phase2_mutation_post_audits.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SNAPSHOT_VERIFY = _load(
    SNAPSHOT_VERIFY_TOOL,
    "phase2_post_audit_snapshot_verify_for_fresh_reverify",
)
CATALOG = _load(
    CATALOG_TOOL,
    "phase2_post_audit_catalog_for_fresh_reverify",
)


@dataclass(frozen=True)
class Phase2PostAuditCatalogFreshReverification:
    snapshot_path: str
    snapshot_sha256: str
    snapshot_verified: bool
    artifact_directory: str
    pattern: str
    snapshot_artifacts_seen: int
    live_artifacts_seen: int
    live_artifacts_verified: int
    live_artifacts_failed: int
    live_duplicate_artifact_hashes: int
    live_duplicate_receipt_hashes: int
    live_catalog_stable_during_scan: bool
    live_catalog_all_verified: bool
    evidence_identity_sets_match: bool
    missing_evidence_identities: int
    unexpected_evidence_identities: int
    artifact_paths_ignored_for_identity: bool
    artifacts_reverified: bool
    fresh_reverification_verified: bool
    authorizes_next_action: bool
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _snapshot_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "historical_snapshot_only", False)
        and not getattr(report, "authorizes_next_action", True)
        and getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "mutation_executed", True)
    )


def _catalog_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "catalog_stable_during_scan", False)
        and getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "mutation_executed", True)
    )


def _load_snapshot_catalog(snapshot_path: str | Path) -> dict[str, Any]:
    path = SNAPSHOT_VERIFY._private_json_file(snapshot_path)
    payload = SNAPSHOT_VERIFY._load_json_object(path)
    SNAPSHOT_VERIFY._assert_credential_minimal(payload)
    catalog = payload.get("catalog")
    if not isinstance(catalog, dict):
        raise ValueError("post-audit catalog snapshot catalog payload is invalid")
    entries = catalog.get("entries")
    if not isinstance(entries, list):
        raise ValueError("post-audit catalog snapshot entries are invalid")
    return catalog


def _identity(entry: dict[str, Any]) -> tuple[Any, ...]:
    return (
        entry.get("artifact_sha256"),
        entry.get("execution_receipt_sha256"),
        entry.get("audit_source_commit"),
        entry.get("prior_state"),
        entry.get("current_state"),
        entry.get("current_next_action"),
        entry.get("current_next_tool"),
        entry.get("current_next_mutation_flag"),
    )


def _snapshot_identities(catalog: dict[str, Any]) -> set[tuple[Any, ...]]:
    entries = catalog.get("entries")
    if not isinstance(entries, list):
        raise ValueError("post-audit catalog snapshot entries are invalid")
    identities: set[tuple[Any, ...]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("post-audit catalog snapshot entry is invalid")
        identities.add(_identity(entry))
    return identities


def _live_identities(report: Any) -> set[tuple[Any, ...]]:
    identities: set[tuple[Any, ...]] = set()
    for item in report.entries:
        record = item.to_record() if hasattr(item, "to_record") else dict(item)
        if record.get("verification_status") != "VERIFIED":
            continue
        identities.add(_identity(record))
    return identities


def freshly_reverify_phase2_post_audit_catalog(
    *,
    snapshot_path: str | Path,
    artifact_directory: str | Path,
    pattern: str = "*.post-audit.json",
    repository_root: str | Path = SNAPSHOT_VERIFY.REPO_ROOT,
) -> Phase2PostAuditCatalogFreshReverification:
    SNAPSHOT_VERIFY._assert_credential_minimal(str(artifact_directory))
    SNAPSHOT_VERIFY._assert_credential_minimal(pattern)

    snapshot = SNAPSHOT_VERIFY.verify_phase2_post_audit_catalog_snapshot(
        snapshot_path=snapshot_path,
        repository_root=repository_root,
    )
    if not _snapshot_boundary_ok(snapshot):
        raise ValueError(
            "post-audit catalog snapshot crossed the historical evidence boundary"
        )

    snapshot_catalog = _load_snapshot_catalog(snapshot.snapshot_path)

    live = CATALOG.build_phase2_post_audit_catalog(
        artifact_directory=artifact_directory,
        pattern=pattern,
        repository_root=repository_root,
    )
    if not _catalog_boundary_ok(live):
        raise ValueError("live post-audit catalog crossed the read-only boundary")
    SNAPSHOT_VERIFY._assert_credential_minimal(str(live.artifact_directory))
    SNAPSHOT_VERIFY._assert_credential_minimal(str(live.pattern))

    snapshot_ids = _snapshot_identities(snapshot_catalog)
    live_ids = _live_identities(live)
    missing = snapshot_ids - live_ids
    unexpected = live_ids - snapshot_ids
    identity_match = not missing and not unexpected

    fully_verified_live = bool(
        live.artifacts_seen > 0
        and live.artifacts_verified == live.artifacts_seen
        and live.artifacts_failed == 0
        and live.duplicate_artifact_hashes == 0
        and live.duplicate_receipt_hashes == 0
        and live.catalog_stable_during_scan
        and live.all_verified
    )

    fresh_verified = bool(
        snapshot.snapshot_verified
        and fully_verified_live
        and identity_match
        and int(snapshot.artifacts_seen) == int(live.artifacts_seen)
    )

    return Phase2PostAuditCatalogFreshReverification(
        snapshot_path=str(snapshot.snapshot_path),
        snapshot_sha256=str(snapshot.snapshot_sha256),
        snapshot_verified=bool(snapshot.snapshot_verified),
        artifact_directory=str(live.artifact_directory),
        pattern=str(live.pattern),
        snapshot_artifacts_seen=int(snapshot.artifacts_seen),
        live_artifacts_seen=int(live.artifacts_seen),
        live_artifacts_verified=int(live.artifacts_verified),
        live_artifacts_failed=int(live.artifacts_failed),
        live_duplicate_artifact_hashes=int(live.duplicate_artifact_hashes),
        live_duplicate_receipt_hashes=int(live.duplicate_receipt_hashes),
        live_catalog_stable_during_scan=bool(live.catalog_stable_during_scan),
        live_catalog_all_verified=bool(live.all_verified),
        evidence_identity_sets_match=identity_match,
        missing_evidence_identities=len(missing),
        unexpected_evidence_identities=len(unexpected),
        artifact_paths_ignored_for_identity=True,
        artifacts_reverified=True,
        fresh_reverification_verified=fresh_verified,
        authorizes_next_action=False,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freshly re-verify the post-audit artifacts in an explicit archive "
            "directory and compare their immutable evidence identities with a "
            "saved Phase-2 post-audit catalog snapshot. Artifact path relocation "
            "does not count as evidence drift."
        )
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--artifact-directory", required=True)
    parser.add_argument("--pattern", default="*.post-audit.json")
    parser.add_argument(
        "--repository-root",
        default=str(SNAPSHOT_VERIFY.REPO_ROOT),
    )
    args = parser.parse_args()

    report = freshly_reverify_phase2_post_audit_catalog(
        snapshot_path=args.snapshot,
        artifact_directory=args.artifact_directory,
        pattern=args.pattern,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.fresh_reverification_verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
