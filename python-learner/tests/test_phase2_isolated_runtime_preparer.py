from __future__ import annotations

import importlib.util
from pathlib import Path
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/prepare_phase2_isolated_runtime.py"
SPEC = importlib.util.spec_from_file_location(
    "prepare_phase2_isolated_runtime",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_source(tmp_path: Path, monkeypatch) -> Path:
    source = tmp_path / "runtime"
    subprocess.run(
        ["git", "clone", "--quiet", str(ROOT), str(source)],
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    monkeypatch.setattr(MODULE.CHECK, "PINNED_SOURCE_HEAD", head)
    return source


def test_preparer_refuses_live_production_tree():
    with pytest.raises(ValueError, match="must not be /opt/pio"):
        MODULE.prepare_runtime(source_tree="/opt/pio")


def test_preparer_preflight_is_non_mutating(tmp_path, monkeypatch):
    source = make_source(tmp_path, monkeypatch)

    report = MODULE.prepare_runtime(source_tree=source)

    assert report.initial_tracked_clean is True
    assert report.compat_status == "READY_APPLY"
    assert report.compat_ready is True
    assert report.compat_applied is False
    assert report.build_requested is False
    assert report.runtime_ready is False
    assert MODULE._tracked_dirty(source) == ()


def test_preparer_applies_compat_builds_and_validates(tmp_path, monkeypatch):
    source = make_source(tmp_path, monkeypatch)

    def fake_build(runtime):
        executor = runtime / MODULE.CHECK.EXECUTOR_RELATIVE
        executor.parent.mkdir(parents=True, exist_ok=True)
        executor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executor.chmod(executor.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr(MODULE, "_build_executor", fake_build)

    report = MODULE.prepare_runtime(source_tree=source, prepare=True)

    assert report.initial_tracked_clean is True
    assert report.compat_applied is True
    assert report.build_requested is True
    assert report.build_succeeded is True
    assert report.runtime_ready is True
    assert report.production_tree_modified is False
    assert report.rpc_called is False
    assert report.service_control_performed is False


def test_preparer_rejects_dirty_source_before_mutation(tmp_path, monkeypatch):
    source = make_source(tmp_path, monkeypatch)
    tracked = source / "rust-executor" / "src" / "main.rs"
    tracked.write_text(
        tracked.read_text(encoding="utf-8") + "\n// local drift\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected tracked changes"):
        MODULE.prepare_runtime(source_tree=source, prepare=True)
