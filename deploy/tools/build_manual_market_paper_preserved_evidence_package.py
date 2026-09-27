from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_EVIDENCE_PACKAGE_V1"

READINESS_TOOL = Path("deploy/tools/check_manual_market_paper_preserved_readiness.py")
HANDOFF_TOOL = Path("deploy/tools/manual_market_paper_preserved_handoff.py")
PLAN_TOOL = Path("deploy/tools/build_manual_market_paper_preserved_deployment_plan.py")
GATE_TOOL = Path("deploy/tools/check_manual_market_paper_preserved_deployment_gate.py")
REVIEW_TOOL = Path("deploy/tools/build_manual_market_paper_preserved_mutation_review.py")

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "aec184548fa3149d42f006cd2b6dcb7d2da014f3",
    HANDOFF_TOOL: "3bc9c551a5bbe17bcedfc30e165a72e21966f407",
    PLAN_TOOL: "0887331414259b22dace2ad90aa74296f1c4dd49",
    GATE_TOOL: "1489160a5888285a63b14bc8b323318e934b76dd",
    REVIEW_TOOL: "f4692a8208cd213f1b95d99f55d32b5bf7b7208c",
}

AUTHORIZATION_FIELDS = (
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

PACKAGE_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "readiness_sha256",
    "handoff_sha256",
    "handoff_state_sha256",
    "plan_sha256",
    "gate_sha256",
    "mutation_review_sha256",
    "private_bundle_sha256",
    "readiness_to_handoff_bound",
    "handoff_to_plan_bound",
    "plan_to_gate_bound",
    "gate_to_review_bound",
    "private_bundle_consistent",
    "all_stages_ready",
    "fresh_gate_matches_saved",
    "stale_pre_preservation_artifacts_forbidden",
    "evidence_complete",
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
    return hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()


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


def _load_json(path: str | Path) -> dict[str, Any]:
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError(f"JSON artifact must not be a symlink: {candidate}")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"JSON artifact must be a regular file: {candidate}")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {candidate}")
    return value


def _load_reviewed_modules(source_tree: Path) -> tuple[Any, Any, Any, Any, Any]:
    modules = []
    for index, (relative, expected_blob) in enumerate(REVIEWED_SOURCE_BLOBS.items()):
        path = source_tree / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"reviewed evidence-package artifact is missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"reviewed evidence-package artifact mismatch: {relative}")
        modules.append(
            _load_module(path, f"manual_market_paper_preserved_evidence_package_{index}")
        )
    return tuple(modules)  # type: ignore[return-value]


def _all_authorization_false(value: dict[str, Any]) -> bool:
    return all(value.get(field) is False for field in AUTHORIZATION_FIELDS)


def _shared_private_bundle(
    readiness: dict[str, Any],
    handoff: dict[str, Any],
    plan: dict[str, Any],
    gate: dict[str, Any],
    review: dict[str, Any],
) -> tuple[str, bool]:
    values = [
        readiness.get("private_bundle_sha256"),
        handoff.get("state", {}).get("private_bundle_sha256")
        if isinstance(handoff.get("state"), dict)
        else None,
        plan.get("private_bundle_sha256"),
        gate.get("private_bundle_sha256"),
        review.get("private_bundle_sha256"),
    ]
    digest = values[0]
    consistent = _is_hex_digest(digest, 64) and all(value == digest for value in values)
    return str(digest), bool(consistent)


def build_evidence_package_from_validated(
    *,
    readiness: dict[str, Any],
    handoff: dict[str, Any],
    plan: dict[str, Any],
    gate: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    readiness_to_handoff = (
        handoff.get("capture_readiness_sha256") == readiness.get("readiness_sha256")
    )
    handoff_to_plan = bool(
        plan.get("handoff_sha256") == handoff.get("handoff_sha256")
        and plan.get("handoff_state_sha256") == handoff.get("production_state_sha256")
    )
    plan_to_gate = bool(
        gate.get("saved_handoff_sha256") == handoff.get("handoff_sha256")
        and gate.get("saved_handoff_state_sha256") == handoff.get("production_state_sha256")
        and gate.get("saved_plan_sha256") == plan.get("plan_sha256")
    )
    gate_to_review = bool(
        review.get("handoff_state_sha256") == handoff.get("production_state_sha256")
        and review.get("plan_sha256") == plan.get("plan_sha256")
        and review.get("saved_gate_sha256") == gate.get("gate_sha256")
    )
    private_bundle_sha256, private_bundle_consistent = _shared_private_bundle(
        readiness, handoff, plan, gate, review
    )

    all_stages_ready = bool(
        readiness.get("deployment_preflight_clean") is True
        and readiness.get("private_bundle_verified") is True
        and handoff.get("handoff_ready") is True
        and plan.get("plan_ready") is True
        and gate.get("gate_ready") is True
        and review.get("review_ready") is True
    )
    fresh_gate_matches_saved = bool(
        review.get("fresh_gate_matches_saved") is True
        and review.get("fresh_gate_sha256") == review.get("saved_gate_sha256")
        and review.get("saved_gate_sha256") == gate.get("gate_sha256")
    )
    all_authorization_false = all(
        _all_authorization_false(item)
        for item in (readiness, handoff, plan, gate, review)
    )
    evidence_complete = bool(
        readiness_to_handoff
        and handoff_to_plan
        and plan_to_gate
        and gate_to_review
        and private_bundle_consistent
        and all_stages_ready
        and fresh_gate_matches_saved
        and all_authorization_false
    )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        "readiness_sha256": readiness["readiness_sha256"],
        "handoff_sha256": handoff["handoff_sha256"],
        "handoff_state_sha256": handoff["production_state_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "gate_sha256": gate["gate_sha256"],
        "mutation_review_sha256": review["review_sha256"],
        "private_bundle_sha256": private_bundle_sha256,
        "readiness_to_handoff_bound": readiness_to_handoff,
        "handoff_to_plan_bound": handoff_to_plan,
        "plan_to_gate_bound": plan_to_gate,
        "gate_to_review_bound": gate_to_review,
        "private_bundle_consistent": private_bundle_consistent,
        "all_stages_ready": all_stages_ready,
        "fresh_gate_matches_saved": fresh_gate_matches_saved,
        "stale_pre_preservation_artifacts_forbidden": True,
        "evidence_complete": evidence_complete,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    package = {
        **identity,
        "package_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_evidence_package(package)
    return package


def validate_evidence_package(package: dict[str, Any]) -> None:
    if not isinstance(package, dict):
        raise ValueError("preserved evidence package must be a JSON object")
    if set(package) != set(PACKAGE_FIELDS) | {"package_sha256"}:
        raise ValueError("preserved evidence package fields do not match reviewed schema")
    if package.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preserved evidence package format")
    if package.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preserved evidence package artifact type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if package.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("preserved evidence package source lineage mismatch")

    for field in (
        "readiness_sha256",
        "handoff_sha256",
        "handoff_state_sha256",
        "plan_sha256",
        "gate_sha256",
        "mutation_review_sha256",
        "private_bundle_sha256",
        "package_sha256",
    ):
        if not _is_hex_digest(package.get(field), 64):
            raise ValueError(f"preserved evidence package {field} is invalid")

    required_true = (
        "readiness_to_handoff_bound",
        "handoff_to_plan_bound",
        "plan_to_gate_bound",
        "gate_to_review_bound",
        "private_bundle_consistent",
        "all_stages_ready",
        "fresh_gate_matches_saved",
        "stale_pre_preservation_artifacts_forbidden",
        "requires_separate_mutation_authorization",
    )
    for field in required_true:
        if package.get(field) is not True:
            raise ValueError(f"preserved evidence package requires {field}=true")

    expected_complete = all(package[field] is True for field in required_true[:-2])
    if package.get("evidence_complete") is not expected_complete:
        raise ValueError("preserved evidence package completeness mismatch")

    for field in AUTHORIZATION_FIELDS:
        if package.get(field) is not False:
            raise ValueError(f"preserved evidence package requires {field}=false")

    identity = {field: package[field] for field in PACKAGE_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if package["package_sha256"] != expected_digest:
        raise ValueError("preserved evidence package digest mismatch")


def build_evidence_package(
    *,
    source_tree: str | Path,
    readiness_path: str | Path,
    handoff_path: str | Path,
    plan_path: str | Path,
    gate_path: str | Path,
    review_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")

    readiness_module, handoff_module, plan_module, gate_module, review_module = (
        _load_reviewed_modules(source)
    )
    readiness = _load_json(readiness_path)
    handoff = _load_json(handoff_path)
    plan = _load_json(plan_path)
    gate = _load_json(gate_path)
    review = _load_json(review_path)

    readiness_module.validate_preserved_readiness(readiness)
    handoff_module.validate_handoff_snapshot(handoff)
    plan_module.validate_preserved_deployment_plan(plan)
    gate_module.validate_preserved_gate(gate)
    review_module.validate_preserved_mutation_review(review)

    return build_evidence_package_from_validated(
        readiness=readiness,
        handoff=handoff,
        plan=plan,
        gate=gate,
        review=review,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and seal the complete preservation-aware read-only PAPER "
            "evidence lineage: readiness, handoff, deployment plan, gate, and "
            "mutation review. The tool is evidence-only and performs no "
            "production access or mutation."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--readiness", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--gate", required=True)
    parser.add_argument("--review", required=True)
    args = parser.parse_args()

    package = build_evidence_package(
        source_tree=args.source_tree,
        readiness_path=args.readiness,
        handoff_path=args.handoff,
        plan_path=args.plan,
        gate_path=args.gate,
        review_path=args.review,
    )
    print(json.dumps(package, indent=2, sort_keys=True))
    if not package["evidence_complete"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
