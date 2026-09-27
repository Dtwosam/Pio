from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_preserved_evidence_package.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_preserved_evidence_package",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


AUTH_FALSE = {
    "production_deployment_authorized": False,
    "mutation_authorized": False,
    "service_restart_authorized": False,
    "detector_cursor_movement_authorized": False,
    "paper_timer_enable_authorized": False,
    "live_capital_authorized": False,
}


def _artifacts():
    bundle = "a" * 64
    readiness = {
        "readiness_sha256": "1" * 64,
        "private_bundle_sha256": bundle,
        "private_bundle_verified": True,
        "deployment_preflight_clean": True,
        **AUTH_FALSE,
    }
    handoff = {
        "capture_readiness_sha256": readiness["readiness_sha256"],
        "handoff_sha256": "2" * 64,
        "production_state_sha256": "3" * 64,
        "handoff_ready": True,
        "state": {"private_bundle_sha256": bundle},
        **AUTH_FALSE,
    }
    plan = {
        "handoff_sha256": handoff["handoff_sha256"],
        "handoff_state_sha256": handoff["production_state_sha256"],
        "plan_sha256": "4" * 64,
        "private_bundle_sha256": bundle,
        "plan_ready": True,
        **AUTH_FALSE,
    }
    gate = {
        "saved_handoff_sha256": handoff["handoff_sha256"],
        "saved_handoff_state_sha256": handoff["production_state_sha256"],
        "saved_plan_sha256": plan["plan_sha256"],
        "gate_sha256": "5" * 64,
        "private_bundle_sha256": bundle,
        "gate_ready": True,
        **AUTH_FALSE,
    }
    review = {
        "handoff_state_sha256": handoff["production_state_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "saved_gate_sha256": gate["gate_sha256"],
        "fresh_gate_sha256": gate["gate_sha256"],
        "fresh_gate_matches_saved": True,
        "private_bundle_sha256": bundle,
        "review_sha256": "6" * 64,
        "review_ready": True,
        **AUTH_FALSE,
    }
    return readiness, handoff, plan, gate, review


def _reseal(package):
    identity = {
        field: package[field]
        for field in MODULE.PACKAGE_FIELDS
    }
    package["package_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_stage_tools_are_exactly_pinned():
    modules = MODULE._load_reviewed_modules(ROOT)

    assert len(modules) == 5
    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_complete_lineage_produces_sealed_non_authorizing_package():
    readiness, handoff, plan, gate, review = _artifacts()

    package = MODULE.build_evidence_package_from_validated(
        readiness=readiness,
        handoff=handoff,
        plan=plan,
        gate=gate,
        review=review,
    )

    MODULE.validate_evidence_package(json.loads(json.dumps(package)))
    assert package["evidence_complete"] is True
    assert package["readiness_to_handoff_bound"] is True
    assert package["handoff_to_plan_bound"] is True
    assert package["plan_to_gate_bound"] is True
    assert package["gate_to_review_bound"] is True
    assert package["private_bundle_consistent"] is True
    assert package["all_stages_ready"] is True
    assert package["fresh_gate_matches_saved"] is True
    assert package["stale_pre_preservation_artifacts_forbidden"] is True
    assert package["mutation_authorized"] is False


def test_lineage_substitution_fails_closed_before_package_emission():
    readiness, handoff, plan, gate, review = _artifacts()
    gate["saved_plan_sha256"] = "9" * 64

    try:
        MODULE.build_evidence_package_from_validated(
            readiness=readiness,
            handoff=handoff,
            plan=plan,
            gate=gate,
            review=review,
        )
    except ValueError as exc:
        assert "plan_to_gate_bound=true" in str(exc)
    else:
        raise AssertionError("cross-lineage substitution must fail closed")


def test_rehashed_package_cannot_claim_different_plan_digest():
    readiness, handoff, plan, gate, review = _artifacts()
    package = MODULE.build_evidence_package_from_validated(
        readiness=readiness,
        handoff=handoff,
        plan=plan,
        gate=gate,
        review=review,
    )
    package["plan_sha256"] = "9" * 64
    _reseal(package)

    try:
        MODULE.validate_evidence_package(package)
    except ValueError as exc:
        assert "digest mismatch" not in str(exc) or "mismatch" in str(exc)
    else:
        raise AssertionError("rehashed plan substitution must fail closed")


def test_rehashed_package_cannot_authorize_mutation():
    readiness, handoff, plan, gate, review = _artifacts()
    package = MODULE.build_evidence_package_from_validated(
        readiness=readiness,
        handoff=handoff,
        plan=plan,
        gate=gate,
        review=review,
    )
    package["mutation_authorized"] = True
    _reseal(package)

    try:
        MODULE.validate_evidence_package(package)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_rehashed_package_cannot_drop_stale_artifact_boundary():
    readiness, handoff, plan, gate, review = _artifacts()
    package = MODULE.build_evidence_package_from_validated(
        readiness=readiness,
        handoff=handoff,
        plan=plan,
        gate=gate,
        review=review,
    )
    package["stale_pre_preservation_artifacts_forbidden"] = False
    _reseal(package)

    try:
        MODULE.validate_evidence_package(package)
    except ValueError as exc:
        assert "stale_pre_preservation_artifacts_forbidden=true" in str(exc)
    else:
        raise AssertionError("stale-artifact boundary must fail closed")


def test_source_is_evidence_only_and_has_no_production_or_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert 'parser.add_argument("--repo"' not in source
    assert "/opt/pio" not in source
    assert "subprocess" not in source
    assert "shutil" not in source
    assert "systemctl" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
