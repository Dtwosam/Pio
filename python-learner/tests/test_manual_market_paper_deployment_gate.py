from __future__ import annotations

from collections import Counter
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
    / "check_manual_market_paper_deployment_gate.py"
)

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_deployment_gate",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

PLAN, HANDOFF = MODULE._load_reviewed_modules(ROOT)
PREREQUISITE = json.loads(
    (ROOT / PLAN.PREREQUISITE_MANIFEST).read_text(encoding="utf-8")
)
RUNTIME = json.loads(
    (ROOT / PLAN.MANUAL_RUNTIME_MANIFEST).read_text(encoding="utf-8")
)


def _overlay(manifest, pending=None):
    pending = dict(pending or {})
    counts = Counter()
    pending_records = []
    for path in manifest["deployment_files"]:
        status = pending.get(path, "ALREADY_TARGET")
        counts[status] += 1
        if status in PLAN.PENDING_STATUSES:
            pending_records.append({"path": path, "status": status})
    return {
        "content_ready": all(
            status in PLAN.READY_STATUSES
            for status in counts
        ),
        "deployed": counts.get("ALREADY_TARGET", 0) == len(
            manifest["deployment_files"]
        ),
        "apply_authorized": False,
        "files_changed": len(pending_records),
        "status_counts": dict(sorted(counts.items())),
        "pending": pending_records,
        "nonready": [],
    }


def _report(
    *,
    cursor="cursor",
    phase2=None,
    market_paper=None,
    state_reader=None,
):
    phase2 = phase2 or _overlay(PREREQUISITE)
    market_paper = market_paper or _overlay(RUNTIME)
    state_reader = state_reader or {
        "preflight_ready": True,
        "deployed": True,
        "status": "ALREADY_TARGET",
        "error_category": None,
    }
    runtime_files_deployed = (
        phase2["deployed"]
        and market_paper["deployed"]
        and state_reader["deployed"]
    )
    return {
        "repository": "/opt/pio",
        "source_tree": "/var/tmp/reviewed",
        "production_head": "abc123",
        "tracked_changes": 3,
        "tracked_diff_sha256": "0" * 64,
        "detector_service": "active",
        "watcher_service": "active",
        "paper_account": "paper-proof",
        "paper_service": "inactive",
        "paper_timer": "inactive",
        "manual_mode_safe": True,
        "target_pool": "pool",
        "target_pool_cursor": cursor,
        "phase2": phase2,
        "state_reader": state_reader,
        "market_paper": market_paper,
        "deployment_preflight_clean": True,
        "runtime_files_deployed": runtime_files_deployed,
        "operational_services_healthy": True,
        "manual_paper_runtime_ready": runtime_files_deployed,
        "mutation_authorized": False,
    }


def _artifacts(report):
    handoff = HANDOFF.build_handoff_snapshot(report)
    plan = PLAN.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=handoff,
    )
    PLAN.validate_deployment_plan(plan)
    return handoff, plan


def test_reviewed_gate_source_artifacts_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_exact_current_state_passes_read_only_gate_without_authorizing_mutation():
    report = _report(
        phase2=_overlay(
            PREREQUISITE,
            {
                "python-learner/src/meteora_learner/storage.py": (
                    "READY_UPDATE"
                )
            },
        )
    )
    handoff, plan = _artifacts(report)

    gate = MODULE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        current_report=copy.deepcopy(report),
    )

    assert gate.gate_ready is True
    assert gate.state_matches is True
    assert gate.changed_sections == ()
    assert gate.saved_handoff_ready is True
    assert gate.current_handoff_ready is True
    assert gate.plan_matches_saved_handoff is True
    assert gate.plan_matches_reviewed_source is True
    assert gate.operation_count == 1
    assert gate.deployment_needed is True
    assert gate.requires_separate_mutation_authorization is True
    assert gate.production_deployment_authorized is False
    assert gate.mutation_authorized is False
    assert gate.service_restart_authorized is False
    assert gate.detector_cursor_movement_authorized is False
    assert gate.paper_timer_enable_authorized is False
    assert gate.live_capital_authorized is False

    record = json.loads(json.dumps(gate.to_record()))
    MODULE.validate_deployment_gate_report(record)
    assert len(record["gate_sha256"]) == 64


def test_cursor_drift_fails_gate_even_when_current_handoff_is_otherwise_ready():
    saved_report = _report()
    handoff, plan = _artifacts(saved_report)
    current = _report(cursor="different-cursor")

    gate = MODULE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        current_report=current,
    )

    assert gate.gate_ready is False
    assert gate.state_matches is False
    assert gate.changed_sections == ("target_pool_cursor",)
    assert gate.current_handoff_ready is True
    assert gate.mutation_authorized is False


def test_paper_unit_drift_fails_current_handoff_readiness():
    saved_report = _report()
    handoff, plan = _artifacts(saved_report)
    current = copy.deepcopy(saved_report)
    current["paper_timer"] = "active"
    current["manual_mode_safe"] = False
    current["manual_paper_runtime_ready"] = False

    gate = MODULE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        current_report=current,
    )

    assert gate.gate_ready is False
    assert gate.state_matches is False
    assert gate.current_handoff_ready is False
    assert "paper_timer" in gate.changed_sections
    assert "manual_mode_safe" in gate.changed_sections


def test_plan_built_for_different_handoff_fails_gate_without_mutating():
    first_report = _report(cursor="first")
    second_report = _report(cursor="second")
    first_handoff, first_plan = _artifacts(first_report)
    second_handoff = HANDOFF.build_handoff_snapshot(second_report)

    gate = MODULE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=second_handoff,
        saved_plan=first_plan,
        current_report=copy.deepcopy(second_report),
    )

    assert gate.gate_ready is False
    assert gate.state_matches is True
    assert gate.plan_matches_saved_handoff is False
    assert gate.plan_matches_reviewed_source is False
    assert gate.mutation_authorized is False


def test_tampered_saved_plan_is_rejected_before_gate_evaluation():
    report = _report()
    handoff, plan = _artifacts(report)
    plan["mutation_authorized"] = True

    try:
        MODULE.evaluate_deployment_gate(
            source_tree=ROOT,
            saved_handoff=handoff,
            saved_plan=plan,
            current_report=copy.deepcopy(report),
        )
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("tampered plan must fail closed")


def _reseal_gate_record(record):
    identity = {
        field: record[field]
        for field in MODULE.GATE_IDENTITY_FIELDS
    }
    record["gate_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_tampered_gate_report_digest_is_rejected():
    report = _report()
    handoff, plan = _artifacts(report)
    gate = MODULE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        current_report=copy.deepcopy(report),
    ).to_record()

    gate["operation_count"] += 1

    try:
        MODULE.validate_deployment_gate_report(gate)
    except ValueError as exc:
        assert "deployment-needed flag mismatch" in str(exc) or "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered gate report must fail closed")


def test_rehashed_gate_report_cannot_authorize_mutation():
    report = _report()
    handoff, plan = _artifacts(report)
    gate = MODULE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        current_report=copy.deepcopy(report),
    ).to_record()

    gate["mutation_authorized"] = True
    _reseal_gate_record(gate)

    try:
        MODULE.validate_deployment_gate_report(gate)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_rehashed_gate_report_cannot_hide_state_consistency_rules():
    report = _report()
    handoff, plan = _artifacts(report)
    gate = MODULE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        current_report=copy.deepcopy(report),
    ).to_record()

    gate["changed_sections"] = ["target_pool_cursor"]
    _reseal_gate_record(gate)

    try:
        MODULE.validate_deployment_gate_report(gate)
    except ValueError as exc:
        assert "matching deployment gate state" in str(exc)
    else:
        raise AssertionError("inconsistent rehashed gate report must fail closed")


def test_gate_report_schema_rejects_extra_fields_even_when_digest_is_unchanged():
    report = _report()
    handoff, plan = _artifacts(report)
    gate = MODULE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        current_report=copy.deepcopy(report),
    ).to_record()
    gate["unexpected"] = "field"

    try:
        MODULE.validate_deployment_gate_report(gate)
    except ValueError as exc:
        assert "fields do not match reviewed schema" in str(exc)
    else:
        raise AssertionError("gate schema drift must fail closed")


def test_gate_source_contains_only_read_only_handoff_path():
    source = TOOL.read_text(encoding="utf-8")

    assert "_build_current_report" in source
    assert "apply=True" not in source
    assert "apply_guarded_patch" not in source
    assert "shutil" not in source
    assert "copy2" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
