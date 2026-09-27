from __future__ import annotations

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
    / "build_manual_market_paper_preserved_deployment_plan.py"
)

SPEC = importlib.util.spec_from_file_location(
    "build_manual_market_paper_preserved_deployment_plan",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_reviewed_preserved_plan_dependencies_are_exactly_pinned():
    MODULE._verify_reviewed_source(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_standard_contract_is_bound_to_pinned_manifests():
    contract = MODULE._standard_contract_map()

    assert MODULE.RESEARCH_STORE_PATH not in contract
    assert MODULE.STATE_READER_PATH not in contract
    assert MODULE.STORAGE_PATH in contract
    assert contract[MODULE.STORAGE_PATH][0] == "PHASE2_SHARED_PREREQUISITES"
    assert any(
        layer == "MARKET_PAPER_RUNTIME"
        for layer, _base, _target in contract.values()
    )


def test_standard_and_preserved_operation_shapes():
    create = MODULE._standard_operation(
        layer="MARKET_PAPER_RUNTIME",
        item={
            "path": "x.py",
            "expected_current_blob": None,
            "target_blob": "1" * 40,
            "status": "READY_CREATE",
        },
    )
    assert create == {
        "layer": "MARKET_PAPER_RUNTIME",
        "operation": "CREATE_FILE",
        "path": "x.py",
        "expected_current_blob": None,
        "target_blob": "1" * 40,
        "source_blob": "1" * 40,
        "backup_required": False,
    }

    preserved = MODULE._preserved_operation(
        layer="STATE_READER",
        item={
            "path": MODULE.STATE_READER_PATH,
            "expected_current_blob": MODULE.EXPECTED_STATE_READER_CURRENT_BLOB,
            "target_blob": MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
            "status": "READY_UPDATE",
        },
        private_bundle_sha256="2" * 64,
    )
    assert preserved["operation"] == "UPDATE_PRESERVED_FILE"
    assert preserved["backup_required"] is True
    assert preserved["private_bundle_sha256"] == "2" * 64


def test_source_blob_rejects_symlinked_parent(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    real = root / "real"
    real.mkdir()
    (real / "x.py").write_text("x\n", encoding="utf-8")
    (root / "alias").symlink_to(real, target_is_directory=True)

    try:
        MODULE._source_blob(root, "alias/x.py")
    except ValueError as exc:
        assert "symlinked parent" in str(exc)
    else:
        raise AssertionError("symlinked source parent must fail closed")


def _real_manifests():
    phase2 = MODULE._load_manifest(ROOT, MODULE.PHASE2_MANIFEST)
    runtime = MODULE._load_manifest(ROOT, MODULE.RUNTIME_MANIFEST)
    return phase2, runtime


def _layer_files(contract):
    result = []
    for path, base, target in contract:
        status = "READY_CREATE" if base is None else "READY_UPDATE"
        result.append(
            {
                "path": path,
                "expected_current_blob": base,
                "target_blob": target,
                "source_blob": target,
                "current_blob": base,
                "status": status,
            }
        )
    return result


def _fake_handoff():
    phase2, runtime = _real_manifests()
    phase2_contract = MODULE._expected_phase2_contract(phase2)
    runtime_contract = MODULE._expected_runtime_contract(runtime)
    bundle_sha = "a" * 64

    return {
        "format_version": 1,
        "artifact_type": MODULE.EXPECTED_HANDOFF_ARTIFACT_TYPE,
        "handoff_sha256": "b" * 64,
        "production_state_sha256": "c" * 64,
        "handoff_ready": True,
        "deployment_needed": True,
        "requires_preservation_aware_plan": True,
        "state": {
            "private_bundle_sha256": bundle_sha,
            "phase2": {
                "files": _layer_files(phase2_contract),
            },
            "state_reader": {
                "expected_current_blob": MODULE.EXPECTED_STATE_READER_CURRENT_BLOB,
                "target_blob": MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
                "source_blob": MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
                "current_blob": MODULE.EXPECTED_STATE_READER_CURRENT_BLOB,
                "status": "READY_UPDATE",
            },
            "market_paper": {
                "files": _layer_files(runtime_contract),
            },
        },
    }


def test_builder_emits_preserved_ops_plus_unchanged_standard_ops(
    tmp_path,
    monkeypatch,
):
    phase2, runtime = _real_manifests()
    fake_module = SimpleNamespace(
        validate_handoff_snapshot=lambda _value: None,
        validate_bundle_report=lambda _value: None,
    )

    monkeypatch.setattr(MODULE, "_verify_reviewed_source", lambda _source: None)
    monkeypatch.setattr(
        MODULE,
        "_load_module",
        lambda _path, _name: fake_module,
    )
    monkeypatch.setattr(
        MODULE,
        "_bundle_dir",
        lambda **_kwargs: tmp_path,
    )
    monkeypatch.setattr(
        MODULE,
        "_load_manifest",
        lambda _source, relative: (
            phase2
            if relative == MODULE.PHASE2_MANIFEST
            else runtime
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "_verify_source_contract",
        lambda **_kwargs: None,
    )

    plan = MODULE.build_preserved_deployment_plan(
        source_tree=tmp_path,
        handoff_snapshot=_fake_handoff(),
        private_bundle_report={"bundle_ready": True},
    )

    MODULE.validate_preserved_deployment_plan(
        json.loads(json.dumps(plan))
    )
    assert plan["plan_ready"] is True
    assert plan["deployment_needed"] is True
    assert plan["candidate_content_included"] is False
    assert plan["operation_count"] == 19
    assert plan["operations"][0]["path"] == MODULE.RESEARCH_STORE_PATH
    assert plan["operations"][0]["operation"] == "UPDATE_PRESERVED_FILE"
    assert plan["operations"][1]["path"] == MODULE.STORAGE_PATH
    assert plan["operations"][1]["operation"] == "UPDATE_FILE"
    assert plan["operations"][2]["path"] == MODULE.STATE_READER_PATH
    assert plan["operations"][2]["operation"] == "UPDATE_PRESERVED_FILE"
    assert all(
        item["layer"] == "MARKET_PAPER_RUNTIME"
        for item in plan["operations"][3:]
    )
    assert plan["mutation_authorized"] is False


def _synthetic_plan():
    standard = MODULE._standard_contract_map()
    storage_layer, storage_base, storage_target = standard[MODULE.STORAGE_PATH]
    runtime_path = next(
        path
        for path, (layer, _base, _target) in standard.items()
        if layer == "MARKET_PAPER_RUNTIME"
    )
    runtime_layer, runtime_base, runtime_target = standard[runtime_path]
    runtime_operation = "CREATE_FILE" if runtime_base is None else "UPDATE_FILE"

    bundle_sha = "a" * 64
    operations = [
        {
            "layer": "PHASE2_SHARED_PREREQUISITES",
            "operation": "UPDATE_PRESERVED_FILE",
            "path": MODULE.RESEARCH_STORE_PATH,
            "expected_current_blob": MODULE.EXPECTED_RESEARCH_STORE_CURRENT_BLOB,
            "target_blob": MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
            "source_blob": MODULE.EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
            "backup_required": True,
            "private_bundle_sha256": bundle_sha,
        },
        {
            "layer": storage_layer,
            "operation": "UPDATE_FILE",
            "path": MODULE.STORAGE_PATH,
            "expected_current_blob": storage_base,
            "target_blob": storage_target,
            "source_blob": storage_target,
            "backup_required": True,
        },
        {
            "layer": "STATE_READER",
            "operation": "UPDATE_PRESERVED_FILE",
            "path": MODULE.STATE_READER_PATH,
            "expected_current_blob": MODULE.EXPECTED_STATE_READER_CURRENT_BLOB,
            "target_blob": MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
            "source_blob": MODULE.EXPECTED_STATE_READER_CANDIDATE_BLOB,
            "backup_required": True,
            "private_bundle_sha256": bundle_sha,
        },
        {
            "layer": runtime_layer,
            "operation": runtime_operation,
            "path": runtime_path,
            "expected_current_blob": runtime_base,
            "target_blob": runtime_target,
            "source_blob": runtime_target,
            "backup_required": runtime_base is not None,
        },
    ]

    identity = {
        "format_version": MODULE.FORMAT_VERSION,
        "artifact_type": MODULE.ARTIFACT_TYPE,
        "handoff_artifact_type": MODULE.EXPECTED_HANDOFF_ARTIFACT_TYPE,
        "handoff_sha256": "b" * 64,
        "handoff_state_sha256": "c" * 64,
        "private_bundle_sha256": bundle_sha,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                MODULE.REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "required_layer_order": list(MODULE.LAYER_ORDER),
        "operations": operations,
        "operation_count": len(operations),
        "deployment_needed": True,
        "plan_ready": True,
        "candidate_content_included": False,
        "requires_fresh_handoff_verification": True,
        "requires_fresh_gate_verification": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    return {
        **identity,
        "plan_sha256": hashlib.sha256(
            MODULE._canonical_bytes(identity)
        ).hexdigest(),
    }


def _reseal(plan):
    identity = {
        field: plan[field]
        for field in MODULE.PLAN_FIELDS
    }
    plan["plan_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()


def test_sealed_preserved_plan_validates():
    plan = _synthetic_plan()

    MODULE.validate_preserved_deployment_plan(
        json.loads(json.dumps(plan))
    )

    assert plan["candidate_content_included"] is False
    assert plan["mutation_authorized"] is False


def test_rehashed_plan_cannot_substitute_preserved_target():
    plan = _synthetic_plan()
    plan["operations"][0]["target_blob"] = "f" * 40
    plan["operations"][0]["source_blob"] = "f" * 40
    _reseal(plan)

    try:
        MODULE.validate_preserved_deployment_plan(plan)
    except ValueError as exc:
        assert "target mismatch" in str(exc)
    else:
        raise AssertionError("rehashed preserved-target substitution must fail closed")


def test_rehashed_plan_cannot_add_arbitrary_standard_path():
    plan = _synthetic_plan()
    plan["operations"].append(
        {
            "layer": "MARKET_PAPER_RUNTIME",
            "operation": "UPDATE_FILE",
            "path": "python-learner/src/meteora_learner/not-reviewed.py",
            "expected_current_blob": "1" * 40,
            "target_blob": "2" * 40,
            "source_blob": "2" * 40,
            "backup_required": True,
        }
    )
    plan["operation_count"] = len(plan["operations"])
    _reseal(plan)

    try:
        MODULE.validate_preserved_deployment_plan(plan)
    except ValueError as exc:
        assert "unexpected path" in str(exc)
    else:
        raise AssertionError("arbitrary standard path must fail closed")


def test_rehashed_plan_cannot_authorize_mutation():
    plan = _synthetic_plan()
    plan["mutation_authorized"] = True
    _reseal(plan)

    try:
        MODULE.validate_preserved_deployment_plan(plan)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_tampered_plan_digest_fails_closed():
    plan = _synthetic_plan()
    plan["operation_count"] += 1

    try:
        MODULE.validate_preserved_deployment_plan(plan)
    except ValueError as exc:
        assert (
            "operation count mismatch" in str(exc)
            or "digest mismatch" in str(exc)
        )
    else:
        raise AssertionError("tampered preserved plan must fail closed")


def test_preserved_plan_has_no_mutation_primitives():
    source = TOOL.read_text(encoding="utf-8")

    assert ".write_text(" not in source
    assert ".write_bytes(" not in source
    assert "shutil" not in source
    assert "subprocess" not in source
    assert "systemctl" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "apply=True" not in source
