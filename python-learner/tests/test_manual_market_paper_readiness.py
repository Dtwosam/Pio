from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "check_manual_market_paper_readiness.py"

SPEC = importlib.util.spec_from_file_location(
    "check_manual_market_paper_readiness",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _completed(args, *, stdout="", returncode=0):
    return subprocess.CompletedProcess(
        args=args,
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def _file(path, status):
    return SimpleNamespace(path=path, status=status)


def _overlay(*statuses, authorized=False):
    files = tuple(
        _file(f"file-{index}.py", status)
        for index, status in enumerate(statuses)
    )
    return SimpleNamespace(
        content_ready=all(
            status in {"ALREADY_TARGET", "READY_CREATE", "READY_UPDATE"}
            for status in statuses
        ),
        apply_authorized=authorized,
        files_changed=sum(
            status in {"READY_CREATE", "READY_UPDATE"}
            for status in statuses
        ),
        files=files,
    )



def _deploy_file(
    path,
    status,
    *,
    expected_base_blob,
    target_blob,
    source_blob,
    current_blob,
):
    return SimpleNamespace(
        path=path,
        status=status,
        expected_base_blob=expected_base_blob,
        target_blob=target_blob,
        source_blob=source_blob,
        current_blob=current_blob,
    )


def _deploy_report(*files):
    ready = {"ALREADY_TARGET", "READY_CREATE", "READY_UPDATE"}
    return SimpleNamespace(
        content_ready=all(item.status in ready for item in files),
        apply_authorized=False,
        files_changed=sum(
            item.status in {"READY_CREATE", "READY_UPDATE"}
            for item in files
        ),
        files=tuple(files),
    )


def test_reviewed_readiness_source_artifacts_are_exactly_pinned():
    MODULE._verify_reviewed_source_artifacts(ROOT)

    for relative, expected in MODULE.REVIEWED_SOURCE_BLOBS.items():
        assert MODULE._git_blob_sha(ROOT / relative) == expected


def test_reviewed_source_artifact_mismatch_fails_closed(tmp_path):
    source = tmp_path / "source"
    for relative in MODULE.REVIEWED_SOURCE_BLOBS:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / relative).read_bytes())

    damaged = source / MODULE.MARKET_PAPER_MANIFEST
    damaged.write_text("{}\n", encoding="utf-8")

    try:
        MODULE._verify_reviewed_source_artifacts(source)
    except ValueError as exc:
        assert "reviewed source artifact mismatch" in str(exc)
    else:
        raise AssertionError("modified reviewed manifest must fail closed")


def test_phase2_readiness_scope_is_only_market_paper_shared_prerequisites():
    path = ROOT / MODULE.PHASE2_PREREQUISITES_MANIFEST
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["production_deployment_authorized"] is False
    assert payload["deployment_guard_apply_locked"] is True
    assert set(payload["deployment_files"]) == {
        "python-learner/src/meteora_learner/research_store.py",
        "python-learner/src/meteora_learner/storage.py",
    }
    assert "python-learner/src/meteora_learner/cli.py" not in payload["deployment_files"]
    assert not any(
        item.startswith("scripts/") or item.startswith("deploy/systemd/")
        for item in payload["deployment_files"]
    )


def test_overlay_summary_separates_preflight_from_deployed_state():
    ready = MODULE._overlay_summary(
        _overlay("READY_UPDATE", "READY_CREATE")
    )
    assert ready.content_ready is True
    assert ready.deployed is False
    assert ready.files_changed == 2
    assert ready.pending == (
        {"path": "file-0.py", "status": "READY_UPDATE"},
        {"path": "file-1.py", "status": "READY_CREATE"},
    )

    deployed = MODULE._overlay_summary(
        _overlay("ALREADY_TARGET", "ALREADY_TARGET")
    )
    assert deployed.content_ready is True
    assert deployed.deployed is True
    assert deployed.files_changed == 0
    assert deployed.pending == ()

    conflict = MODULE._overlay_summary(
        _overlay("ALREADY_TARGET", "CONFLICT_MODIFIED")
    )
    assert conflict.content_ready is False
    assert conflict.deployed is False
    assert conflict.pending == ()
    assert conflict.nonready == (
        {"path": "file-1.py", "status": "CONFLICT_MODIFIED"},
    )



def test_projected_market_preflight_resolves_only_safe_prerequisite_transition():
    old_blob = "1" * 40
    shared_target = "2" * 40
    runtime_target = "3" * 40

    prerequisite_file = _deploy_file(
        "shared.py",
        "READY_UPDATE",
        expected_base_blob=old_blob,
        target_blob=shared_target,
        source_blob=shared_target,
        current_blob=old_blob,
    )
    prerequisite_report = _deploy_report(prerequisite_file)

    shared_market = _deploy_file(
        "shared.py",
        "CONFLICT_MODIFIED",
        expected_base_blob=shared_target,
        target_blob=shared_target,
        source_blob=shared_target,
        current_blob=old_blob,
    )
    runtime_file = _deploy_file(
        "runtime.py",
        "READY_CREATE",
        expected_base_blob=None,
        target_blob=runtime_target,
        source_blob=runtime_target,
        current_blob=None,
    )
    market_report = _deploy_report(shared_market, runtime_file)
    assert market_report.content_ready is False

    projected = MODULE._projected_market_overlay_summary(
        market_report,
        prerequisite_report,
    )

    assert projected.content_ready is True
    assert projected.files_changed == 1
    assert projected.status_counts == {
        "ALREADY_TARGET": 1,
        "READY_CREATE": 1,
    }
    assert projected.pending == (
        {"path": "runtime.py", "status": "READY_CREATE"},
    )
    assert projected.nonready == ()
    assert projected.projected_prerequisite_paths == ("shared.py",)
    assert projected.mutation_authorized is False

    # Projection is in-memory only. The current-state report remains a conflict.
    assert shared_market.current_blob == old_blob
    assert shared_market.status == "CONFLICT_MODIFIED"


def test_projected_market_preflight_does_not_hide_prerequisite_conflict():
    old_blob = "1" * 40
    shared_target = "2" * 40

    prerequisite_report = _deploy_report(
        _deploy_file(
            "shared.py",
            "CONFLICT_MODIFIED",
            expected_base_blob="0" * 40,
            target_blob=shared_target,
            source_blob=shared_target,
            current_blob=old_blob,
        )
    )
    market_report = _deploy_report(
        _deploy_file(
            "shared.py",
            "CONFLICT_MODIFIED",
            expected_base_blob=shared_target,
            target_blob=shared_target,
            source_blob=shared_target,
            current_blob=old_blob,
        )
    )

    projected = MODULE._projected_market_overlay_summary(
        market_report,
        prerequisite_report,
    )

    assert prerequisite_report.content_ready is False
    assert projected.content_ready is False
    assert projected.projected_prerequisite_paths == ()
    assert projected.nonready == (
        {"path": "shared.py", "status": "CONFLICT_MODIFIED"},
    )


def test_projected_market_preflight_preserves_structural_conflict():
    target = "2" * 40
    prerequisite_report = _deploy_report(
        _deploy_file(
            "shared.py",
            "ALREADY_TARGET",
            expected_base_blob="1" * 40,
            target_blob=target,
            source_blob=target,
            current_blob=target,
        )
    )
    market_report = _deploy_report(
        _deploy_file(
            "shared.py",
            "CONFLICT_SYMLINK",
            expected_base_blob=target,
            target_blob=target,
            source_blob=None,
            current_blob=None,
        )
    )

    projected = MODULE._projected_market_overlay_summary(
        market_report,
        prerequisite_report,
    )

    assert projected.content_ready is False
    assert projected.nonready == (
        {"path": "shared.py", "status": "CONFLICT_SYMLINK"},
    )
    assert projected.projected_prerequisite_paths == ()


def test_deployment_preflight_uses_projected_market_state_not_current_conflict():
    source = TOOL.read_text(encoding="utf-8")

    assert "market_paper_after_prerequisites.content_ready" in source
    assert "and market_paper.content_ready" not in source


def test_read_only_command_runner_never_requests_mutating_git_or_systemctl():
    seen = []

    def runner(args, **kwargs):
        seen.append(tuple(args))
        if args[:2] == ["systemctl", "is-active"]:
            return _completed(args, stdout="active\n")
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return _completed(args, stdout="abc123\n")
        if args[:3] == ["git", "status", "--porcelain=v1"]:
            return _completed(args, stdout=" M one.py\n M two.py\n")
        raise AssertionError(f"unexpected command: {args}")

    assert MODULE._service_state("svc", runner=runner) == "active"
    assert MODULE._git_head(Path("/tmp"), runner=runner) == "abc123"
    assert MODULE._tracked_change_count(Path("/tmp"), runner=runner) == 2

    flattened = [" ".join(command) for command in seen]
    assert all(" checkout " not in f" {value} " for value in flattened)
    assert all(" reset " not in f" {value} " for value in flattened)
    assert all(" pull " not in f" {value} " for value in flattened)
    assert all(" restart " not in f" {value} " for value in flattened)
    assert all(" start " not in f" {value} " for value in flattened)


def test_paper_account_units_must_be_inactive_for_manual_mode():
    seen = []

    def runner(args, **kwargs):
        seen.append(tuple(args))
        if args[:2] == ["systemctl", "is-active"]:
            unit = args[2]
            value = "inactive\n" if unit.startswith("pio-paper@") else "active\n"
            return _completed(args, stdout=value)
        raise AssertionError(f"unexpected command: {args}")

    service = MODULE._service_state(
        MODULE.PAPER_SERVICE_TEMPLATE.format(account="paper"),
        runner=runner,
    )
    timer = MODULE._service_state(
        MODULE.PAPER_TIMER_TEMPLATE.format(account="paper"),
        runner=runner,
    )

    assert service == "inactive"
    assert timer == "inactive"
    assert (
        MODULE.PAPER_SERVICE_TEMPLATE.format(account="paper"),
    ) not in seen
    assert (
        "systemctl",
        "is-active",
        "pio-paper@paper.service",
    ) in seen
    assert (
        "systemctl",
        "is-active",
        "pio-paper@paper.timer",
    ) in seen


def test_manual_mode_safety_fails_closed_on_unknown_or_failed_units():
    assert MODULE._manual_mode_safe(
        paper_service="inactive",
        paper_timer="inactive",
    ) is True
    assert MODULE._manual_mode_safe(
        paper_service="unknown",
        paper_timer="inactive",
    ) is False
    assert MODULE._manual_mode_safe(
        paper_service="inactive",
        paper_timer="unknown",
    ) is False
    assert MODULE._manual_mode_safe(
        paper_service="failed",
        paper_timer="inactive",
    ) is False


def test_target_cursor_is_read_only(tmp_path):
    repo = tmp_path / "repo"
    state = repo / MODULE.DETECTOR_STATE
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "cursors": {
                    MODULE.TARGET_POOL: "cursor-value",
                }
            }
        ),
        encoding="utf-8",
    )
    before = state.read_bytes()

    assert MODULE._target_cursor(
        repo,
        pool=MODULE.TARGET_POOL,
    ) == "cursor-value"
    assert state.read_bytes() == before


def test_source_contains_locked_market_paper_manifest_and_no_apply_path():
    source = TOOL.read_text(encoding="utf-8")

    assert "apply=False" in source
    assert "systemctl\", \"is-active" in source
    assert "mutation_authorized=False" in source
    assert "apply=True" not in source
    assert "systemctl\", \"restart" not in source
    assert "systemctl\", \"start" not in source
    assert "git\", \"checkout" not in source
    assert "git\", \"reset" not in source
    assert "git\", \"pull" not in source
