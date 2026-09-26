from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "tools" / "apply_phase2_state_reader_patch.py"
TARGET = Path("rust-executor/src/state_reader.rs")


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

    dry = subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(repo), "--patch", str(patch)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"ready": true' in dry.stdout
    assert '"applied": false' in dry.stdout
    assert (repo / TARGET).read_text(encoding="utf-8") == "before\n"
    assert (repo / "keep.txt").read_text(encoding="utf-8") == "local-only\n"

    backup_dir = tmp_path / "backups"
    applied = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--repo",
            str(repo),
            "--patch",
            str(patch),
            "--apply",
            "--backup-dir",
            str(backup_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"applied": true' in applied.stdout
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

    completed = subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(repo), "--patch", str(patch)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "patch must modify only" in completed.stderr
    assert (repo / TARGET).read_text(encoding="utf-8") == "before\n"


def test_guarded_patch_fails_closed_on_target_conflict(tmp_path):
    repo = _repo(tmp_path)
    patch = tmp_path / "state-reader.patch"
    _make_patch(repo, TARGET, "after\n", patch)
    (repo / TARGET).write_text("production-local-fix\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(repo), "--patch", str(patch)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert (repo / TARGET).read_text(encoding="utf-8") == "production-local-fix\n"
