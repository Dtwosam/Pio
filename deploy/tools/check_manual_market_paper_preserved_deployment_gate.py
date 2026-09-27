from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_DEPLOYMENT_GATE_V1"

HANDOFF_TOOL = Path("deploy/tools/manual_market_paper_preserved_handoff.py")
PLAN_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_deployment_plan.py"
)

REVIEWED_SOURCE_BLOBS = {
    HANDOFF_TOOL: "3bc9c551a5bbe17bcedfc30e165a72e21966f407",
    PLAN_TOOL: "0887331414259b22dace2ad90aa74296f1c4dd49",
}

GATE_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_handoff_sha256",
    "saved_handoff_state_sha256",
    "saved_plan_sha256",
    "private_bundle_sha256",
    "current_readiness_sha256",
    "snapshot_handoff_ready",
    "current_handoff_ready",
    "snapshot_state_sha256",
    "current_state_sha256",
    "state_matches",
    "changed_sections",
    "rebuilt_plan_matches",
    "plan_ready",
    "plan_mutation_authorized",
    "operation_count",
    "deployment_needed",
    "gate_ready",
    "requires_fresh_mutation_review",
    "requires_separate_mutation_authorization",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
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


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
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


def _verify_reviewed_source(source: Path) -> None:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"reviewed gate artifact is missing: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed gate artifact mismatch: {relative}")


def validate_preserved_gate(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("preserved deployment gate must be a JSON object")
    if set(report) != set(GATE_FIELDS) | {"gate_sha256"}:
        raise ValueError("preserved deployment gate fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preserved deployment gate format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preserved deployment gate artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("preserved deployment gate source lineage mismatch")

    for field in (
        "saved_handoff_sha256",
        "saved_handoff_state_sha256",
        "saved_plan_sha256",
        "private_bundle_sha256",
        "current_readiness_sha256",
        "snapshot_state_sha256",
        "current_state_sha256",
        "gate_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"preserved deployment gate {field} is invalid")

    for field in (
        "snapshot_handoff_ready",
        "current_handoff_ready",
        "state_matches",
        "rebuilt_plan_matches",
        "plan_ready",
        "plan_mutation_authorized",
        "deployment_needed",
        "gate_ready",
    ):
        if not isinstance(report.get(field), bool):
            raise ValueError(f"preserved deployment gate {field} must be boolean")

    changed = report.get("changed_sections")
    if (
        not isinstance(changed, list)
        or any(not isinstance(item, str) or not item for item in changed)
        or len(changed) != len(set(changed))
    ):
        raise ValueError("preserved deployment gate changed sections are invalid")

    expected_state_matches = (
        report["snapshot_state_sha256"] == report["current_state_sha256"]
    )
    if report["state_matches"] is not expected_state_matches:
        raise ValueError("preserved deployment gate state-match flag mismatch")
    if report["state_matches"] and changed:
        raise ValueError("matching preserved deployment gate state cannot report changes")
    if not report["state_matches"] and not changed:
        raise ValueError("drifted preserved deployment gate state must report changes")

    operation_count = report.get("operation_count")
    if (
        not isinstance(operation_count, int)
        or isinstance(operation_count, bool)
        or operation_count < 0
    ):
        raise ValueError("preserved deployment gate operation count is invalid")
    if report["deployment_needed"] is not (operation_count > 0):
        raise ValueError("preserved deployment gate deployment-needed mismatch")

    expected_gate_ready = bool(
        report["snapshot_handoff_ready"]
        and report["current_handoff_ready"]
        and report["state_matches"]
        and report["rebuilt_plan_matches"]
        and report["plan_ready"]
        and report["plan_mutation_authorized"] is False
    )
    if report["gate_ready"] is not expected_gate_ready:
        raise ValueError("preserved deployment gate ready flag mismatch")

    for field in (
        "requires_fresh_mutation_review",
        "requires_separate_mutation_authorization",
    ):
        if report.get(field) is not True:
            raise ValueError(f"preserved deployment gate requires {field}=true")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"preserved deployment gate requires {field}=false")

    identity = {field: report[field] for field in GATE_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["gate_sha256"] != expected_digest:
        raise ValueError("preserved deployment gate digest mismatch")


def build_preserved_gate(
    *,
    repository: str | Path,
    source_tree: str | Path,
    private_bundle_report_path: str | Path,
    saved_handoff: dict[str, Any],
    saved_plan: dict[str, Any],
    paper_account: str,
    pool: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    _verify_reviewed_source(source)

    handoff_module = _load_module(
        source / HANDOFF_TOOL,
        "manual_market_paper_preserved_gate_handoff",
    )
    plan_module = _load_module(
        source / PLAN_TOOL,
        "manual_market_paper_preserved_gate_plan",
    )
    handoff_module.validate_handoff_snapshot(saved_handoff)
    plan_module.validate_preserved_deployment_plan(saved_plan)

    if saved_handoff.get("handoff_ready") is not True:
        raise ValueError("saved preserved handoff is not ready")
    if saved_plan.get("plan_ready") is not True:
        raise ValueError("saved preserved plan is not ready")
    if saved_plan.get("mutation_authorized") is not False:
        raise ValueError("saved preserved plan must not authorize mutation")
    if saved_plan.get("handoff_sha256") != saved_handoff["handoff_sha256"]:
        raise ValueError("saved preserved plan does not bind saved handoff")
    if saved_plan.get("handoff_state_sha256") != saved_handoff[
        "production_state_sha256"
    ]:
        raise ValueError("saved preserved plan state lineage mismatch")
    if saved_plan.get("private_bundle_sha256") != saved_handoff["state"][
        "private_bundle_sha256"
    ]:
        raise ValueError("saved preserved plan private-bundle lineage mismatch")

    bundle_report = _load_json(Path(private_bundle_report_path))
    rebuilt_plan = plan_module.build_preserved_deployment_plan(
        source_tree=source,
        handoff_snapshot=saved_handoff,
        private_bundle_report=bundle_report,
    )
    rebuilt_plan_matches = rebuilt_plan == saved_plan

    current_report, tracked_diff, readiness_module = (
        handoff_module._build_current_report(
            repository=str(Path(repository).resolve()),
            source_tree=source,
            private_bundle_report=Path(private_bundle_report_path).resolve(),
            pool=pool,
            paper_account=paper_account,
        )
    )
    verification = handoff_module.compare_handoff_snapshot(
        saved_handoff,
        current_report,
        tracked_diff_sha256=tracked_diff,
        readiness_module=readiness_module,
    )
    current_handoff = handoff_module.build_handoff_snapshot(
        current_report,
        tracked_diff_sha256=tracked_diff,
        readiness_module=readiness_module,
    )

    snapshot_handoff_ready = bool(saved_handoff["handoff_ready"])
    current_handoff_ready = bool(current_handoff["handoff_ready"])
    state_matches = bool(verification["state_matches"])
    plan_ready = bool(saved_plan["plan_ready"])
    plan_mutation_authorized = bool(saved_plan["mutation_authorized"])

    gate_ready = bool(
        snapshot_handoff_ready
        and current_handoff_ready
        and state_matches
        and rebuilt_plan_matches
        and plan_ready
        and plan_mutation_authorized is False
    )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "saved_handoff_sha256": saved_handoff["handoff_sha256"],
        "saved_handoff_state_sha256": saved_handoff["production_state_sha256"],
        "saved_plan_sha256": saved_plan["plan_sha256"],
        "private_bundle_sha256": saved_plan["private_bundle_sha256"],
        "current_readiness_sha256": current_report["readiness_sha256"],
        "snapshot_handoff_ready": snapshot_handoff_ready,
        "current_handoff_ready": current_handoff_ready,
        "snapshot_state_sha256": saved_handoff["production_state_sha256"],
        "current_state_sha256": current_handoff["production_state_sha256"],
        "state_matches": state_matches,
        "changed_sections": list(verification["changed_sections"]),
        "rebuilt_plan_matches": rebuilt_plan_matches,
        "plan_ready": plan_ready,
        "plan_mutation_authorized": plan_mutation_authorized,
        "operation_count": saved_plan["operation_count"],
        "deployment_needed": saved_plan["deployment_needed"],
        "gate_ready": gate_ready,
        "requires_fresh_mutation_review": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    report = {
        **identity,
        "gate_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_preserved_gate(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run the preserved manual market/PAPER handoff against current "
            "production, rebuild the deterministic preserved plan, and seal a "
            "read-only gate only when state and plan still match. This command "
            "never writes production files, captures backups, changes services, "
            "moves cursors, enables timers, or authorizes mutation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--paper-account", required=True)
    parser.add_argument("--pool")
    args = parser.parse_args()

    report = build_preserved_gate(
        repository=args.repo,
        source_tree=args.source_tree,
        private_bundle_report_path=args.private_bundle_report,
        saved_handoff=_load_json(Path(args.handoff)),
        saved_plan=_load_json(Path(args.plan)),
        paper_account=args.paper_account,
        pool=args.pool,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["gate_ready"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
