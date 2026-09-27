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


def test_overlay_summary_separates_preflight_from_deployed_state():
    ready = MODULE._overlay_summary(
        _overlay("READY_UPDATE", "READY_CREATE")
    )
    assert ready.content_ready is True
    assert ready.deployed is False
    assert ready.files_changed == 2

    deployed = MODULE._overlay_summary(
        _overlay("ALREADY_TARGET", "ALREADY_TARGET")
    )
    assert deployed.content_ready is True
    assert deployed.deployed is True
    assert deployed.files_changed == 0

    conflict = MODULE._overlay_summary(
        _overlay("ALREADY_TARGET", "CONFLICT_MODIFIED")
    )
    assert conflict.content_ready is False
    assert conflict.deployed is False
    assert conflict.nonready == (
        {"path": "file-1.py", "status": "CONFLICT_MODIFIED"},
    )


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
