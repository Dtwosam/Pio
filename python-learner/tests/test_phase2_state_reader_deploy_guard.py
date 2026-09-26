import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "apply_phase2_state_reader_patch.py"
TARGET = Path("rust-executor/src/state_reader.rs")

SPEC = importlib.util.spec_from_file_location(
    "apply_phase2_state_reader_patch",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / TARGET.parent).mkdir(parents=True)
    (repo / TARGET).write_text("before\n", encoding="utf-8")
    (repo / "keep.txt").write_text("keep\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def _make_patch(repo, path, contents, patch_path):
    full = repo / path
    full.write_text(contents, encoding="utf-8")
    patch = _git(repo, "diff", "--", str(path)).stdout
    patch_path.write_text(patch, encoding="utf-8")
    _git(repo, "checkout", "--", str(path))


def test_guarded_patch_dry_run_and_apply_touch_only_state_reader(tmp_path):
    repo = _repo(tmp_path)
    patch = tmp_path / "state-reader.patch"
    _make_patch(repo, TARGET, "after\n", patch)
    (repo / "keep.txt").write_text("local-only\n", encoding="utf-8")

    dry = MODULE.apply_guarded_patch(
        repository=repo,
        patch=patch,
        reference_patch=patch,
    )
    assert dry.ready is True
    assert dry.applied is False
    assert dry.pr7_head == MODULE.PR7_HEAD
    assert len(dry.patch_sha256) == 64
    assert (repo / TARGET).read_text(encoding="utf-8") == "before\n"
    assert (repo / "keep.txt").read_text(encoding="utf-8") == "local-only\n"

    backup_dir = tmp_path / "backups"
    applied = MODULE.apply_guarded_patch(
        repository=repo,
        patch=patch,
        reference_patch=patch,
        apply=True,
        backup_dir=backup_dir,
    )
    assert applied.applied is True
    assert applied.patch_sha256 == dry.patch_sha256
    assert (repo / TARGET).read_text(encoding="utf-8") == "after\n"
    assert (repo / "keep.txt").read_text(encoding="utf-8") == "local-only\n"
    backups = list(backup_dir.glob("*/rust-executor/src/state_reader.rs"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "before\n"


def test_guarded_patch_rejects_multi_file_patch(tmp_path):
    repo = _repo(tmp_path)
    (repo / TARGET).write_text("after\n", encoding="utf-8")
    (repo / "keep.txt").write_text("changed\n", encoding="utf-8")
    patch = tmp_path / "multi.patch"
    patch.write_text(_git(repo, "diff").stdout, encoding="utf-8")
    _git(repo, "checkout", "--", ".")

    with pytest.raises(ValueError, match="patch must modify only"):
        MODULE.apply_guarded_patch(
            repository=repo,
            patch=patch,
            reference_patch=patch,
        )
    assert (repo / TARGET).read_text(encoding="utf-8") == "before\n"


def test_guarded_patch_fails_closed_on_target_conflict(tmp_path):
    repo = _repo(tmp_path)
    patch = tmp_path / "state-reader.patch"
    _make_patch(repo, TARGET, "after\n", patch)
    (repo / TARGET).write_text("production-local-fix\n", encoding="utf-8")

    with pytest.raises(subprocess.CalledProcessError):
        MODULE.apply_guarded_patch(
            repository=repo,
            patch=patch,
            reference_patch=patch,
        )
    assert (
        repo / TARGET
    ).read_text(encoding="utf-8") == "production-local-fix\n"


def test_guarded_patch_rejects_unreviewed_patch_bytes(tmp_path):
    repo = _repo(tmp_path)
    approved = tmp_path / "approved.patch"
    _make_patch(repo, TARGET, "reviewed\n", approved)
    unreviewed = tmp_path / "unreviewed.patch"
    _make_patch(repo, TARGET, "different\n", unreviewed)

    with pytest.raises(ValueError, match="reviewed PR #7 reference patch"):
        MODULE.apply_guarded_patch(
            repository=repo,
            patch=unreviewed,
            reference_patch=approved,
        )
    assert (repo / TARGET).read_text(encoding="utf-8") == "before\n"


def test_bundled_reference_patch_is_single_file_and_pinned_to_pr7():
    reference = MODULE.REFERENCE_PATCH
    assert reference.is_file()
    assert MODULE.changed_paths(
        reference.read_text(encoding="utf-8")
    ) == (str(TARGET),)
    assert MODULE.PR7_HEAD == (
        "ea235e676b700891839f28b0250e23149cfb77f8"
    )
