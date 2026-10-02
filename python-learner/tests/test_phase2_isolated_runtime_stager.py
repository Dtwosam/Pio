from __future__ import annotations

import importlib.util
from pathlib import Path
import os
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/stage_phase2_isolated_runtime.py"
SPEC = importlib.util.spec_from_file_location(
    "stage_phase2_isolated_runtime",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _git_blob(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    import hashlib
    return hashlib.sha1(header + payload).hexdigest()


def make_ready_runtime(tmp_path: Path, monkeypatch) -> Path:
    source = tmp_path / "prepared"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tests"],
        cwd=source,
        check=True,
    )

    contract = {}
    for relative in MODULE.CHECK.TRACKED_CONTRACT:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"base:{relative}\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "base"],
        cwd=source,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    for relative in MODULE.CHECK.EXPECTED_DIRTY_PATHS:
        path = source / relative
        path.write_text(f"patched:{relative}\n", encoding="utf-8")

    for relative in MODULE.CHECK.TRACKED_CONTRACT:
        contract[relative] = _git_blob(source / relative)

    executor = source / MODULE.CHECK.EXECUTOR_RELATIVE
    executor.parent.mkdir(parents=True, exist_ok=True)
    executor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executor.chmod(executor.stat().st_mode | stat.S_IXUSR)

    watcher = source / MODULE.CHECK.WATCH_EXECUTOR_RELATIVE
    watcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    watcher.chmod(watcher.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr(MODULE.CHECK, "PINNED_SOURCE_HEAD", head)
    monkeypatch.setattr(MODULE.CHECK, "TRACKED_CONTRACT", contract)
    return source


def test_stager_preflight_is_non_mutating(tmp_path, monkeypatch):
    source = make_ready_runtime(tmp_path, monkeypatch)
    destination = tmp_path / "runtime-root"

    report = MODULE.stage_runtime(
        source_tree=source,
        destination_root=destination,
    )

    assert report.source_runtime_ready is True
    assert report.applied is False
    assert destination.exists() is False
    assert report.production_tree_modified is False
    assert report.rpc_called is False
    assert report.service_control_performed is False


def test_stager_installs_versioned_release_and_atomic_current_link(
    tmp_path,
    monkeypatch,
):
    source = make_ready_runtime(tmp_path, monkeypatch)
    destination = tmp_path / "runtime-root"

    report = MODULE.stage_runtime(
        source_tree=source,
        destination_root=destination,
        apply=True,
    )

    release = Path(report.release_path)
    current = Path(report.current_link)
    assert report.applied is True
    assert release.is_dir()
    assert current.is_symlink()
    assert current.resolve() == release.resolve()
    assert report.current_target == os.path.relpath(release, destination)
    assert MODULE.CHECK.inspect_runtime(release).runtime_ready is True


def test_stager_reuses_valid_existing_release(tmp_path, monkeypatch):
    source = make_ready_runtime(tmp_path, monkeypatch)
    destination = tmp_path / "runtime-root"

    first = MODULE.stage_runtime(
        source_tree=source,
        destination_root=destination,
        apply=True,
    )
    second = MODULE.stage_runtime(
        source_tree=source,
        destination_root=destination,
        apply=True,
    )

    assert first.existing_release_reused is False
    assert second.existing_release_reused is True
    assert Path(second.current_link).resolve() == Path(
        second.release_path
    ).resolve()


def test_stager_refuses_non_symlink_current_path(tmp_path, monkeypatch):
    source = make_ready_runtime(tmp_path, monkeypatch)
    destination = tmp_path / "runtime-root"
    destination.mkdir()
    (destination / "current").mkdir()

    with pytest.raises(ValueError, match="not a symlink"):
        MODULE.stage_runtime(
            source_tree=source,
            destination_root=destination,
            apply=True,
        )


def test_stager_rejects_live_production_paths():
    with pytest.raises(ValueError, match="must not be inside /opt/pio"):
        MODULE.stage_runtime(
            source_tree="/opt/pio",
            destination_root="/tmp/runtime",
        )

    with pytest.raises(ValueError, match="must not be inside /opt/pio"):
        MODULE._destination("/opt/pio/phase2-runtime")



def test_stager_rejects_symlinked_source_and_destination(
    tmp_path,
    monkeypatch,
):
    source = make_ready_runtime(tmp_path, monkeypatch)
    source_link = tmp_path / "source-link"
    source_link.symlink_to(source, target_is_directory=True)

    with pytest.raises(ValueError, match="source tree must not be a symlink"):
        MODULE.stage_runtime(
            source_tree=source_link,
            destination_root=tmp_path / "runtime-root",
        )

    real_destination = tmp_path / "real-runtime-root"
    real_destination.mkdir()
    destination_link = tmp_path / "runtime-link"
    destination_link.symlink_to(real_destination, target_is_directory=True)

    with pytest.raises(
        ValueError,
        match="destination root must not be a symlink",
    ):
        MODULE.stage_runtime(
            source_tree=source,
            destination_root=destination_link,
        )
