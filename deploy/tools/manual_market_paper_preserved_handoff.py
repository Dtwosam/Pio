from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_HANDOFF_V1"
VERIFY_ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_HANDOFF_VERIFY_V1"

READINESS_TOOL = Path("deploy/tools/check_manual_market_paper_preserved_readiness.py")
EXPECTED_READINESS_TOOL_BLOB = "aec184548fa3149d42f006cd2b6dcb7d2da014f3"
EXPECTED_REVIEWED_SOURCE_BLOBS = {
    "deploy/tools/build_manual_market_paper_preserved_source_bundle.py":
        "27c2d87bab3e896d84c22cdef8bd95082ca0cad3",
    "deploy/tools/apply_phase2_collection_stack.py":
        "a8a76cd4db95867a842de93f8688daa0cd7100c3",
    "deploy/manifests/market-paper-phase2-prerequisites.json":
        "dfb1eb62241f6fc3b6351e3b40b524fb68f4a61e",
    "deploy/manifests/market-paper-manual-cycle.json":
        "1891afe2c88e267a3dd76fdcdb717a49bc94b15c",
}

STATE_FIELDS = (
    "private_bundle_sha256",
    "private_bundle_verified",
    "production_head",
    "production_head_matches_reviewed_baseline",
    "tracked_changes",
    "tracked_diff_sha256",
    "detector_service",
    "watcher_service",
    "paper_account",
    "paper_service",
    "paper_timer",
    "manual_mode_safe",
    "target_pool",
    "target_pool_cursor",
    "phase2",
    "state_reader",
    "market_paper",
    "deployment_preflight_clean",
    "runtime_files_deployed",
    "operational_services_healthy",
    "manual_paper_runtime_ready",
    "requires_preservation_aware_plan",
    "requires_separate_mutation_authorization",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

HANDOFF_FIELDS = (
    "format_version",
    "artifact_type",
    "readiness_tool_blob",
    "reviewed_source_blobs",
    "capture_readiness_sha256",
    "production_state_sha256",
    "handoff_ready",
    "deployment_needed",
    "requires_preservation_aware_plan",
    "requires_fresh_gate_verification",
    "requires_separate_mutation_authorization",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
    "state",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _load_readiness_module(source_tree: Path) -> Any:
    tool = source_tree / READINESS_TOOL
    if tool.is_symlink() or not tool.is_file():
        raise ValueError(f"reviewed preserved readiness tool is missing: {READINESS_TOOL}")
    if _git_blob_sha(tool) != EXPECTED_READINESS_TOOL_BLOB:
        raise ValueError("reviewed preserved readiness tool blob mismatch")

    spec = importlib.util.spec_from_file_location(
        "manual_market_paper_preserved_handoff_readiness",
        tool,
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load preserved readiness tool: {tool}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"JSON artifact must not be a symlink: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"JSON artifact must be a regular file: {path}")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _tracked_diff_sha256(
    repository: str | Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str:
    repo = Path(repository).resolve()
    proc = runner(
        [
            "git",
            "-c",
            f"safe.directory={repo}",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError("cannot fingerprint tracked production diff")
    stdout = proc.stdout
    if not isinstance(stdout, bytes):
        stdout = str(stdout).encode("utf-8")
    return hashlib.sha256(stdout).hexdigest()


def _state_identity(
    report: dict[str, Any],
    *,
    tracked_diff_sha256: str,
) -> dict[str, Any]:
    missing = [
        field
        for field in STATE_FIELDS
        if field not in report and field != "tracked_diff_sha256"
    ]
    if missing:
        raise ValueError(
            "preserved readiness report is missing handoff fields: "
            + ", ".join(sorted(missing))
        )

    identity: dict[str, Any] = {}
    for field in STATE_FIELDS:
        if field == "tracked_diff_sha256":
            identity[field] = tracked_diff_sha256
        else:
            identity[field] = report[field]
    return identity


def _state_digest(identity: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(identity)).hexdigest()


def _handoff_ready(identity: dict[str, Any]) -> bool:
    return bool(
        identity["paper_account"]
        and identity["private_bundle_verified"] is True
        and identity["production_head_matches_reviewed_baseline"] is True
        and identity["deployment_preflight_clean"] is True
        and identity["operational_services_healthy"] is True
        and identity["manual_mode_safe"] is True
        and identity["requires_preservation_aware_plan"] is True
        and identity["requires_separate_mutation_authorization"] is True
        and identity["production_deployment_authorized"] is False
        and identity["mutation_authorized"] is False
        and identity["service_restart_authorized"] is False
        and identity["detector_cursor_movement_authorized"] is False
        and identity["paper_timer_enable_authorized"] is False
        and identity["live_capital_authorized"] is False
    )


def build_handoff_snapshot(
    report: dict[str, Any],
    *,
    tracked_diff_sha256: str,
    readiness_module: Any,
) -> dict[str, Any]:
    readiness_module.validate_preserved_readiness(report)
    if not _is_hex_digest(tracked_diff_sha256, 64):
        raise ValueError("tracked production diff digest is invalid")

    identity = _state_identity(
        report,
        tracked_diff_sha256=tracked_diff_sha256,
    )
    ready = _handoff_ready(identity)

    if report.get("reviewed_source_blobs") != EXPECTED_REVIEWED_SOURCE_BLOBS:
        raise ValueError("preserved readiness reviewed-source lineage mismatch")

    handoff_identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "readiness_tool_blob": EXPECTED_READINESS_TOOL_BLOB,
        "reviewed_source_blobs": report["reviewed_source_blobs"],
        "capture_readiness_sha256": report["readiness_sha256"],
        "production_state_sha256": _state_digest(identity),
        "handoff_ready": ready,
        "deployment_needed": not bool(identity["runtime_files_deployed"]),
        "requires_preservation_aware_plan": True,
        "requires_fresh_gate_verification": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
        "state": identity,
    }
    snapshot = {
        **handoff_identity,
        "handoff_sha256": hashlib.sha256(
            _canonical_bytes(handoff_identity)
        ).hexdigest(),
    }
    validate_handoff_snapshot(snapshot)
    return snapshot


def validate_handoff_snapshot(snapshot: dict[str, Any]) -> None:
    if not isinstance(snapshot, dict):
        raise ValueError("preserved handoff must be a JSON object")
    if set(snapshot) != set(HANDOFF_FIELDS) | {"handoff_sha256"}:
        raise ValueError("preserved handoff fields do not match reviewed schema")
    if snapshot.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preserved handoff format")
    if snapshot.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preserved handoff artifact type")
    if snapshot.get("readiness_tool_blob") != EXPECTED_READINESS_TOOL_BLOB:
        raise ValueError("preserved handoff readiness-tool lineage mismatch")

    source_blobs = snapshot.get("reviewed_source_blobs")
    if source_blobs != EXPECTED_REVIEWED_SOURCE_BLOBS:
        raise ValueError("preserved handoff reviewed-source lineage mismatch")

    if not _is_hex_digest(snapshot.get("capture_readiness_sha256"), 64):
        raise ValueError("preserved handoff readiness digest is invalid")
    if not _is_hex_digest(snapshot.get("production_state_sha256"), 64):
        raise ValueError("preserved handoff production-state digest is invalid")
    if not _is_hex_digest(snapshot.get("handoff_sha256"), 64):
        raise ValueError("preserved handoff digest is invalid")

    state = snapshot.get("state")
    if not isinstance(state, dict) or set(state) != set(STATE_FIELDS):
        raise ValueError("preserved handoff state schema mismatch")
    if not _is_hex_digest(state.get("private_bundle_sha256"), 64):
        raise ValueError("preserved handoff private-bundle digest is invalid")
    if not _is_hex_digest(state.get("tracked_diff_sha256"), 64):
        raise ValueError("preserved handoff tracked-diff digest is invalid")

    expected_state_digest = _state_digest(state)
    if snapshot["production_state_sha256"] != expected_state_digest:
        raise ValueError("preserved handoff production-state digest mismatch")
    if snapshot.get("handoff_ready") is not _handoff_ready(state):
        raise ValueError("preserved handoff readiness flag mismatch")
    if snapshot.get("deployment_needed") is not (
        not bool(state["runtime_files_deployed"])
    ):
        raise ValueError("preserved handoff deployment-needed flag mismatch")

    for field in (
        "requires_preservation_aware_plan",
        "requires_fresh_gate_verification",
        "requires_separate_mutation_authorization",
    ):
        if snapshot.get(field) is not True:
            raise ValueError(f"preserved handoff requires {field}=true")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if snapshot.get(field) is not False:
            raise ValueError(f"preserved handoff requires {field}=false")

    identity = {field: snapshot[field] for field in HANDOFF_FIELDS}
    expected_handoff_digest = hashlib.sha256(
        _canonical_bytes(identity)
    ).hexdigest()
    if snapshot["handoff_sha256"] != expected_handoff_digest:
        raise ValueError("preserved handoff digest mismatch")


def compare_handoff_snapshot(
    snapshot: dict[str, Any],
    current_report: dict[str, Any],
    *,
    tracked_diff_sha256: str,
    readiness_module: Any,
) -> dict[str, Any]:
    validate_handoff_snapshot(snapshot)
    current = build_handoff_snapshot(
        current_report,
        tracked_diff_sha256=tracked_diff_sha256,
        readiness_module=readiness_module,
    )

    prior_state = snapshot["state"]
    current_state = current["state"]
    changed = [
        field
        for field in STATE_FIELDS
        if prior_state[field] != current_state[field]
    ]
    matches = (
        snapshot["production_state_sha256"]
        == current["production_state_sha256"]
    )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": VERIFY_ARTIFACT_TYPE,
        "snapshot_handoff_sha256": snapshot["handoff_sha256"],
        "snapshot_handoff_ready": bool(snapshot["handoff_ready"]),
        "current_handoff_ready": bool(current["handoff_ready"]),
        "state_matches": matches,
        "changed_sections": changed,
        "snapshot_state_sha256": snapshot["production_state_sha256"],
        "current_state_sha256": current["production_state_sha256"],
        "requires_preservation_aware_plan": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
    }
    return {
        **identity,
        "verify_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }


def _build_current_report(
    *,
    repository: str,
    source_tree: Path,
    private_bundle_report: Path,
    pool: str | None,
    paper_account: str,
    diff_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[dict[str, Any], str, Any]:
    repo = Path(repository).resolve()
    diff_before = _tracked_diff_sha256(repo, runner=diff_runner)

    module = _load_readiness_module(source_tree)
    kwargs: dict[str, Any] = {
        "repository": repo,
        "reviewed_source_tree": source_tree,
        "private_bundle_report_path": private_bundle_report,
        "paper_account": paper_account,
    }
    if pool is not None:
        kwargs["pool"] = pool

    report = module.build_preserved_readiness(**kwargs)
    module.validate_preserved_readiness(report)

    diff_after = _tracked_diff_sha256(repo, runner=diff_runner)
    if diff_before != diff_after:
        raise ValueError(
            "tracked production diff changed during preserved read-only readiness"
        )
    return report, diff_after, module


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture or verify a sealed, path-independent handoff for the "
            "privately preserved manual market/PAPER readiness state. The "
            "handoff binds the private bundle SHA, exact file classifications, "
            "services, cursor, production HEAD, and tracked-diff SHA. It never "
            "writes production files, restarts services, enables timers, moves "
            "detector cursors, or authorizes mutation."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--repo", default="/opt/pio")
        subparser.add_argument("--reviewed-source-tree", required=True)
        subparser.add_argument("--private-bundle-report", required=True)
        subparser.add_argument("--paper-account", required=True)
        subparser.add_argument("--pool")

    capture = subparsers.add_parser(
        "capture",
        help="Print a sealed preserved-readiness handoff to stdout.",
    )
    add_common(capture)

    verify = subparsers.add_parser(
        "verify",
        help="Re-run preserved readiness and compare with a saved handoff.",
    )
    add_common(verify)
    verify.add_argument("--snapshot", required=True)

    args = parser.parse_args()
    source_tree = Path(args.reviewed_source_tree).resolve()
    private_bundle_report = Path(args.private_bundle_report).resolve()

    current_report, tracked_diff, module = _build_current_report(
        repository=args.repo,
        source_tree=source_tree,
        private_bundle_report=private_bundle_report,
        pool=args.pool,
        paper_account=args.paper_account,
    )

    if args.command == "capture":
        result = build_handoff_snapshot(
            current_report,
            tracked_diff_sha256=tracked_diff,
            readiness_module=module,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        if not result["handoff_ready"]:
            raise SystemExit(2)
        return

    snapshot = _load_json(Path(args.snapshot))
    result = compare_handoff_snapshot(
        snapshot,
        current_report,
        tracked_diff_sha256=tracked_diff,
        readiness_module=module,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not (
        result["snapshot_handoff_ready"]
        and result["current_handoff_ready"]
        and result["state_matches"]
    ):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
