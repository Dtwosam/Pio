import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "deploy"
    / "tools"
    / "apply_phase2_single_slot_stack_patch.py"
)
TARGET = Path("rust-executor/src/state_reader.rs")

SPEC = importlib.util.spec_from_file_location(
    "apply_phase2_single_slot_stack_patch",
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
    target = repo / TARGET
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (repo / "keep.txt").write_text("keep\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def _make_patch(repo, contents, patch_path):
    target = repo / TARGET
    target.write_text(contents, encoding="utf-8")
    patch_path.write_text(
        _git(repo, "diff", "--", str(TARGET)).stdout,
        encoding="utf-8",
    )
    _git(repo, "checkout", "--", str(TARGET))


def test_combined_guard_dry_run_and_apply_preserve_unrelated_local_edits(
    tmp_path,
):
    repo = _repo(tmp_path)
    patch = tmp_path / "combined.patch"
    _make_patch(repo, "after\n", patch)
    (repo / "keep.txt").write_text("production-local\n", encoding="utf-8")

    dry = MODULE.apply_guarded_patch(
        repository=repo,
        patch=patch,
        reference_patch=patch,
    )
    assert dry.ready is True
    assert dry.applied is False
    assert dry.stack_head == MODULE.STACK_HEAD
    assert len(dry.patch_sha256) == 64
    assert (repo / TARGET).read_text(encoding="utf-8") == "before\n"
    assert (repo / "keep.txt").read_text(encoding="utf-8") == (
        "production-local\n"
    )

    backup_dir = tmp_path / "backups"
    applied = MODULE.apply_guarded_patch(
        repository=repo,
        patch=patch,
        reference_patch=patch,
        apply=True,
        backup_dir=backup_dir,
    )
    assert applied.applied is True
    assert applied.stack_head == MODULE.STACK_HEAD
    assert applied.patch_sha256 == dry.patch_sha256
    assert (repo / TARGET).read_text(encoding="utf-8") == "after\n"
    assert (repo / "keep.txt").read_text(encoding="utf-8") == (
        "production-local\n"
    )
    backups = list(
        backup_dir.glob("*/rust-executor/src/state_reader.rs")
    )
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "before\n"


def test_combined_guard_rejects_unreviewed_patch_bytes(tmp_path):
    repo = _repo(tmp_path)
    approved = tmp_path / "approved.patch"
    _make_patch(repo, "approved\n", approved)
    unreviewed = tmp_path / "unreviewed.patch"
    _make_patch(repo, "different\n", unreviewed)

    with pytest.raises(
        ValueError,
        match="reviewed combined Phase-2 single-slot stack",
    ):
        MODULE.apply_guarded_patch(
            repository=repo,
            patch=unreviewed,
            reference_patch=approved,
        )

    assert (repo / TARGET).read_text(encoding="utf-8") == "before\n"


def test_combined_guard_fails_closed_on_target_conflict(tmp_path):
    repo = _repo(tmp_path)
    patch = tmp_path / "combined.patch"
    _make_patch(repo, "approved\n", patch)
    (repo / TARGET).write_text(
        "production-local-state-reader-fix\n",
        encoding="utf-8",
    )

    with pytest.raises(subprocess.CalledProcessError):
        MODULE.apply_guarded_patch(
            repository=repo,
            patch=patch,
            reference_patch=patch,
        )

    assert (repo / TARGET).read_text(encoding="utf-8") == (
        "production-local-state-reader-fix\n"
    )


def test_bundled_combined_patch_is_single_file_and_pinned():
    reference = MODULE.REFERENCE_PATCH
    assert reference.is_file()
    assert MODULE.changed_paths(
        reference.read_text(encoding="utf-8")
    ) == (str(TARGET),)
    assert MODULE.STACK_HEAD == (
        "59473ee07190c34f5a3264e4a120e20fd8cec338"
    )
    assert MODULE.STACK_TARGET_BLOB_SHA == (
        "30d1435af1329bca07f73d6639539b43503e84e9"
    )


def test_bundled_combined_patch_reconstructs_reviewed_state_reader_blob(
    tmp_path,
):
    repo = tmp_path / "repo"
    target = repo / TARGET
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / TARGET).read_bytes())
    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "main state reader")

    _git(repo, "apply", str(MODULE.REFERENCE_PATCH))
    blob = _git(repo, "hash-object", str(TARGET)).stdout.strip()

    assert blob == MODULE.STACK_TARGET_BLOB_SHA
