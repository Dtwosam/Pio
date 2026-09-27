from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "manual_market_paper_preserved_handoff.py"
)

SPEC = importlib.util.spec_from_file_location(
    "manual_market_paper_preserved_handoff",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _fake_readiness_module():
    return SimpleNamespace(
        validate_preserved_readiness=lambda _report: None,
    )


def _report(
    *,
    cursor="cursor-a",
    runtime_files_deployed=False,
    readiness_sha="1" * 64,
    reviewed_source_tree="/var/tmp/source-a",
    private_bundle_dir="/var/tmp/bundle-a",
):
    return {
        "reviewed_source_blobs": dict(
            MODULE.EXPECTED_REVIEWED_SOURCE_BLOBS
        ),
        "readiness_sha256": readiness_sha,
        "repository": "/production/repo",
        "reviewed_source_tree": reviewed_source_tree,
        "private_bundle_dir": private_bundle_dir,
        "private_bundle_sha256": "2" * 64,
        "private_bundle_verified": True,
        "production_head": MODULE.EXPECTED_PRODUCTION_HEAD,
        "production_head_matches_reviewed_baseline": True,
        "tracked_changes": 46,
        "detector_service": "active",
        "watcher_service": "active",
        "paper_account": "paper-test",
        "paper_service": "inactive",
        "paper_timer": "inactive",
        "manual_mode_safe": True,
        "target_pool": MODULE.EXPECTED_TARGET_POOL,
        "target_pool_cursor": cursor,
        "phase2": {"content_ready": True, "deployed": runtime_files_deployed},
        "state_reader": {
            "content_ready": True,
            "deployed": runtime_files_deployed,
        },
        "market_paper": {
            "content_ready": True,
            "deployed": runtime_files_deployed,
        },
        "deployment_preflight_clean": True,
        "runtime_files_deployed": runtime_files_deployed,
        "operational_services_healthy": True,
        "manual_paper_runtime_ready": runtime_files_deployed,
        "requires_fresh_handoff": True,
        "requires_preservation_aware_plan": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }


def test_preserved_handoff_readiness_tool_is_exactly_pinned():
    module = MODULE._load_readiness_module(ROOT)

    assert module is not None
    assert MODULE._git_blob_sha(ROOT / MODULE.READINESS_TOOL) == (
        MODULE.EXPECTED_READINESS_TOOL_BLOB
    )


def test_tracked_diff_uses_safe_directory_and_binary_diff(tmp_path):
    seen = []

    def runner(args, **kwargs):
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=b"diff-bytes",
            stderr=b"",
        )

    digest = MODULE._tracked_diff_sha256(
        tmp_path,
        runner=runner,
    )

    args = seen[0][0]
    assert args[:3] == [
        "git",
        "-c",
        f"safe.directory={tmp_path}",
    ]
    assert args[3:] == [
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
    ]
    assert digest == hashlib.sha256(b"diff-bytes").hexdigest()


def test_ready_report_builds_sealed_preserved_handoff():
    report = _report()

    snapshot = MODULE.build_handoff_snapshot(
        report,
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )

    MODULE.validate_handoff_snapshot(
        json.loads(json.dumps(snapshot))
    )
    assert snapshot["handoff_ready"] is True
    assert snapshot["deployment_needed"] is True
    assert snapshot["state"]["tracked_diff_sha256"] == "4" * 64
    assert snapshot["state"]["private_bundle_sha256"] == "2" * 64
    assert snapshot["mutation_authorized"] is False


def test_deployed_report_marks_deployment_not_needed():
    snapshot = MODULE.build_handoff_snapshot(
        _report(runtime_files_deployed=True),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )

    assert snapshot["handoff_ready"] is True
    assert snapshot["deployment_needed"] is False


def test_handoff_state_is_path_independent():
    first = MODULE.build_handoff_snapshot(
        _report(
            reviewed_source_tree="/var/tmp/source-one",
            private_bundle_dir="/var/tmp/bundle-one",
            readiness_sha="1" * 64,
        ),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )
    second = MODULE.build_handoff_snapshot(
        _report(
            reviewed_source_tree="/var/tmp/source-two",
            private_bundle_dir="/var/tmp/bundle-two",
            readiness_sha="5" * 64,
        ),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )

    assert first["production_state_sha256"] == second[
        "production_state_sha256"
    ]
    assert first["capture_readiness_sha256"] != second[
        "capture_readiness_sha256"
    ]


def test_compare_accepts_fresh_clone_paths_when_logical_state_matches():
    snapshot = MODULE.build_handoff_snapshot(
        _report(
            reviewed_source_tree="/var/tmp/source-one",
            private_bundle_dir="/var/tmp/bundle-one",
            readiness_sha="1" * 64,
        ),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )

    result = MODULE.compare_handoff_snapshot(
        snapshot,
        _report(
            reviewed_source_tree="/var/tmp/source-two",
            private_bundle_dir="/var/tmp/bundle-two",
            readiness_sha="5" * 64,
        ),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )

    assert result["snapshot_handoff_ready"] is True
    assert result["current_handoff_ready"] is True
    assert result["state_matches"] is True
    assert result["changed_sections"] == []


def test_compare_surfaces_cursor_drift():
    snapshot = MODULE.build_handoff_snapshot(
        _report(cursor="cursor-a"),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )

    result = MODULE.compare_handoff_snapshot(
        snapshot,
        _report(cursor="cursor-b"),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )

    assert result["state_matches"] is False
    assert result["changed_sections"] == ["target_pool_cursor"]


def test_compare_surfaces_tracked_diff_drift():
    snapshot = MODULE.build_handoff_snapshot(
        _report(),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )

    result = MODULE.compare_handoff_snapshot(
        snapshot,
        _report(),
        tracked_diff_sha256="5" * 64,
        readiness_module=_fake_readiness_module(),
    )

    assert result["state_matches"] is False
    assert result["changed_sections"] == ["tracked_diff_sha256"]


def test_rehashed_handoff_cannot_claim_clean_preflight_on_head_drift():
    snapshot = MODULE.build_handoff_snapshot(
        _report(),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )
    snapshot["state"]["production_head"] = "f" * 40
    snapshot["state"]["production_head_matches_reviewed_baseline"] = True
    snapshot["state"]["deployment_preflight_clean"] = True
    snapshot["production_state_sha256"] = MODULE._state_digest(
        snapshot["state"]
    )
    snapshot["handoff_ready"] = True
    identity = {
        field: snapshot[field]
        for field in MODULE.HANDOFF_FIELDS
    }
    snapshot["handoff_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_handoff_snapshot(snapshot)
    except ValueError as exc:
        assert "production-head flag mismatch" in str(exc)
    else:
        raise AssertionError("rehashed production-head contradiction must fail closed")


def test_rehashed_handoff_cannot_authorize_mutation():
    snapshot = MODULE.build_handoff_snapshot(
        _report(),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )
    snapshot["mutation_authorized"] = True
    identity = {
        field: snapshot[field]
        for field in MODULE.HANDOFF_FIELDS
    }
    snapshot["handoff_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_handoff_snapshot(snapshot)
    except ValueError as exc:
        assert "mutation_authorized=false" in str(exc)
    else:
        raise AssertionError("rehashed authorization flip must fail closed")


def test_rehashed_handoff_cannot_substitute_reviewed_source_lineage():
    snapshot = MODULE.build_handoff_snapshot(
        _report(),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )
    snapshot["reviewed_source_blobs"] = dict(
        snapshot["reviewed_source_blobs"]
    )
    key = next(iter(snapshot["reviewed_source_blobs"]))
    snapshot["reviewed_source_blobs"][key] = "f" * 40
    identity = {
        field: snapshot[field]
        for field in MODULE.HANDOFF_FIELDS
    }
    snapshot["handoff_sha256"] = hashlib.sha256(
        MODULE._canonical_bytes(identity)
    ).hexdigest()

    try:
        MODULE.validate_handoff_snapshot(snapshot)
    except ValueError as exc:
        assert "reviewed-source lineage mismatch" in str(exc)
    else:
        raise AssertionError("rehashed source-lineage substitution must fail closed")


def test_tampered_handoff_state_digest_fails_closed():
    snapshot = MODULE.build_handoff_snapshot(
        _report(),
        tracked_diff_sha256="4" * 64,
        readiness_module=_fake_readiness_module(),
    )
    tampered = copy.deepcopy(snapshot)
    tampered["state"]["target_pool_cursor"] = "changed"

    try:
        MODULE.validate_handoff_snapshot(tampered)
    except ValueError as exc:
        assert "production-state digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered handoff state must fail closed")


def test_preserved_handoff_has_read_only_production_surface():
    source = TOOL.read_text(encoding="utf-8")

    assert "safe.directory=" in source
    assert '"diff"' in source
    assert '"--binary"' in source
    assert ".write_text(" not in source
    assert ".write_bytes(" not in source
    assert "shutil" not in source
    assert 'git", "apply' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "systemctl restart" not in source
    assert "systemctl enable" not in source
    assert "apply=True" not in source
