#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


TARGET_PATH = "scripts/phase2-prestate-watch.py"
EXPECTED_LIVE_BLOB = "c5f463615949609b07640ba6db4b05006efa5245"
EXPECTED_TARGET_BLOB = "8ab3f364dff67f4ef1274f75c14ecf027f73f09e"
REVIEWED_TARGET_COMMIT = "deaf07eabd570d287d06e08d1b904d16cc0bddab"


@dataclass(frozen=True)
class RpcEfficiencyPatchReport:
    repository: str
    source_tree: str
    target_path: str
    current_blob: str | None
    source_blob: str | None
    expected_live_blob: str
    expected_target_blob: str
    reviewed_target_commit: str
    status: str
    ready: bool
    applied: bool
    backup_path: str | None
    service_control_performed: bool

    def to_record(self) -> dict:
        return asdict(self)


def git_blob_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _safe_target(root: Path) -> Path:
    relative = PurePosixPath(TARGET_PATH)
    target = root / str(relative)
    resolved_root = root.resolve()
    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("target resolves outside repository") from exc
    return target


def preflight(*, repository: str | Path, source_tree: str | Path) -> RpcEfficiencyPatchReport:
    repo = Path(repository).resolve()
    source = Path(source_tree).resolve()
    if not repo.is_dir():
        raise ValueError(f"repository directory missing: {repo}")
    if not source.is_dir():
        raise ValueError(f"source tree directory missing: {source}")

    live = _safe_target(repo)
    candidate = _safe_target(source)

    if live.is_symlink():
        status = "CONFLICT_LIVE_SYMLINK"
        current_blob = None
    else:
        current_blob = git_blob_sha(live)
        status = "UNKNOWN"

    if candidate.is_symlink():
        source_blob = None
        status = "SOURCE_SYMLINK"
    else:
        source_blob = git_blob_sha(candidate)

    if status == "UNKNOWN":
        if source_blob != EXPECTED_TARGET_BLOB:
            status = "SOURCE_MISMATCH"
        elif current_blob == EXPECTED_TARGET_BLOB:
            status = "ALREADY_TARGET"
        elif current_blob == EXPECTED_LIVE_BLOB:
            status = "READY_UPDATE"
        elif current_blob is None:
            status = "CONFLICT_LIVE_MISSING"
        else:
            status = "CONFLICT_LIVE_MODIFIED"

    return RpcEfficiencyPatchReport(
        repository=str(repo),
        source_tree=str(source),
        target_path=TARGET_PATH,
        current_blob=current_blob,
        source_blob=source_blob,
        expected_live_blob=EXPECTED_LIVE_BLOB,
        expected_target_blob=EXPECTED_TARGET_BLOB,
        reviewed_target_commit=REVIEWED_TARGET_COMMIT,
        status=status,
        ready=status in {"READY_UPDATE", "ALREADY_TARGET"},
        applied=False,
        backup_path=None,
        service_control_performed=False,
    )


def apply_patch(
    *,
    repository: str | Path,
    source_tree: str | Path,
    apply: bool = False,
    backup_dir: str | Path = "/opt/pio-backups",
) -> RpcEfficiencyPatchReport:
    report = preflight(repository=repository, source_tree=source_tree)
    if not apply:
        return report
    if not report.ready:
        raise ValueError(f"preflight not ready: {report.status}")
    if report.status == "ALREADY_TARGET":
        return replace(report, applied=True)

    repo = Path(repository).resolve()
    source = Path(source_tree).resolve()
    live = _safe_target(repo)
    candidate = _safe_target(source)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(backup_dir).resolve() / stamp / TARGET_PATH
    backup.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(live, backup)

    try:
        shutil.copy2(candidate, live)
        if git_blob_sha(live) != EXPECTED_TARGET_BLOB:
            raise ValueError("post-copy target blob mismatch")
    except Exception:
        shutil.copy2(backup, live)
        raise

    return replace(
        report,
        current_blob=EXPECTED_TARGET_BLOB,
        status="APPLIED",
        applied=True,
        backup_path=str(backup),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed selective deployment of the reviewed Phase-2 "
            "prestate watcher RPC-efficiency fix. This tool never controls "
            "systemd services."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--backup-dir", default="/opt/pio-backups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = apply_patch(
        repository=args.repo,
        source_tree=args.source_tree,
        apply=args.apply,
        backup_dir=args.backup_dir,
    )
    print(json.dumps(result.to_record(), indent=2))
    if not result.ready and not result.applied:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
