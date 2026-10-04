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


def make_runtime(tmp_path: Path, monkeypatch) -> Path:
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
    monkeypatch.setattr(MODULE, "PINNED_SOURCE_HEAD", head)

    patch = ROOT / "deploy/patches/phase2-production-add-index-compat.patch"
    subprocess.run(
        ["git", "apply", "--whitespace=error-all", str(patch)],
        cwd=source,
        check=True,
    )

    executor = source / MODULE.EXECUTOR_RELATIVE
    executor.parent.mkdir(parents=True, exist_ok=True)
    executor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executor.chmod(executor.stat().st_mode | stat.S_IXUSR)

    watcher = source / MODULE.WATCH_EXECUTOR_RELATIVE
    watcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    watcher.chmod(watcher.stat().st_mode | stat.S_IXUSR)
    return source


def test_runtime_checker_rejects_live_production_path():
    with pytest.raises(ValueError, match="must not be /opt/pio"):
        MODULE.inspect_runtime("/opt/pio")


def test_runtime_checker_fails_closed_on_unexpected_dirty_path(
    tmp_path,
    monkeypatch,
):
    source = make_runtime(tmp_path, monkeypatch)
    extra = source / "rust-executor/src/main.rs"
    extra.write_text(extra.read_text(encoding="utf-8") + "\n// drift\n")

    report = MODULE.inspect_runtime(source)

    assert report.runtime_ready is False


def test_runtime_checker_requires_executable_binary(tmp_path, monkeypatch):
    source = make_runtime(tmp_path, monkeypatch)
    executor = source / MODULE.EXECUTOR_RELATIVE
    executor.chmod(0o644)

    report = MODULE.inspect_runtime(source)

    assert report.executor_exists is True
    assert report.executor_executable is False
    assert report.runtime_ready is False



def test_runtime_checker_requires_event_watch_binary(tmp_path, monkeypatch):
    source = make_runtime(tmp_path, monkeypatch)
    watcher = source / MODULE.WATCH_EXECUTOR_RELATIVE
    watcher.unlink()

    report = MODULE.inspect_runtime(source)

    assert report.watch_executor_exists is False
    assert report.watch_executor_executable is False
    assert report.runtime_ready is False


def test_runtime_checker_requires_executable_event_watch_binary(
    tmp_path,
    monkeypatch,
):
    source = make_runtime(tmp_path, monkeypatch)
    watcher = source / MODULE.WATCH_EXECUTOR_RELATIVE
    watcher.chmod(0o644)

    report = MODULE.inspect_runtime(source)

    assert report.watch_executor_exists is True
    assert report.watch_executor_executable is False
    assert report.runtime_ready is False



def test_runtime_contract_pins_operational_autopause_surface():
    required = {
        "deploy/tools/autopause_phase2_isolated_timer.py":
            "e907ce7cccdd28986066027449378682955c04ea",
        "deploy/systemd/pio-phase2-isolated-prestate-stream@.service":
            "4270a0c7b3aec8844ae9f06c6a073aa7b9d247af",
        "deploy/systemd/pio-phase2-isolated-add-detector.service":
            "fdb5334cca2174421e084813b42052f5399c435b",
        "deploy/systemd/pio-phase2-isolated-evidence-cycle.service":
            "3a82fc92e9785f6dcbde8cfd9c3458a71e34c8e3",
        "deploy/systemd/pio-phase2-isolated-evidence-cycle.timer":
            "e6781b6e7f4d235ec170d7f08d8f9c25414100ef",
        "deploy/systemd/pio-phase2-isolated-rate-limit-pause.service":
            "07e8d4a58243537bea74226da4ab6548bf212496",
    }

    for relative, expected_blob in required.items():
        assert MODULE.TRACKED_CONTRACT[relative] == expected_blob
        assert MODULE._git_blob_sha(ROOT / relative) == expected_blob



def test_runtime_checker_uses_descriptor_bound_contract_reads():
    source = TOOL.read_text(encoding="utf-8")

    assert "O_NOFOLLOW" in source
    assert "os.fstat(" in source
    assert "def _capture_regular_file(" in source
    assert ".read_bytes(" not in source


def test_runtime_checker_rejects_same_content_path_replacement(
    tmp_path,
    monkeypatch,
):
    source = make_runtime(tmp_path, monkeypatch)
    target = source / "rust-executor/src/main.rs"
    real_dirty_paths = MODULE._dirty_paths
    calls = 0

    def replace_before_final_recheck(runtime):
        nonlocal calls
        calls += 1
        if calls == 2:
            replacement = target.with_name("main.rs.replacement")
            replacement.write_bytes(target.read_bytes())
            replacement.replace(target)
        return real_dirty_paths(runtime)

    monkeypatch.setattr(
        MODULE,
        "_dirty_paths",
        replace_before_final_recheck,
    )

    with pytest.raises(
        ValueError,
        match="path changed during runtime inspection",
    ):
        MODULE.inspect_runtime(source)
