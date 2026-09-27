from __future__ import annotations

from collections import Counter
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_mutation_review.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_mutation_review",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

GATE, PLAN, HANDOFF = MODULE._load_reviewed_modules(ROOT)
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
    gate = GATE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        current_report=copy.deepcopy(report),
    ).to_record()
    GATE.validate_deployment_gate_report(gate)
    return handoff, plan, gate


def _create_parent(repo: Path, relative: str) -> None:
    (repo / Path(relative).parent).mkdir(parents=True, exist_ok=True)


def _reseal_review(review):
    identity = {
        field: review[field]
        for field in MODULE.REVIEW_IDENTITY_FIELDS
    }
    review["review_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_reviewed_mutation_review_source_artifacts_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_ready_create_operation_produces_sealed_read_only_review(tmp_path):
    relative = "python-learner/src/meteora_learner/manual_market_paper_cycle.py"
    report = _report(
        market_paper=_overlay(
            RUNTIME,
            {relative: "READY_CREATE"},
        )
    )
    handoff, plan, gate = _artifacts(report)
    assert plan["operation_count"] == 1
    assert plan["operations"][0]["operation"] == "CREATE_FILE"

    repo = tmp_path / "production"
    repo.mkdir()
    _create_parent(repo, relative)

    review = MODULE.evaluate_mutation_review(
        repository=repo,
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        saved_gate=gate,
        fresh_gate=copy.deepcopy(gate),
    )

    assert review["review_ready"] is True
    assert review["fresh_gate_matches_saved"] is True
    assert review["fresh_gate_ready"] is True
    assert review["source_targets_match_plan"] is True
    assert review["current_files_match_plan"] is True
    assert review["rollback_recipe_complete"] is True
    assert review["backup_capture_required"] is False
    assert review["backup_material_captured"] is False
    assert review["requires_fresh_execution_recheck"] is True
    assert review["requires_backup_capture_before_mutation"] is False
    assert review["requires_separate_mutation_authorization"] is True
    assert review["production_deployment_authorized"] is False
    assert review["mutation_authorized"] is False
    assert review["service_restart_authorized"] is False
    assert review["detector_cursor_movement_authorized"] is False
    assert review["paper_timer_enable_authorized"] is False
    assert review["live_capital_authorized"] is False

    check = review["operation_checks"][0]
    assert check["current_state"] == "ABSENT_AS_EXPECTED"
    assert check["current_matches_expected"] is True
    assert check["source_state"] == "BLOB_MATCH"
    assert check["source_matches_target"] is True
    assert check["rollback_operation"] == "DELETE_CREATED_FILE"
    assert check["rollback_blob"] is None
    assert check["backup_required"] is False
    assert check["operation_ready"] is True

    round_tripped = json.loads(json.dumps(review))
    MODULE.validate_mutation_review(round_tripped)
    assert len(round_tripped["review_sha256"]) == 64


def test_unexpected_production_file_fails_review_without_mutating(tmp_path):
    relative = "python-learner/src/meteora_learner/manual_market_paper_cycle.py"
    report = _report(
        market_paper=_overlay(
            RUNTIME,
            {relative: "READY_CREATE"},
        )
    )
    handoff, plan, gate = _artifacts(report)

    repo = tmp_path / "production"
    repo.mkdir()
    _create_parent(repo, relative)
    target = repo / relative
    target.write_text("unexpected local production content\n", encoding="utf-8")

    review = MODULE.evaluate_mutation_review(
        repository=repo,
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        saved_gate=gate,
        fresh_gate=copy.deepcopy(gate),
    )

    assert review["review_ready"] is False
    assert review["current_files_match_plan"] is False
    assert review["operation_checks"][0]["current_state"] == "UNEXPECTED_PRESENT"
    assert target.read_text(encoding="utf-8") == "unexpected local production content\n"


def test_symlinked_production_parent_fails_closed(tmp_path):
    relative = "python-learner/src/meteora_learner/manual_market_paper_cycle.py"
    report = _report(
        market_paper=_overlay(
            RUNTIME,
            {relative: "READY_CREATE"},
        )
    )
    handoff, plan, gate = _artifacts(report)

    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "production"
    repo.mkdir()
    (repo / "python-learner").symlink_to(outside, target_is_directory=True)

    review = MODULE.evaluate_mutation_review(
        repository=repo,
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        saved_gate=gate,
        fresh_gate=copy.deepcopy(gate),
    )

    assert review["review_ready"] is False
    assert review["current_files_match_plan"] is False
    assert review["operation_checks"][0]["current_state"] == "PARENT_SYMLINK"


def test_fresh_gate_drift_fails_review_even_when_operation_path_still_matches(tmp_path):
    relative = "python-learner/src/meteora_learner/manual_market_paper_cycle.py"
    saved_report = _report(
        market_paper=_overlay(
            RUNTIME,
            {relative: "READY_CREATE"},
        )
    )
    handoff, plan, saved_gate = _artifacts(saved_report)
    current_report = copy.deepcopy(saved_report)
    current_report["target_pool_cursor"] = "different-cursor"
    fresh_gate = GATE.evaluate_deployment_gate(
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        current_report=current_report,
    ).to_record()
    GATE.validate_deployment_gate_report(fresh_gate)

    repo = tmp_path / "production"
    repo.mkdir()
    _create_parent(repo, relative)

    review = MODULE.evaluate_mutation_review(
        repository=repo,
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        saved_gate=saved_gate,
        fresh_gate=fresh_gate,
    )

    assert review["current_files_match_plan"] is True
    assert review["fresh_gate_ready"] is False
    assert review["fresh_gate_matches_saved"] is False
    assert review["review_ready"] is False


def test_update_operation_requires_backup_and_exact_restore_blob(tmp_path):
    source = tmp_path / "source"
    repo = tmp_path / "production"
    relative = Path("nested/file.py")
    (source / relative.parent).mkdir(parents=True)
    (repo / relative.parent).mkdir(parents=True)

    source_target = source / relative
    current_target = repo / relative
    source_target.write_text("new reviewed bytes\n", encoding="utf-8")
    current_target.write_text("old production bytes\n", encoding="utf-8")

    target_blob = MODULE._git_blob_sha(source_target)
    current_blob = MODULE._git_blob_sha(current_target)
    operation = {
        "layer": "MARKET_PAPER_RUNTIME",
        "operation": "UPDATE_FILE",
        "path": relative.as_posix(),
        "expected_current_blob": current_blob,
        "target_blob": target_blob,
        "source_blob": target_blob,
        "backup_required": True,
    }

    check = MODULE._operation_check(
        index=0,
        operation=operation,
        source=source,
        repository=repo,
        plan_module=SimpleNamespace(
            STATE_READER_PATCH=Path("unused.patch")
        ),
    )

    assert check["current_state"] == "BLOB_MATCH"
    assert check["source_state"] == "BLOB_MATCH"
    assert check["backup_required"] is True
    assert check["rollback_operation"] == "RESTORE_EXPECTED_BLOB"
    assert check["rollback_blob"] == current_blob
    assert check["rollback_recipe_complete"] is True
    assert check["operation_ready"] is True


def test_patch_operation_requires_exact_reviewed_patch_bytes(tmp_path):
    source = tmp_path / "source"
    repo = tmp_path / "production"
    relative = Path("rust-executor/src/state_reader.rs")
    patch_relative = Path("deploy/patches/review.patch")
    (source / relative.parent).mkdir(parents=True)
    (repo / relative.parent).mkdir(parents=True)
    (source / patch_relative.parent).mkdir(parents=True)

    source_target = source / relative
    current_target = repo / relative
    patch = source / patch_relative
    source_target.write_text("target state reader\n", encoding="utf-8")
    current_target.write_text("base state reader\n", encoding="utf-8")
    patch.write_text("reviewed patch bytes\n", encoding="utf-8")

    target_blob = MODULE._git_blob_sha(source_target)
    current_blob = MODULE._git_blob_sha(current_target)
    patch_sha = MODULE._sha256(patch)
    operation = {
        "layer": "STATE_READER",
        "operation": "APPLY_REVIEWED_PATCH",
        "path": relative.as_posix(),
        "expected_current_blob": current_blob,
        "target_blob": target_blob,
        "patch_sha256": patch_sha,
        "backup_required": True,
    }

    check = MODULE._operation_check(
        index=0,
        operation=operation,
        source=source,
        repository=repo,
        plan_module=SimpleNamespace(
            STATE_READER_PATCH=patch_relative
        ),
    )
    assert check["patch_matches_plan"] is True
    assert check["observed_patch_sha256"] == patch_sha
    assert check["operation_ready"] is True

    patch.write_text("tampered patch bytes\n", encoding="utf-8")
    drifted = MODULE._operation_check(
        index=0,
        operation=operation,
        source=source,
        repository=repo,
        plan_module=SimpleNamespace(
            STATE_READER_PATCH=patch_relative
        ),
    )
    assert drifted["patch_matches_plan"] is False
    assert drifted["operation_ready"] is False


def test_rehashed_review_cannot_flip_mutation_authorization(tmp_path):
    relative = "python-learner/src/meteora_learner/manual_market_paper_cycle.py"
    report = _report(
        market_paper=_overlay(
            RUNTIME,
            {relative: "READY_CREATE"},
        )
    )
    handoff, plan, gate = _artifacts(report)
    repo = tmp_path / "production"
    repo.mkdir()
    _create_parent(repo, relative)
    review = MODULE.evaluate_mutation_review(
        repository=repo,
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        saved_gate=gate,
        fresh_gate=copy.deepcopy(gate),
    )

    review["mutation_authorized"] = True
    _reseal_review(review)

    try:
        MODULE.validate_mutation_review(review)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed mutation authorization must fail closed")


def test_rehashed_review_cannot_claim_backup_material_capture(tmp_path):
    relative = "python-learner/src/meteora_learner/manual_market_paper_cycle.py"
    report = _report(
        market_paper=_overlay(
            RUNTIME,
            {relative: "READY_CREATE"},
        )
    )
    handoff, plan, gate = _artifacts(report)
    repo = tmp_path / "production"
    repo.mkdir()
    _create_parent(repo, relative)
    review = MODULE.evaluate_mutation_review(
        repository=repo,
        source_tree=ROOT,
        saved_handoff=handoff,
        saved_plan=plan,
        saved_gate=gate,
        fresh_gate=copy.deepcopy(gate),
    )

    review["backup_material_captured"] = True
    _reseal_review(review)

    try:
        MODULE.validate_mutation_review(review)
    except ValueError as exc:
        assert "must not claim backup material capture" in str(exc)
    else:
        raise AssertionError("review must not claim a backup it did not capture")


def test_unsafe_relative_paths_are_rejected():
    for path in (
        "../outside",
        "/absolute/path",
        "nested/../outside",
        "nested\\outside",
    ):
        try:
            MODULE._safe_relative_path(path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe path must fail closed: {path}")


def test_mutation_review_tool_contains_no_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "copy2(" not in source
    assert "shutil" not in source
    assert "subprocess" not in source
    assert "os.replace" not in source
    assert ".unlink(" not in source
    assert ".rename(" not in source
    assert "systemctl" not in source
    assert "apply_guarded_patch" not in source
