from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/check_phase2_isolated_runtime.py"
SPEC = importlib.util.spec_from_file_location(
    "check_phase2_isolated_runtime",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_runtime(tmp_path: Path) -> Path:
    source = tmp_path / "runtime"
    subprocess.run(
        ["git", "clone", "--quiet", str(ROOT), str(source)],
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", MODULE.PINNED_SOURCE_HEAD],
        cwd=source,
        check=True,
    )

    for relative, expected in MODULE.TRACKED_CONTRACT.items():
        src = ROOT / relative
        if MODULE._git_blob_sha(src) == expected:
            dst = source / relative
            shutil.copy2(src, dst)

    for relative in MODULE.EXPECTED_DIRTY_PATHS:
        src = ROOT / relative
        dst = source / relative
        shutil.copy2(src, dst)

    executor = source / MODULE.EXECUTOR_RELATIVE
    executor.parent.mkdir(parents=True, exist_ok=True)
    executor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executor.chmod(executor.stat().st_mode | stat.S_IXUSR)
    return source


def test_runtime_checker_rejects_live_production_path():
    with pytest.raises(ValueError, match="must not be /opt/pio"):
        MODULE.inspect_runtime("/opt/pio")


def test_runtime_checker_fails_closed_on_unexpected_dirty_path(tmp_path):
    source = make_runtime(tmp_path)
    extra = source / "rust-executor/src/main.rs"
    extra.write_text(extra.read_text(encoding="utf-8") + "\n// drift\n")

    report = MODULE.inspect_runtime(source)

    assert report.runtime_ready is False


def test_runtime_checker_requires_executable_binary(tmp_path):
    source = make_runtime(tmp_path)
    executor = source / MODULE.EXECUTOR_RELATIVE
    executor.chmod(0o644)

    report = MODULE.inspect_runtime(source)

    assert report.executor_exists is True
    assert report.executor_executable is False
    assert report.runtime_ready is False
