from __future__ import annotations

import hashlib
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
    assert report.source_executor_sha256 == hashlib.sha256(
        (source / MODULE.CHECK.EXECUTOR_RELATIVE).read_bytes()
    ).hexdigest()
    assert report.source_watch_executor_sha256 == hashlib.sha256(
        (source / MODULE.CHECK.WATCH_EXECUTOR_RELATIVE).read_bytes()
    ).hexdigest()
    assert report.release_executor_sha256 is None
    assert report.release_watch_executor_sha256 is None
    assert report.identity_sha256 is None
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
    assert report.release_executor_sha256 == report.source_executor_sha256
    assert (
        report.release_watch_executor_sha256
        == report.source_watch_executor_sha256
    )
    identity_path = Path(report.identity_path)
    assert identity_path == release / MODULE._IDENTITY_FILENAME
    assert identity_path.is_file()
    assert report.identity_sha256 == hashlib.sha256(
        identity_path.read_bytes()
    ).hexdigest()
    identity = __import__("json").loads(
        identity_path.read_text(encoding="utf-8")
    )
    assert identity["release_path"] == str(release)
    assert identity["pinned_source_head"] == MODULE.CHECK.PINNED_SOURCE_HEAD
    assert identity["executor_sha256"] == report.release_executor_sha256
    assert (
        identity["watch_executor_sha256"]
        == report.release_watch_executor_sha256
    )
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



def test_stager_rejects_reviewed_validator_drift_before_preflight_return(
    tmp_path,
    monkeypatch,
):
    source = make_ready_runtime(tmp_path, monkeypatch)
    destination = tmp_path / "runtime-root"
    identities = iter(
        (
            ("commit-a", "validator-sha"),
            ("commit-b", "validator-sha"),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_check_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(
        ValueError,
        match="validator changed during stage preflight",
    ):
        MODULE.stage_runtime(
            source_tree=source,
            destination_root=destination,
        )

    assert destination.exists() is False


def test_stager_rolls_back_current_link_on_post_switch_validator_drift(
    tmp_path,
    monkeypatch,
):
    source = make_ready_runtime(tmp_path, monkeypatch)
    destination = tmp_path / "runtime-root"
    previous = destination / "releases" / "previous"
    previous.mkdir(parents=True)
    current = destination / "current"
    previous_target = os.path.relpath(previous, destination)
    current.symlink_to(previous_target)

    stable = ("commit-a", "validator-sha")
    identities = iter(
        (
            stable,
            stable,
            stable,
            stable,
            ("commit-b", "validator-sha"),
        )
    )
    monkeypatch.setattr(
        MODULE,
        "_check_source_identity",
        lambda: next(identities),
    )

    with pytest.raises(
        ValueError,
        match="validator changed after current-link update",
    ):
        MODULE.stage_runtime(
            source_tree=source,
            destination_root=destination,
            apply=True,
        )

    assert current.is_symlink()
    assert os.readlink(current) == previous_target
    assert current.resolve() == previous.resolve()


def test_stager_validator_capture_rejects_symlink(tmp_path):
    target = tmp_path / "check.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    link = tmp_path / "check-link.py"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        MODULE._capture_regular_file(
            link,
            label="reviewed test validator",
        )



def test_stager_rejects_existing_release_with_different_binary_identity(
    tmp_path,
    monkeypatch,
):
    source = make_ready_runtime(tmp_path, monkeypatch)
    destination = tmp_path / "runtime-root"

    first = MODULE.stage_runtime(
        source_tree=source,
        destination_root=destination,
        apply=True,
    )
    release = Path(first.release_path)
    executor = release / MODULE.CHECK.EXECUTOR_RELATIVE
    executor.write_text("#!/bin/sh\necho drift\n", encoding="utf-8")
    executor.chmod(executor.stat().st_mode | stat.S_IXUSR)

    assert MODULE.CHECK.inspect_runtime(release).runtime_ready is True

    with pytest.raises(
        ValueError,
        match="binary identity does not match prepared source",
    ):
        MODULE.stage_runtime(
            source_tree=source,
            destination_root=destination,
            apply=True,
        )


def test_stager_rejects_source_binary_drift_during_copy(
    tmp_path,
    monkeypatch,
):
    source = make_ready_runtime(tmp_path, monkeypatch)
    destination = tmp_path / "runtime-root"
    executor = source / MODULE.CHECK.EXECUTOR_RELATIVE
    real_copytree = MODULE.shutil.copytree

    def copy_then_drift(*args, **kwargs):
        result = real_copytree(*args, **kwargs)
        executor.write_text("#!/bin/sh\necho changed\n", encoding="utf-8")
        executor.chmod(executor.stat().st_mode | stat.S_IXUSR)
        return result

    monkeypatch.setattr(MODULE.shutil, "copytree", copy_then_drift)

    with pytest.raises(
        ValueError,
        match="prepared source binary identity changed during stage copy",
    ):
        MODULE.stage_runtime(
            source_tree=source,
            destination_root=destination,
            apply=True,
        )

    release = (
        destination
        / "releases"
        / MODULE.CHECK.PINNED_SOURCE_HEAD
    )
    assert release.exists() is False
    assert (destination / "current").exists() is False



def test_stager_rejects_tampered_existing_identity_manifest(
    tmp_path,
    monkeypatch,
):
    source = make_ready_runtime(tmp_path, monkeypatch)
    destination = tmp_path / "runtime-root"
    first = MODULE.stage_runtime(
        source_tree=source,
        destination_root=destination,
        apply=True,
    )
    identity_path = Path(first.identity_path)
    payload = __import__("json").loads(
        identity_path.read_text(encoding="utf-8")
    )
    payload["executor_sha256"] = "0" * 64
    identity_path.write_text(
        __import__("json").dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="runtime identity manifest does not match staged runtime",
    ):
        MODULE.stage_runtime(
            source_tree=source,
            destination_root=destination,
        )
