from __future__ import annotations

from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "build_manual_market_paper_deployment_plan.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_deployment_plan",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

HANDOFF = MODULE._load_module(
    ROOT / MODULE.HANDOFF_TOOL,
    "test_manual_market_paper_plan_handoff",
)
PREREQUISITE = json.loads(
    (ROOT / MODULE.PREREQUISITE_MANIFEST).read_text(encoding="utf-8")
)
RUNTIME = json.loads(
    (ROOT / MODULE.MANUAL_RUNTIME_MANIFEST).read_text(encoding="utf-8")
)


def _overlay(manifest, pending=None):
    pending = dict(pending or {})
    counts = Counter()
    pending_records = []
    for path in manifest["deployment_files"]:
        status = pending.get(path, "ALREADY_TARGET")
        counts[status] += 1
        if status in MODULE.PENDING_STATUSES:
            pending_records.append({"path": path, "status": status})
    return {
        "content_ready": all(
            status in MODULE.READY_STATUSES
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
    phase2=None,
    market_paper=None,
    state_reader=None,
    runtime_files_deployed=None,
):
    phase2 = phase2 or _overlay(PREREQUISITE)
    market_paper = market_paper or _overlay(RUNTIME)
    state_reader = state_reader or {
        "preflight_ready": True,
        "deployed": True,
        "status": "ALREADY_TARGET",
        "error_category": None,
    }
    if runtime_files_deployed is None:
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
        "target_pool_cursor": "cursor",
        "phase2": phase2,
        "state_reader": state_reader,
        "market_paper": market_paper,
        "deployment_preflight_clean": True,
        "runtime_files_deployed": runtime_files_deployed,
        "operational_services_healthy": True,
        "manual_paper_runtime_ready": runtime_files_deployed,
        "mutation_authorized": False,
    }


def _handoff(**kwargs):
    return HANDOFF.build_handoff_snapshot(_report(**kwargs))


def test_reviewed_plan_source_artifacts_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_runtime_and_prerequisite_layers_are_disjoint():
    MODULE._validate_manifest(PREREQUISITE, label="prerequisite")
    MODULE._validate_manifest(RUNTIME, label="runtime")

    prerequisite_paths = set(PREREQUISITE["deployment_files"])
    runtime_paths = set(RUNTIME["deployment_files"])
    assert prerequisite_paths.isdisjoint(runtime_paths)
    assert "python-learner/src/meteora_learner/research_store.py" in (
        prerequisite_paths
    )
    assert "python-learner/src/meteora_learner/storage.py" in prerequisite_paths
    assert "python-learner/src/meteora_learner/cli.py" not in prerequisite_paths
    assert "python-learner/src/meteora_learner/cli.py" not in runtime_paths


def test_plan_orders_prerequisite_patch_then_runtime_with_exact_blobs():
    phase2 = _overlay(
        PREREQUISITE,
        {
            "python-learner/src/meteora_learner/research_store.py": (
                "READY_UPDATE"
            ),
        },
    )
    market = _overlay(
        RUNTIME,
        {
            "python-learner/src/meteora_learner/jupiter_quotes.py": (
                "READY_UPDATE"
            ),
            "python-learner/src/meteora_learner/manual_market_paper_cycle.py": (
                "READY_CREATE"
            ),
        },
    )
    state_reader = {
        "preflight_ready": True,
        "deployed": False,
        "status": "READY_UPDATE",
        "error_category": None,
    }
    snapshot = _handoff(
        phase2=phase2,
        market_paper=market,
        state_reader=state_reader,
    )

    plan = MODULE.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=snapshot,
    )

    paths = [item["path"] for item in plan["operations"]]
    assert paths == [
        "python-learner/src/meteora_learner/research_store.py",
        "rust-executor/src/state_reader.rs",
        "python-learner/src/meteora_learner/jupiter_quotes.py",
        "python-learner/src/meteora_learner/manual_market_paper_cycle.py",
    ]
    assert [item["layer"] for item in plan["operations"]] == [
        "PHASE2_SHARED_PREREQUISITES",
        "STATE_READER",
        "MARKET_PAPER_RUNTIME",
        "MARKET_PAPER_RUNTIME",
    ]

    prerequisite_op = plan["operations"][0]
    assert prerequisite_op["expected_current_blob"] == (
        PREREQUISITE["deployment_base_file_blobs"][
            prerequisite_op["path"]
        ]
    )
    assert prerequisite_op["target_blob"] == (
        PREREQUISITE["deployment_target_file_blobs"][
            prerequisite_op["path"]
        ]
    )
    assert prerequisite_op["backup_required"] is True

    patch_op = plan["operations"][1]
    assert patch_op["operation"] == "APPLY_REVIEWED_PATCH"
    assert patch_op["expected_current_blob"] == (
        "50b2291d3cdb1b727bc443beaca780e7a6e20e85"
    )
    assert patch_op["target_blob"] == (
        "30d1435af1329bca07f73d6639539b43503e84e9"
    )
    assert len(patch_op["patch_sha256"]) == 64

    create_op = plan["operations"][-1]
    assert create_op["operation"] == "CREATE_FILE"
    assert create_op["expected_current_blob"] is None
    assert create_op["backup_required"] is False

    assert plan["operation_count"] == 4
    assert plan["deployment_needed"] is True
    assert plan["plan_ready"] is True
    assert plan["requires_fresh_handoff_verification"] is True
    assert plan["production_deployment_authorized"] is False
    assert plan["mutation_authorized"] is False
    assert plan["service_restart_authorized"] is False
    assert plan["detector_cursor_movement_authorized"] is False
    assert plan["paper_timer_enable_authorized"] is False
    assert plan["live_capital_authorized"] is False
    assert len(plan["plan_sha256"]) == 64


def test_plan_is_deterministic_for_same_handoff_and_source():
    snapshot = _handoff(
        phase2=_overlay(
            PREREQUISITE,
            {
                "python-learner/src/meteora_learner/storage.py": (
                    "READY_UPDATE"
                )
            },
        )
    )
    one = MODULE.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=snapshot,
    )
    two = MODULE.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=json.loads(json.dumps(snapshot)),
    )

    assert one == two


def test_already_deployed_handoff_builds_empty_non_authorizing_plan():
    snapshot = _handoff()
    plan = MODULE.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=snapshot,
    )

    assert plan["operations"] == []
    assert plan["operation_count"] == 0
    assert plan["deployment_needed"] is False
    assert plan["plan_ready"] is True
    assert plan["mutation_authorized"] is False


def test_plan_rejects_nonready_overlay_even_if_handoff_flag_is_forged_clean():
    phase2 = _overlay(PREREQUISITE)
    phase2["content_ready"] = False
    phase2["status_counts"] = {
        "ALREADY_TARGET": len(PREREQUISITE["deployment_files"]) - 1,
        "CONFLICT_MODIFIED": 1,
    }
    phase2["nonready"] = [
        {
            "path": "python-learner/src/meteora_learner/research_store.py",
            "status": "CONFLICT_MODIFIED",
        }
    ]
    snapshot = _handoff(
        phase2=phase2,
        runtime_files_deployed=False,
    )

    try:
        MODULE.build_deployment_plan(
            source_tree=ROOT,
            handoff_snapshot=snapshot,
        )
    except ValueError as exc:
        assert "not content-ready" in str(exc)
    else:
        raise AssertionError("non-ready overlay must not produce a plan")


def test_plan_rejects_unexpected_pending_path():
    phase2 = _overlay(
        PREREQUISITE,
        {
            "python-learner/src/meteora_learner/research_store.py": (
                "READY_UPDATE"
            )
        },
    )
    phase2["pending"][0]["path"] = (
        "python-learner/src/meteora_learner/cli.py"
    )
    snapshot = _handoff(
        phase2=phase2,
        runtime_files_deployed=False,
    )

    try:
        MODULE.build_deployment_plan(
            source_tree=ROOT,
            handoff_snapshot=snapshot,
        )
    except ValueError as exc:
        assert "outside manifest" in str(exc)
    else:
        raise AssertionError("unexpected pending path must fail closed")


def test_plan_rejects_tampered_handoff_before_building_operations():
    snapshot = _handoff()
    snapshot["state"]["target_pool_cursor"] = "tampered"

    try:
        MODULE.build_deployment_plan(
            source_tree=ROOT,
            handoff_snapshot=snapshot,
        )
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered handoff must fail closed")


def test_plan_source_has_no_production_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "shutil" not in source
    assert "copy2" not in source
    assert "apply_guarded_patch" not in source
    assert "systemctl" not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source


def _rehash_plan(plan):
    identity = {
        field: plan[field]
        for field in MODULE.PLAN_IDENTITY_FIELDS
    }
    plan["plan_sha256"] = __import__("hashlib").sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_saved_plan_validator_accepts_exact_built_plan():
    snapshot = _handoff(
        phase2=_overlay(
            PREREQUISITE,
            {
                "python-learner/src/meteora_learner/storage.py": (
                    "READY_UPDATE"
                )
            },
        )
    )
    plan = MODULE.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=snapshot,
    )

    MODULE.validate_deployment_plan(plan)


def test_saved_plan_validator_rejects_digest_tamper():
    plan = MODULE.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=_handoff(),
    )
    plan["handoff_state_sha256"] = "f" * 64

    try:
        MODULE.validate_deployment_plan(plan)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered saved plan must fail closed")


def test_saved_plan_validator_rejects_authorization_flip_even_if_rehashed():
    plan = MODULE.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=_handoff(),
    )
    plan["mutation_authorized"] = True
    _rehash_plan(plan)

    try:
        MODULE.validate_deployment_plan(plan)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("saved plan must never authorize mutation")


def test_saved_plan_validator_rejects_reordered_layers_even_if_rehashed():
    phase2 = _overlay(
        PREREQUISITE,
        {
            "python-learner/src/meteora_learner/research_store.py": (
                "READY_UPDATE"
            )
        },
    )
    market = _overlay(
        RUNTIME,
        {
            "python-learner/src/meteora_learner/jupiter_quotes.py": (
                "READY_UPDATE"
            )
        },
    )
    plan = MODULE.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=_handoff(
            phase2=phase2,
            market_paper=market,
        ),
    )
    plan["operations"] = list(reversed(plan["operations"]))
    _rehash_plan(plan)

    try:
        MODULE.validate_deployment_plan(plan)
    except ValueError as exc:
        assert "out of layer order" in str(exc)
    else:
        raise AssertionError("reordered deployment layers must fail closed")


def test_saved_plan_validator_rejects_unknown_fields():
    plan = MODULE.build_deployment_plan(
        source_tree=ROOT,
        handoff_snapshot=_handoff(),
    )
    plan["apply_now"] = True

    try:
        MODULE.validate_deployment_plan(plan)
    except ValueError as exc:
        assert "fields do not match" in str(exc)
    else:
        raise AssertionError("unknown saved-plan fields must fail closed")
