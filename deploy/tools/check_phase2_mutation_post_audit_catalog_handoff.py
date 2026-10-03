#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
CATALOG_VERIFY_TOOL = TOOLS_DIR / "check_phase2_mutation_post_audit_catalog.py"
CATALOG_FRESHNESS_TOOL = (
    TOOLS_DIR / "check_phase2_mutation_post_audit_catalog_freshness.py"
)
LIFECYCLE_HANDOFF_TOOL = TOOLS_DIR / "check_phase2_isolated_lifecycle_handoff.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CATALOG_VERIFY = _load(
    CATALOG_VERIFY_TOOL,
    "phase2_post_audit_catalog_snapshot_verify_for_handoff",
)
CATALOG_FRESHNESS = _load(
    CATALOG_FRESHNESS_TOOL,
    "phase2_post_audit_catalog_freshness_for_handoff",
)
LIFECYCLE = _load(
    LIFECYCLE_HANDOFF_TOOL,
    "phase2_current_lifecycle_for_post_audit_handoff",
)

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Phase2PostAuditCatalogHandoffReport:
    snapshot_path: str
    snapshot_sha256: str
    snapshot_verified: bool
    historical_artifacts_seen: int
    historical_artifacts_verified: int
    historical_catalog_source_commit: str
    historical_snapshot_only: bool
    historical_authorizes_next_action: bool
    fresh_reverification_requested: bool
    artifacts_reverified: bool
    fresh_reverification_verified: bool | None
    fresh_artifact_directory: str | None
    current_state: str
    current_next_action: str
    current_next_tool: str | None
    current_next_parameters: dict[str, Any]
    current_next_mutation_flag: str | None
    current_attention_required: bool
    current_blockers: tuple[str, ...]
    provider_rate_limit_incident: bool
    provider_rate_limit_paused: bool
    evidence_lineage_verified: bool
    snapshot_influenced_current_action: bool
    next_action_source: str
    attention_required: bool
    blockers: tuple[str, ...]
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
        getattr(report, "historical_snapshot_only", False)
        and not getattr(report, "authorizes_next_action", True)
        and getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "mutation_executed", True)
    )


def _freshness_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "artifacts_reverified", False)
        and not getattr(report, "authorizes_next_action", True)
        and getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "mutation_executed", True)
    )


def _lifecycle_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
    )


def inspect_phase2_post_audit_catalog_handoff(
    *,
    snapshot_path: str | Path,
    artifact_directory: str | Path | None = None,
    artifact_pattern: str = "*.post-audit.json",
    repository_root: str | Path = CATALOG_VERIFY.REPO_ROOT,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    max_receipt_age_seconds: int = 1800,
    source_tree: str | Path | None = None,
    repository_url: str = LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    runner: SystemctlRunner = subprocess.run,
) -> Phase2PostAuditCatalogHandoffReport:
    snapshot = CATALOG_VERIFY.verify_phase2_post_audit_catalog_snapshot(
        snapshot_path=snapshot_path,
        repository_root=repository_root,
    )
    if not _snapshot_boundary_ok(snapshot):
        raise ValueError(
            "historical post-audit catalog snapshot crossed the evidence-only boundary"
        )

    freshness = None
    fresh_requested = artifact_directory is not None
    if fresh_requested:
        freshness = CATALOG_FRESHNESS.freshly_reverify_phase2_post_audit_catalog(
            snapshot_path=snapshot_path,
            artifact_directory=artifact_directory,
            pattern=artifact_pattern,
            repository_root=repository_root,
        )
        if not _freshness_boundary_ok(freshness):
            raise ValueError(
                "fresh post-audit catalog verification crossed the read-only boundary"
            )

    lifecycle = LIFECYCLE.inspect_lifecycle_handoff(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
        source_tree=source_tree,
        repository_url=repository_url,
        runner=runner,
    )
    if not _lifecycle_boundary_ok(lifecycle):
        raise ValueError(
            "current Phase-2 lifecycle handoff crossed the read-only boundary"
        )

    blockers: list[str] = []
    if not bool(snapshot.snapshot_verified):
        blockers.append("HISTORICAL_CATALOG_SNAPSHOT_NOT_VERIFIED")
    if freshness is not None and not bool(
        freshness.fresh_reverification_verified
    ):
        blockers.append("FRESH_ARCHIVE_REVERIFICATION_FAILED")
    blockers.extend(str(item) for item in lifecycle.blockers)

    evidence_verified = bool(
        snapshot.snapshot_verified
        and (
            freshness is None
            or freshness.fresh_reverification_verified
        )
    )

    return Phase2PostAuditCatalogHandoffReport(
        snapshot_path=str(snapshot.snapshot_path),
        snapshot_sha256=str(snapshot.snapshot_sha256),
        snapshot_verified=bool(snapshot.snapshot_verified),
        historical_artifacts_seen=int(snapshot.artifacts_seen),
        historical_artifacts_verified=int(snapshot.artifacts_verified),
        historical_catalog_source_commit=str(snapshot.catalog_source_commit),
        historical_snapshot_only=bool(snapshot.historical_snapshot_only),
        historical_authorizes_next_action=bool(snapshot.authorizes_next_action),
        fresh_reverification_requested=fresh_requested,
        artifacts_reverified=bool(
            freshness.artifacts_reverified
            if freshness is not None
            else False
        ),
        fresh_reverification_verified=(
            bool(freshness.fresh_reverification_verified)
            if freshness is not None
            else None
        ),
        fresh_artifact_directory=(
            str(freshness.artifact_directory)
            if freshness is not None
            else None
        ),
        current_state=str(lifecycle.state),
        current_next_action=str(lifecycle.next_action),
        current_next_tool=(
            str(lifecycle.next_tool)
            if lifecycle.next_tool is not None
            else None
        ),
        current_next_parameters=dict(lifecycle.next_parameters),
        current_next_mutation_flag=(
            str(lifecycle.next_mutation_flag)
            if lifecycle.next_mutation_flag is not None
            else None
        ),
        current_attention_required=bool(lifecycle.attention_required),
        current_blockers=tuple(str(item) for item in lifecycle.blockers),
        provider_rate_limit_incident=bool(
            lifecycle.provider_rate_limit_incident
        ),
        provider_rate_limit_paused=bool(
            lifecycle.provider_rate_limit_paused
        ),
        evidence_lineage_verified=evidence_verified,
        snapshot_influenced_current_action=False,
        next_action_source="CURRENT_LIFECYCLE_HANDOFF",
        attention_required=bool(
            not evidence_verified
            or lifecycle.attention_required
        ),
        blockers=tuple(blockers),
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
            "Combine a historically verified Phase-2 post-audit catalog snapshot "
            "with the current zero-RPC lifecycle handoff without allowing "
            "historical evidence to authorize or derive the current next action."
        )
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--artifact-directory",
        help=(
            "Optional current post-audit archive directory. When provided, "
            "every artifact is freshly historically re-verified and its "
            "evidence identity must match the immutable snapshot."
        ),
    )
    parser.add_argument(
        "--artifact-pattern",
        default="*.post-audit.json",
    )
    parser.add_argument(
        "--repository-root",
        default=str(CATALOG_VERIFY.REPO_ROOT),
    )
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument(
        "--receipt-path",
        default="/opt/pio/data/phase2-isolated-smoke-receipt.json",
    )
    parser.add_argument("--max-receipt-age-seconds", type=int, default=1800)
    parser.add_argument("--source-tree")
    parser.add_argument(
        "--repository-url",
        default=LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    args = parser.parse_args()

    report = inspect_phase2_post_audit_catalog_handoff(
        snapshot_path=args.snapshot,
        artifact_directory=args.artifact_directory,
        artifact_pattern=args.artifact_pattern,
        repository_root=args.repository_root,
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt_path,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
        source_tree=args.source_tree,
        repository_url=args.repository_url,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.attention_required:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
