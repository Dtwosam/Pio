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
BUNDLE_VERIFY_TOOL = (
    TOOLS_DIR / "check_phase2_mutation_post_audit_handoff_bundle.py"
)
LIFECYCLE_TOOL = TOOLS_DIR / "check_phase2_isolated_lifecycle_handoff.py"


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
    "phase2_portable_bundle_for_current_handoff",
)
LIFECYCLE = _load(
    LIFECYCLE_TOOL,
    "phase2_current_lifecycle_for_portable_bundle",
)

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Phase2PortableBundleCurrentHandoff:
    bundle_directory: str
    bundle_sha256: str
    bundle_verified: bool
    post_audits_verified: int
    historical_handoff_only: bool
    historical_authorizes_next_action: bool
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
    bundle_influenced_current_action: bool
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


def _lifecycle_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
    )


def inspect_phase2_portable_bundle_current_handoff(
    *,
    bundle_directory: str | Path,
    repository_root: str | Path = BUNDLE.HANDOFF_VERIFY.REPO_ROOT,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    max_receipt_age_seconds: int = 1800,
    source_tree: str | Path | None = None,
    repository_url: str = LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    runner: SystemctlRunner = subprocess.run,
) -> Phase2PortableBundleCurrentHandoff:
    bundle = BUNDLE.verify_phase2_portable_handoff_bundle(
        bundle_directory=bundle_directory,
        repository_root=repository_root,
    )
    if not _bundle_boundary_ok(bundle):
        raise ValueError(
            "portable Phase-2 evidence bundle crossed the historical non-authorizing boundary"
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

    blockers = list(str(item) for item in lifecycle.blockers)
    evidence_verified = bool(bundle.bundle_verified)
    if not evidence_verified:
        blockers.insert(0, "PORTABLE_BUNDLE_NOT_VERIFIED")

    return Phase2PortableBundleCurrentHandoff(
        bundle_directory=str(bundle.bundle_directory),
        bundle_sha256=str(bundle.bundle_sha256),
        bundle_verified=bool(bundle.bundle_verified),
        post_audits_verified=int(bundle.post_audits_verified),
        historical_handoff_only=True,
        historical_authorizes_next_action=False,
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
        bundle_influenced_current_action=False,
        next_action_source="CURRENT_LIFECYCLE_HANDOFF",
        attention_required=bool(
            not evidence_verified or lifecycle.attention_required
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
            "Verify a portable historical Phase-2 evidence bundle and independently "
            "report the current zero-RPC lifecycle next step. Historical evidence "
            "never derives or authorizes the current action."
        )
    )
    parser.add_argument("--bundle-directory", required=True)
    parser.add_argument(
        "--repository-root",
        default=str(BUNDLE.HANDOFF_VERIFY.REPO_ROOT),
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

    report = inspect_phase2_portable_bundle_current_handoff(
        bundle_directory=args.bundle_directory,
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
