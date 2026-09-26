#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess


TARGET_PATH = "rust-executor/src/state_reader.rs"


@dataclass(frozen=True)
class PatchResult:
    repository: str
    patch: str
    target: str
    ready: bool
    applied: bool
    backup: str | None

    def to_record(self) -> dict[str, object]:
        return asdict(self)


def changed_paths(patch_text: str) -> tuple[str, ...]:
    paths: set[str] = set()
    for line in patch_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
            raise ValueError("unsupported diff header")
        left = parts[2][2:]
        right = parts[3][2:]
        if left != right:
            raise ValueError("renames are not allowed")
        paths.add(left)
    if not paths:
        raise ValueError("patch contains no git diff")
    return tuple(sorted(paths))


def run_git_apply(repo: Path, patch: Path, *, check_only: bool) -> None:
    command = ["git", "-C", str(repo), "apply"]
    if check_only:
        command.append("--check")
    command.extend(["--whitespace=error-all", str(patch)])
    subprocess.run(command, check=True)


def apply_guarded_patch(
    *,
    repository: str | Path,
    patch: str | Path,
    apply: bool = False,
    backup_dir: str | Path = "/opt/pio-backups",
) -> PatchResult:
    repo = Path(repository).resolve()
    patch_path = Path(patch).resolve()
    target = repo / TARGET_PATH
    if not (repo / ".git").exists():
        raise ValueError(f"repository is not a git working tree: {repo}")
    if not target.is_file():
        raise ValueError(f"target file does not exist: {target}")
    if not patch_path.is_file():
        raise ValueError(f"patch file does not exist: {patch_path}")

    paths = changed_paths(patch_path.read_text(encoding="utf-8"))
    if paths != (TARGET_PATH,):
        raise ValueError(
            f"patch must modify only {TARGET_PATH}; found: {', '.join(paths)}"
        )

    run_git_apply(repo, patch_path, check_only=True)
    if not apply:
        return PatchResult(
            repository=str(repo),
            patch=str(patch_path),
            target=TARGET_PATH,
            ready=True,
            applied=False,
            backup=None,
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = Path(backup_dir).resolve() / stamp
    backup = backup_root / TARGET_PATH
    backup.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(target, backup)

    try:
        run_git_apply(repo, patch_path, check_only=False)
    except Exception:
        shutil.copy2(backup, target)
        raise

    return PatchResult(
        repository=str(repo),
        patch=str(patch_path),
        target=TARGET_PATH,
        ready=True,
        applied=True,
        backup=str(backup),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or selectively apply a patch that modifies only "
            "rust-executor/src/state_reader.rs. This tool never pulls, "
            "checks out, resets, rebuilds, or restarts services."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--patch", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", default="/opt/pio-backups")
    args = parser.parse_args()

    result = apply_guarded_patch(
        repository=args.repo,
        patch=args.patch,
        apply=args.apply,
        backup_dir=args.backup_dir,
    )
    print(json.dumps(result.to_record(), indent=2))


if __name__ == "__main__":
    main()
