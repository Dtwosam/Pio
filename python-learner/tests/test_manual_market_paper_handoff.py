from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "manual_market_paper_handoff.py"

SPEC = importlib.util.spec_from_file_location(
    "manual_market_paper_handoff",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _report(**overrides):
    record = {
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
        "phase2": {
            "content_ready": True,
            "deployed": False,
            "apply_authorized": False,
            "files_changed": 2,
            "status_counts": {"READY_UPDATE": 2},
            "pending": [
                {"path": "a.py", "status": "READY_UPDATE"},
                {"path": "b.py", "status": "READY_UPDATE"},
            ],
            "nonready": [],
        },
        "state_reader": {
            "preflight_ready": True,
            "deployed": False,
            "status": "READY_UPDATE",
            "error_category": None,
        },
        "market_paper": {
            "content_ready": True,
            "deployed": False,
            "apply_authorized": False,
            "files_changed": 5,
            "status_counts": {"READY_CREATE": 5},
            "pending": [
                {"path": "c.py", "status": "READY_CREATE"},
            ],
            "nonready": [],
        },
        "deployment_preflight_clean": True,
        "runtime_files_deployed": False,
        "operational_services_healthy": True,
        "manual_paper_runtime_ready": False,
        "mutation_authorized": False,
    }
    record.update(overrides)
    return record


def test_pinned_readiness_tool_blob_matches_reviewed_source():
    path = ROOT / MODULE.READINESS_TOOL
    assert MODULE._git_blob_sha(path) == MODULE.EXPECTED_READINESS_TOOL_BLOB


def test_tracked_diff_fingerprint_hashes_bytes_without_exposing_them(tmp_path):
    seen = []
    diff = b"diff --git a/a.py b/a.py\n+local production fix\n"

    def runner(args, **kwargs):
        seen.append((tuple(args), kwargs))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=diff,
            stderr=b"",
        )

    digest = MODULE._tracked_diff_sha256(tmp_path, runner=runner)

    assert digest == hashlib.sha256(diff).hexdigest()
    assert seen[0][0] == (
        "git",
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
    )
    assert seen[0][1]["capture_output"] is True
    assert seen[0][1]["check"] is False


def test_tracked_diff_fingerprint_fails_closed_on_git_error(tmp_path):
    def runner(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout=b"",
            stderr=b"failure",
        )

    try:
        MODULE._tracked_diff_sha256(tmp_path, runner=runner)
    except ValueError as exc:
        assert "cannot fingerprint" in str(exc)
    else:
        raise AssertionError("git diff failure must fail closed")


def test_snapshot_is_deterministic_and_never_authorizes_mutation():
    report = _report()
    one = MODULE.build_handoff_snapshot(report)
    two = MODULE.build_handoff_snapshot(json.loads(json.dumps(report)))

    assert one == two
    assert one["handoff_ready"] is True
    assert one["deployment_needed"] is True
    assert one["mutation_authorized"] is False
    assert len(one["production_state_sha256"]) == 64
    MODULE.validate_handoff_snapshot(one)


def test_handoff_requires_explicit_safe_manual_account_state():
    assert MODULE.build_handoff_snapshot(
        _report(paper_account=None)
    )["handoff_ready"] is False
    assert MODULE.build_handoff_snapshot(
        _report(manual_mode_safe=False)
    )["handoff_ready"] is False
    assert MODULE.build_handoff_snapshot(
        _report(operational_services_healthy=False)
    )["handoff_ready"] is False
    assert MODULE.build_handoff_snapshot(
        _report(deployment_preflight_clean=False)
    )["handoff_ready"] is False


def test_snapshot_tamper_fails_closed():
    snapshot = MODULE.build_handoff_snapshot(_report())
    snapshot["state"]["target_pool_cursor"] = "different"

    try:
        MODULE.validate_handoff_snapshot(snapshot)
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("tampered handoff snapshot must fail closed")


def test_snapshot_cannot_claim_mutation_authorization():
    snapshot = MODULE.build_handoff_snapshot(_report())
    snapshot["mutation_authorized"] = True

    try:
        MODULE.validate_handoff_snapshot(snapshot)
    except ValueError as exc:
        assert "never authorize mutation" in str(exc)
    else:
        raise AssertionError("mutation authorization must be rejected")


def test_verify_reports_cursor_drift_without_mutating_snapshot():
    snapshot = MODULE.build_handoff_snapshot(_report())
    before = json.dumps(snapshot, sort_keys=True)

    result = MODULE.compare_handoff_snapshot(
        snapshot,
        _report(target_pool_cursor="new-cursor"),
    )

    assert result["state_matches"] is False
    assert result["changed_sections"] == ("target_pool_cursor",)
    assert result["current_handoff_ready"] is True
    assert result["mutation_authorized"] is False
    assert json.dumps(snapshot, sort_keys=True) == before


def test_verify_reports_same_count_but_different_tracked_diff():
    snapshot = MODULE.build_handoff_snapshot(_report())
    result = MODULE.compare_handoff_snapshot(
        snapshot,
        _report(tracked_diff_sha256="f" * 64),
    )

    assert result["state_matches"] is False
    assert result["changed_sections"] == ("tracked_diff_sha256",)


def test_verify_accepts_exact_same_relevant_state():
    report = _report()
    snapshot = MODULE.build_handoff_snapshot(report)
    result = MODULE.compare_handoff_snapshot(snapshot, report)

    assert result["state_matches"] is True
    assert result["changed_sections"] == ()
    assert result["snapshot_handoff_ready"] is True
    assert result["current_handoff_ready"] is True


def test_source_has_no_apply_restart_or_cursor_write_path():
    source = TOOL.read_text(encoding="utf-8")

    assert "mutation_authorized" in source
    assert "build_production_readiness" in source
    assert "--no-ext-diff" in source
    assert "--no-textconv" in source
    assert "apply=True" not in source
    assert 'systemctl", "restart' not in source
    assert 'systemctl", "start' not in source
    assert 'git", "checkout' not in source
    assert 'git", "reset' not in source
    assert 'git", "pull' not in source
    assert "write_text(" not in source
    assert "write_bytes(" not in source
