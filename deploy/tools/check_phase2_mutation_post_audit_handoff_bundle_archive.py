#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import tempfile
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
BUNDLE_VERIFY_TOOL = (
    TOOLS_DIR / "check_phase2_mutation_post_audit_handoff_bundle.py"
)
FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE2_MUTATION_POST_AUDIT_HANDOFF_BUNDLE_ARCHIVE_V1"
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_MEMBER_BYTES = 8 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUNDLE = _load(
    BUNDLE_VERIFY_TOOL,
    "phase2_portable_bundle_for_archive_verify",
)


@dataclass(frozen=True)
class Phase2PortableBundleArchiveVerification:
    archive_path: str
    artifact_type: str
    archive_sha256: str
    archive_size: int
    deterministic_metadata_valid: bool
    members_verified: int
    source_bundle_sha256: str
    source_bundle_verified: bool
    archive_verified: bool
    temporary_extraction_performed: bool
    authorizes_next_action: bool
    requires_fresh_separate_mutation_authorization: bool
    production_file_modified: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _archive_file(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("portable bundle archive must not be a symlink")
    path = raw.resolve(strict=True)
    if not path.is_file():
        raise ValueError("portable bundle archive must be a regular file")
    size = path.stat().st_size
    if size <= 0 or size > _MAX_ARCHIVE_BYTES:
        raise ValueError("portable bundle archive size is invalid")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("portable bundle archive permissions must be 0600")
    return path


def _safe_member_name(name: str) -> str:
    if not name or name.startswith("/") or "\\" in name:
        raise ValueError("portable bundle archive member path is invalid")
    pure = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("portable bundle archive member path is invalid")
    return str(pure)


def _load_manifest_bytes(payload: bytes) -> dict[str, Any]:
    if not payload or len(payload) > _MAX_MEMBER_BYTES:
        raise ValueError("portable bundle archive manifest size is invalid")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("portable bundle archive manifest is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("portable bundle archive manifest must be an object")
    return value


def _hash_entry(value: Any, *, label: str) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError(f"portable bundle archive {label} entry is invalid")
    path = value.get("path")
    digest = value.get("sha256")
    if not isinstance(path, str) or not path:
        raise ValueError(f"portable bundle archive {label} path is invalid")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError(f"portable bundle archive {label} hash is invalid")
    return path, digest


def _expected_member_contract(manifest: dict[str, Any]) -> dict[str, tuple[str, int]]:
    handoff_path, handoff_sha = _hash_entry(
        manifest.get("handoff_snapshot"),
        label="handoff snapshot",
    )
    catalog_path, catalog_sha = _hash_entry(
        manifest.get("catalog_snapshot"),
        label="catalog snapshot",
    )
    post = manifest.get("post_audits")
    if not isinstance(post, list) or not post:
        raise ValueError("portable bundle archive post-audit entries are invalid")

    contract: dict[str, tuple[str, int]] = {
        "manifest.json": ("", 0o600),
        handoff_path: (handoff_sha, 0o600),
        catalog_path: (catalog_sha, 0o600),
        "post-audits": ("", 0o700),
    }
    for item in post:
        path, digest = _hash_entry(item, label="post-audit")
        if path in contract:
            raise ValueError("portable bundle archive duplicate member path")
        contract[path] = (digest, 0o600)
    return contract


def _bundle_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "bundle_verified", False)
        and getattr(report, "historical_handoff_only", False)
        and not getattr(report, "current_lifecycle_rechecked", True)
        and not getattr(report, "authorizes_next_action", True)
        and getattr(
            report,
            "requires_fresh_separate_mutation_authorization",
            False,
        )
        and getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "mutation_executed", True)
    )


def verify_phase2_portable_bundle_archive(
    *,
    archive_path: str | Path,
    repository_root: str | Path = BUNDLE.HANDOFF_VERIFY.REPO_ROOT,
) -> Phase2PortableBundleArchiveVerification:
    path = _archive_file(archive_path)
    archive_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    archive_size = path.stat().st_size

    with tarfile.open(path, mode="r:") as archive:
        members = archive.getmembers()
        if not members:
            raise ValueError("portable bundle archive is empty")

        by_name: dict[str, tarfile.TarInfo] = {}
        for member in members:
            name = _safe_member_name(member.name)
            if name in by_name:
                raise ValueError("portable bundle archive has duplicate members")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError("portable bundle archive contains unsafe member type")
            if not (member.isfile() or member.isdir()):
                raise ValueError("portable bundle archive contains unsupported member type")
            if (
                member.uid != 0
                or member.gid != 0
                or member.uname not in {"", None}
                or member.gname not in {"", None}
                or member.mtime != 0
            ):
                raise ValueError("portable bundle archive metadata is not deterministic")
            if member.isfile() and (
                member.size <= 0 or member.size > _MAX_MEMBER_BYTES
            ):
                raise ValueError("portable bundle archive member size is invalid")
            by_name[name] = member

        manifest_member = by_name.get("manifest.json")
        if manifest_member is None or not manifest_member.isfile():
            raise ValueError("portable bundle archive manifest is missing")
        handle = archive.extractfile(manifest_member)
        if handle is None:
            raise ValueError("portable bundle archive manifest cannot be read")
        manifest_bytes = handle.read()
        manifest = _load_manifest_bytes(manifest_bytes)
        contract = _expected_member_contract(manifest)

        if set(by_name) != set(contract):
            raise ValueError("portable bundle archive member set is invalid")

        payloads: dict[str, bytes] = {}
        for name, (expected_sha, expected_mode) in contract.items():
            member = by_name[name]
            if stat.S_IMODE(member.mode) != expected_mode:
                raise ValueError("portable bundle archive member mode is invalid")
            if name == "post-audits":
                if not member.isdir():
                    raise ValueError("portable bundle archive post-audits entry is not a directory")
                continue
            if not member.isfile():
                raise ValueError("portable bundle archive file entry is invalid")
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError("portable bundle archive member cannot be read")
            data = handle.read()
            payloads[name] = data
            if name != "manifest.json" and hashlib.sha256(data).hexdigest() != expected_sha:
                raise ValueError("portable bundle archive member hash mismatch")

    with tempfile.TemporaryDirectory(prefix="pio-phase2-bundle-verify.") as temp_name:
        root = Path(temp_name) / "bundle"
        root.mkdir(mode=0o700)
        audit_root = root / "post-audits"
        audit_root.mkdir(mode=0o700)
        for name, data in payloads.items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            os.chmod(destination, 0o600)

        bundle = BUNDLE.verify_phase2_portable_handoff_bundle(
            bundle_directory=root,
            repository_root=repository_root,
        )
        if not _bundle_boundary_ok(bundle):
            raise ValueError("archived portable bundle is not verified and non-authorizing")

    manifest_bundle_sha = manifest.get("bundle_sha256")
    if not isinstance(manifest_bundle_sha, str) or not _SHA256.fullmatch(
        manifest_bundle_sha
    ):
        raise ValueError("portable bundle archive source bundle digest is invalid")
    if str(bundle.bundle_sha256) != manifest_bundle_sha:
        raise ValueError("portable bundle archive source bundle digest mismatch")

    return Phase2PortableBundleArchiveVerification(
        archive_path=str(path),
        artifact_type=ARTIFACT_TYPE,
        archive_sha256=archive_sha,
        archive_size=archive_size,
        deterministic_metadata_valid=True,
        members_verified=len(contract),
        source_bundle_sha256=manifest_bundle_sha,
        source_bundle_verified=True,
        archive_verified=True,
        temporary_extraction_performed=True,
        authorizes_next_action=False,
        requires_fresh_separate_mutation_authorization=True,
        production_file_modified=False,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a deterministic portable Phase-2 handoff bundle tar without "
            "trusting archive paths or metadata. Verification uses only local Git "
            "history and bundled evidence and authorizes no mutation."
        )
    )
    parser.add_argument("--archive", required=True)
    parser.add_argument(
        "--repository-root",
        default=str(BUNDLE.HANDOFF_VERIFY.REPO_ROOT),
    )
    args = parser.parse_args()

    report = verify_phase2_portable_bundle_archive(
        archive_path=args.archive,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.archive_verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
