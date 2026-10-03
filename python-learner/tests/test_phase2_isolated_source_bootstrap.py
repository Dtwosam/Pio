from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy/tools/bootstrap_phase2_isolated_source.py"
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_phase2_isolated_source",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=path,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def make_origin(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "--quiet")
    git(origin, "config", "user.name", "Pio Test")
    git(origin, "config", "user.email", "pio-test@example.invalid")
    (origin / "README.md").write_text("pinned\n", encoding="utf-8")
    git(origin, "add", "README.md")
    git(origin, "commit", "--quiet", "-m", "pinned source")
    head = git(origin, "rev-parse", "HEAD")
    monkeypatch.setattr(MODULE.CHECK, "PINNED_SOURCE_HEAD", head)
    return origin, head


def test_bootstrap_preflight_is_network_free(tmp_path, monkeypatch):
    origin, head = make_origin(tmp_path, monkeypatch)
    destination = tmp_path / "build" / head

    report = MODULE.bootstrap_pinned_source(
        destination=destination,
        repository_url=str(origin),
    )

    assert report.status == "READY_CREATE"
    assert report.ready is True
    assert report.applied is False
    assert report.network_fetch_performed is False
    assert report.read_only is True
    assert report.source_tree_modified is False
    assert destination.exists() is False


def test_bootstrap_fetches_exact_pin_atomically(tmp_path, monkeypatch):
    origin, head = make_origin(tmp_path, monkeypatch)
    destination = tmp_path / "build" / head

    report = MODULE.bootstrap_pinned_source(
        destination=destination,
        repository_url=str(origin),
        apply=True,
    )

    assert report.status == "ALREADY_PINNED"
    assert report.applied is True
    assert report.network_fetch_performed is True
    assert report.read_only is False
    assert report.source_tree_modified is True
    assert report.existing_reused is False
    assert report.observed_source_head == head
    assert report.tracked_clean is True
    assert git(destination, "rev-parse", "HEAD") == head
    assert git(
        destination,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
    ) == ""


def test_bootstrap_reuses_existing_exact_source_without_fetch(
    tmp_path,
    monkeypatch,
):
    origin, head = make_origin(tmp_path, monkeypatch)
    destination = tmp_path / "build" / head
    MODULE.bootstrap_pinned_source(
        destination=destination,
        repository_url=str(origin),
        apply=True,
    )

    report = MODULE.bootstrap_pinned_source(
        destination=destination,
        repository_url="https://unreachable.invalid/Pio.git",
        apply=True,
    )

    assert report.status == "ALREADY_PINNED"
    assert report.applied is True
    assert report.existing_reused is True
    assert report.network_fetch_performed is False
    assert report.read_only is True
    assert report.source_tree_modified is False


def test_bootstrap_fails_closed_on_existing_source_drift(
    tmp_path,
    monkeypatch,
):
    origin, head = make_origin(tmp_path, monkeypatch)
    destination = tmp_path / "build" / head
    MODULE.bootstrap_pinned_source(
        destination=destination,
        repository_url=str(origin),
        apply=True,
    )
    (destination / "README.md").write_text("drift\n", encoding="utf-8")

    report = MODULE.inspect_pinned_source(
        destination=destination,
        repository_url=str(origin),
    )

    assert report.ready is False
    assert report.status == "CONFLICT_SOURCE_DRIFT"
    with pytest.raises(ValueError, match="not ready"):
        MODULE.bootstrap_pinned_source(
            destination=destination,
            repository_url=str(origin),
            apply=True,
        )


def test_bootstrap_refuses_non_git_existing_directory(
    tmp_path,
    monkeypatch,
):
    origin, head = make_origin(tmp_path, monkeypatch)
    destination = tmp_path / "build" / head
    destination.mkdir(parents=True)

    report = MODULE.inspect_pinned_source(
        destination=destination,
        repository_url=str(origin),
    )

    assert report.ready is False
    assert report.status == "CONFLICT_NOT_GIT_TREE"


@pytest.mark.parametrize(
    "destination",
    (
        "/opt/pio/phase2-build",
        "/opt/pio-phase2-runtime/source",
    ),
)
def test_bootstrap_refuses_protected_runtime_trees(destination):
    with pytest.raises(ValueError, match="outside protected"):
        MODULE.inspect_pinned_source(destination=destination)



@pytest.mark.parametrize(
    "repository_url",
    (
        "https://token@github.com/Dtwosam/Pio.git",
        "https://github.com/Dtwosam/Pio.git?token=secret",
        "https://github.com/Dtwosam/Pio.git#secret",
    ),
)
def test_bootstrap_rejects_secret_bearing_repository_urls(
    tmp_path,
    repository_url,
):
    with pytest.raises(ValueError, match="repository URL"):
        MODULE.inspect_pinned_source(
            destination=tmp_path / "build",
            repository_url=repository_url,
        )
