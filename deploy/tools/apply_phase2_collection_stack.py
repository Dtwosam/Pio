#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import shutil
from typing import Any


DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "manifests"
    / "phase2-collection-integration.json"
)

READY_STATUSES = {
    "ALREADY_TARGET",
    "READY_CREATE",
    "READY_UPDATE",
}


@dataclass(frozen=True)
class CollectionDeployFile:
    path: str
    expected_base_blob: str | None
    target_blob: str
    source_blob: str | None
    current_blob: str | None
    status: str


@dataclass(frozen=True)
class CollectionDeployReport:
    repository: str
    source_tree: str
    manifest: str
    content_ready: bool
    production_deployment_authorized: bool
    deployment_guard_apply_locked: bool
    apply_authorized: bool
    applied: bool
    files_changed: int
    backup_root: str | None
    files: tuple[CollectionDeployFile, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def git_blob_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe deployment path: {value}")
    return str(path)


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    deploy_files = payload.get("deployment_files")
    target_blobs = payload.get("deployment_target_file_blobs")
    base_blobs = payload.get("deployment_base_file_blobs")
    if not isinstance(deploy_files, list) or not deploy_files:
        raise ValueError("manifest deployment_files must be a non-empty list")
    if not isinstance(target_blobs, dict):
        raise ValueError("manifest deployment_target_file_blobs is required")
    if not isinstance(base_blobs, dict):
        raise ValueError("manifest deployment_base_file_blobs is required")

    normalized = [_safe_relative_path(str(value)) for value in deploy_files]
    if len(normalized) != len(set(normalized)):
        raise ValueError("manifest deployment_files contains duplicates")
    if set(normalized) != set(target_blobs):
        raise ValueError("deployment target blob keys must match deployment_files")
    if set(normalized) != set(base_blobs):
        raise ValueError("deployment base blob keys must match deployment_files")

    for item in normalized:
        target = target_blobs[item]
        base = base_blobs[item]
        if (
            not isinstance(target, str)
            or re.fullmatch(r"[0-9a-f]{40}", target) is None
        ):
            raise ValueError(f"invalid target blob for {item}")
        if base is not None and (
            not isinstance(base, str)
            or re.fullmatch(r"[0-9a-f]{40}", base) is None
        ):
            raise ValueError(f"invalid base blob for {item}")

    payload["deployment_files"] = normalized
    return payload


def _resolves_within(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return False
    return True


def preflight_collection_stack(
    *,
    repository: str | Path,
    source_tree: str | Path,
    manifest: str | Path = DEFAULT_MANIFEST,
) -> CollectionDeployReport:
    repo = Path(repository).resolve()
    source = Path(source_tree).resolve()
    manifest_path = Path(manifest).resolve()

    if not repo.is_dir():
        raise ValueError(f"repository directory is missing: {repo}")
    if not source.is_dir():
        raise ValueError(f"source tree directory is missing: {source}")
    if not manifest_path.is_file():
        raise ValueError(f"deployment manifest is missing: {manifest_path}")

    payload = _load_manifest(manifest_path)
    target_blobs = payload["deployment_target_file_blobs"]
    base_blobs = payload["deployment_base_file_blobs"]

    files: list[CollectionDeployFile] = []
    for relative in payload["deployment_files"]:
        target_path = repo / relative
        source_path = source / relative
        target_blob = str(target_blobs[relative])
        base_blob = base_blobs[relative]
        source_blob = None
        current_blob = None

        if not _resolves_within(source, source_path):
            status = "SOURCE_OUTSIDE_TREE"
        elif source_path.is_symlink():
            status = "SOURCE_SYMLINK"
        elif not _resolves_within(repo, target_path):
            status = "TARGET_OUTSIDE_REPOSITORY"
        elif target_path.is_symlink():
            status = "CONFLICT_SYMLINK"
        else:
            source_blob = git_blob_sha(source_path)
            current_blob = git_blob_sha(target_path)
            if source_blob != target_blob:
                status = "SOURCE_MISMATCH"
            elif current_blob == target_blob:
                status = "ALREADY_TARGET"
            elif current_blob is None and base_blob is None:
                status = "READY_CREATE"
            elif current_blob == base_blob:
                status = "READY_UPDATE"
            elif current_blob is None:
                status = "CONFLICT_MISSING"
            elif base_blob is None:
                status = "CONFLICT_UNEXPECTED_EXISTING"
            else:
                status = "CONFLICT_MODIFIED"

        files.append(
            CollectionDeployFile(
                path=relative,
                expected_base_blob=base_blob,
                target_blob=target_blob,
                source_blob=source_blob,
                current_blob=current_blob,
                status=status,
            )
        )

    content_ready = all(
        item.status in READY_STATUSES for item in files
    )
    deployment_authorized = bool(
        payload.get("production_deployment_authorized", False)
    )
    apply_locked = bool(
        payload.get("deployment_guard_apply_locked", True)
    )
    return CollectionDeployReport(
        repository=str(repo),
        source_tree=str(source),
        manifest=str(manifest_path),
        content_ready=content_ready,
        production_deployment_authorized=deployment_authorized,
        deployment_guard_apply_locked=apply_locked,
        apply_authorized=(
            deployment_authorized and not apply_locked
        ),
        applied=False,
        files_changed=sum(
            item.status in {"READY_CREATE", "READY_UPDATE"}
            for item in files
        ),
        backup_root=None,
        files=tuple(files),
    )


def apply_guarded_collection_stack(
    *,
    repository: str | Path,
    source_tree: str | Path,
    manifest: str | Path = DEFAULT_MANIFEST,
    apply: bool = False,
    backup_dir: str | Path = "/opt/pio-backups",
) -> CollectionDeployReport:
    report = preflight_collection_stack(
        repository=repository,
        source_tree=source_tree,
        manifest=manifest,
    )
    if not apply:
        return report
    if not report.content_ready:
        raise ValueError(
            "collection stack preflight has source/base conflicts"
        )
    if not report.apply_authorized:
        raise ValueError(
            "collection stack apply is not authorized by the reviewed manifest"
        )

    repo = Path(repository).resolve()
    source = Path(source_tree).resolve()
    changes = [
        item
        for item in report.files
        if item.status in {"READY_CREATE", "READY_UPDATE"}
    ]
    if not changes:
        return replace(report, applied=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = Path(backup_dir).resolve() / stamp
    backup_root.mkdir(parents=True, exist_ok=False)

    backed_up: list[tuple[Path, Path]] = []
    created: list[Path] = []
    try:
        for item in changes:
            target = repo / item.path
            src = source / item.path
            if item.status == "READY_UPDATE":
                backup = backup_root / item.path
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                backed_up.append((target, backup))
            else:
                created.append(target)

            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)

            if git_blob_sha(target) != item.target_blob:
                raise ValueError(
                    f"post-copy blob mismatch for {item.path}"
                )
    except Exception:
        for target, backup in reversed(backed_up):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        for target in reversed(created):
            if target.exists():
                target.unlink()
        raise

    return replace(
        report,
        applied=True,
        backup_root=str(backup_root),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or apply a reviewed Phase-2 collection source tree "
            "without git checkout/reset or service control."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--backup-dir", default="/opt/pio-backups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = apply_guarded_collection_stack(
        repository=args.repo,
        source_tree=args.source_tree,
        manifest=args.manifest,
        apply=args.apply,
        backup_dir=args.backup_dir,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.content_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
