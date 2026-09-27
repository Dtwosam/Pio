from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_DEPLOYMENT_GATE_V1"

PLAN_TOOL = Path(
    "deploy/tools/build_manual_market_paper_deployment_plan.py"
)
HANDOFF_TOOL = Path("deploy/tools/manual_market_paper_handoff.py")

REVIEWED_SOURCE_BLOBS = {
    PLAN_TOOL: "837eb93e51b066c84dafd9808ca61bf517003836",
    HANDOFF_TOOL: "506e5b92cad25ef3990fc2e123221458159d3546",
}

GATE_IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "plan_sha256",
    "saved_handoff_state_sha256",
    "current_handoff_state_sha256",
    "state_matches",
    "changed_sections",
    "saved_handoff_ready",
    "current_handoff_ready",
    "plan_matches_saved_handoff",
    "plan_matches_reviewed_source",
    "operation_count",
    "deployment_needed",
    "gate_ready",
    "requires_separate_mutation_authorization",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)


@dataclass(frozen=True)
class DeploymentGateReport:
    format_version: int
    artifact_type: str
    plan_sha256: str
    saved_handoff_state_sha256: str
    current_handoff_state_sha256: str
    state_matches: bool
    changed_sections: tuple[str, ...]
    saved_handoff_ready: bool
    current_handoff_ready: bool
    plan_matches_saved_handoff: bool
    plan_matches_reviewed_source: bool
    operation_count: int
    deployment_needed: bool
    gate_ready: bool
    requires_separate_mutation_authorization: bool
    production_deployment_authorized: bool
    mutation_authorized: bool
    service_restart_authorized: bool
    detector_cursor_movement_authorized: bool
    paper_timer_enable_authorized: bool
    live_capital_authorized: bool
    gate_sha256: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _verify_reviewed_source(source: Path) -> None:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if not path.is_file():
            raise ValueError(f"reviewed deployment-gate artifact is missing: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"reviewed deployment-gate artifact mismatch: {relative}"
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
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _load_reviewed_modules(source: Path) -> tuple[Any, Any]:
    _verify_reviewed_source(source)
    plan_module = _load_module(
        source / PLAN_TOOL,
        "manual_market_paper_deployment_gate_plan",
    )
    handoff_module = _load_module(
        source / HANDOFF_TOOL,
        "manual_market_paper_deployment_gate_handoff",
    )
    return plan_module, handoff_module


def validate_deployment_gate_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("deployment gate report must be a JSON object")

    expected_keys = set(GATE_IDENTITY_FIELDS) | {"gate_sha256"}
    if set(report) != expected_keys:
        raise ValueError("deployment gate report fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported deployment gate report format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected deployment gate report artifact type")

    for field in (
        "plan_sha256",
        "saved_handoff_state_sha256",
        "current_handoff_state_sha256",
        "gate_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"deployment gate report {field} is invalid")

    changed_sections = report.get("changed_sections")
    if not isinstance(changed_sections, (list, tuple)):
        raise ValueError("deployment gate changed_sections must be a sequence")
    if any(not isinstance(section, str) or not section for section in changed_sections):
        raise ValueError("deployment gate changed_sections contains an invalid section")
    if len(changed_sections) != len(set(changed_sections)):
        raise ValueError("deployment gate changed_sections contains duplicates")

    bool_fields = (
        "state_matches",
        "saved_handoff_ready",
        "current_handoff_ready",
        "plan_matches_saved_handoff",
        "plan_matches_reviewed_source",
        "deployment_needed",
        "gate_ready",
        "requires_separate_mutation_authorization",
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    )
    for field in bool_fields:
        if not isinstance(report.get(field), bool):
            raise ValueError(f"deployment gate report {field} must be boolean")

    operation_count = report.get("operation_count")
    if (
        not isinstance(operation_count, int)
        or isinstance(operation_count, bool)
        or operation_count < 0
    ):
        raise ValueError("deployment gate operation_count must be a non-negative integer")
    if report["deployment_needed"] is not (operation_count > 0):
        raise ValueError("deployment gate deployment-needed flag mismatch")

    if report["state_matches"] and changed_sections:
        raise ValueError("matching deployment gate state cannot report changed sections")
    if not report["state_matches"] and not changed_sections:
        raise ValueError("drifted deployment gate state must identify changed sections")

    expected_gate_ready = bool(
        report["plan_matches_reviewed_source"]
        and report["plan_matches_saved_handoff"]
        and report["saved_handoff_ready"]
        and report["current_handoff_ready"]
        and report["state_matches"]
    )
    if report["gate_ready"] is not expected_gate_ready:
        raise ValueError("deployment gate ready flag is inconsistent with its evidence")

    if report["requires_separate_mutation_authorization"] is not True:
        raise ValueError("deployment gate must require separate mutation authorization")
    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report[field] is not False:
            raise ValueError(f"deployment gate requires {field}=false")

    identity = {
        field: report[field]
        for field in GATE_IDENTITY_FIELDS
    }
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["gate_sha256"] != expected_digest:
        raise ValueError("deployment gate report digest mismatch")


def evaluate_deployment_gate(
    *,
    source_tree: str | Path,
    saved_handoff: dict[str, Any],
    saved_plan: dict[str, Any],
    current_report: dict[str, Any],
    modules: tuple[Any, Any] | None = None,
) -> DeploymentGateReport:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")

    if modules is None:
        plan_module, handoff_module = _load_reviewed_modules(source)
    else:
        plan_module, handoff_module = modules
        _verify_reviewed_source(source)

    handoff_module.validate_handoff_snapshot(saved_handoff)
    plan_module.validate_deployment_plan(saved_plan)

    if saved_handoff.get("handoff_ready") is not True:
        raise ValueError("saved handoff is not ready")
    if saved_handoff.get("mutation_authorized") is not False:
        raise ValueError("saved handoff must not authorize mutation")
    if saved_plan.get("mutation_authorized") is not False:
        raise ValueError("saved plan must not authorize mutation")

    rebuilt_plan = plan_module.build_deployment_plan(
        source_tree=source,
        handoff_snapshot=saved_handoff,
    )
    plan_module.validate_deployment_plan(rebuilt_plan)
    plan_matches_reviewed_source = rebuilt_plan == saved_plan

    saved_state_sha = str(saved_handoff.get("production_state_sha256"))
    plan_matches_saved_handoff = (
        saved_plan.get("handoff_state_sha256") == saved_state_sha
    )

    comparison = handoff_module.compare_handoff_snapshot(
        saved_handoff,
        current_report,
    )
    current_snapshot = handoff_module.build_handoff_snapshot(current_report)
    current_state_sha = str(current_snapshot["production_state_sha256"])

    gate_ready = bool(
        plan_matches_reviewed_source
        and plan_matches_saved_handoff
        and comparison["snapshot_handoff_ready"]
        and comparison["current_handoff_ready"]
        and comparison["state_matches"]
    )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "plan_sha256": str(saved_plan["plan_sha256"]),
        "saved_handoff_state_sha256": saved_state_sha,
        "current_handoff_state_sha256": current_state_sha,
        "state_matches": bool(comparison["state_matches"]),
        "changed_sections": tuple(comparison["changed_sections"]),
        "saved_handoff_ready": bool(comparison["snapshot_handoff_ready"]),
        "current_handoff_ready": bool(comparison["current_handoff_ready"]),
        "plan_matches_saved_handoff": plan_matches_saved_handoff,
        "plan_matches_reviewed_source": plan_matches_reviewed_source,
        "operation_count": int(saved_plan["operation_count"]),
        "deployment_needed": bool(saved_plan["deployment_needed"]),
        "gate_ready": gate_ready,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    report = DeploymentGateReport(
        **identity,
        gate_sha256=hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    )
    validate_deployment_gate_report(report.to_record())
    return report


def build_live_deployment_gate(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_handoff: dict[str, Any],
    saved_plan: dict[str, Any],
) -> DeploymentGateReport:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")
    plan_module, handoff_module = _load_reviewed_modules(source)

    handoff_module.validate_handoff_snapshot(saved_handoff)
    plan_module.validate_deployment_plan(saved_plan)

    state = saved_handoff.get("state")
    if not isinstance(state, dict):
        raise ValueError("saved handoff state is missing")
    paper_account = state.get("paper_account")
    target_pool = state.get("target_pool")
    if not isinstance(paper_account, str) or not paper_account:
        raise ValueError("saved handoff paper account is missing")
    if not isinstance(target_pool, str) or not target_pool:
        raise ValueError("saved handoff target pool is missing")

    current_report = handoff_module._build_current_report(
        repository=str(repository),
        source_tree=source,
        pool=target_pool,
        paper_account=paper_account,
    )
    return evaluate_deployment_gate(
        source_tree=source,
        saved_handoff=saved_handoff,
        saved_plan=saved_plan,
        current_report=current_report,
        modules=(plan_module, handoff_module),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run the read-only manual market/PAPER production preflight and "
            "verify that a sealed handoff and saved deployment plan still match "
            "current production exactly. This gate never mutates production or "
            "authorizes deployment."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--plan", required=True)
    args = parser.parse_args()

    report = build_live_deployment_gate(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_handoff=_load_json(Path(args.handoff)),
        saved_plan=_load_json(Path(args.plan)),
    )
    print(json.dumps(report.to_record(), indent=2, sort_keys=True))
    if not report.gate_ready:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
