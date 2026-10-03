#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
HANDOFF_VERIFY_TOOL = (
    TOOLS_DIR / "check_phase2_mutation_post_audit_catalog_handoff_snapshot.py"
)
CATALOG_VERIFY_TOOL = TOOLS_DIR / "check_phase2_mutation_post_audit_catalog.py"
FRESH_VERIFY_TOOL = (
    TOOLS_DIR / "check_phase2_mutation_post_audit_catalog_freshness.py"
)

FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE2_MUTATION_POST_AUDIT_HANDOFF_BUNDLE_V1"
MANIFEST_NAME = "manifest.json"
HANDOFF_NAME = "handoff.snapshot.json"
CATALOG_NAME = "catalog.snapshot.json"
POST_AUDIT_DIR = "post-audits"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HANDOFF_VERIFY = _load(
    HANDOFF_VERIFY_TOOL,
    "phase2_portable_bundle_verify_handoff",
)
CATALOG_VERIFY = _load(
    CATALOG_VERIFY_TOOL,
    "phase2_portable_bundle_verify_catalog",
)
FRESH_VERIFY = _load(
    FRESH_VERIFY_TOOL,
    "phase2_portable_bundle_verify_fresh",
)


@dataclass(frozen=True)
class Phase2PortableHandoffBundleVerification:
    bundle_directory: str
    bundle_sha256: str
    manifest_valid: bool
    handoff_snapshot_sha256: str
    handoff_snapshot_verified: bool
    catalog_snapshot_sha256: str
    catalog_snapshot_verified: bool
    post_audits_expected: int
    post_audits_verified: int
    artifact_hash_set_matches: bool
    fresh_reverification_verified: bool
    evidence_lineage_verified: bool
    bundle_verified: bool
    historical_handoff_only: bool
    current_lifecycle_rechecked: bool
    authorizes_next_action: bool
    requires_fresh_separate_mutation_authorization: bool
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bundle_root(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("portable handoff bundle must not be a symlink")
    root = raw.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("portable handoff bundle must be a directory")
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError("portable handoff bundle directory permissions must be 0700")
    return root


def _private_file(root: Path, relative: str) -> Path:
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        raise ValueError("portable handoff bundle path is invalid")
    path = root / relative
    if path.is_symlink():
        raise ValueError("portable handoff bundle file must not be a symlink")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("portable handoff bundle file escapes bundle root") from exc
    if not resolved.is_file():
        raise ValueError("portable handoff bundle entry must be a regular file")
    if stat.S_IMODE(resolved.stat().st_mode) != 0o600:
        raise ValueError("portable handoff bundle files must have 0600 permissions")
    return resolved


def _read_private_snapshot(
    root: Path,
    relative: str,
    *,
    label: str,
    max_bytes: int = _MAX_MANIFEST_BYTES,
) -> tuple[Path, bytes, os.stat_result]:
    path = _private_file(root, relative)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise ValueError(f"{label} size is invalid")
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise ValueError(f"{label} permissions must be 0600")

        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)

        after = os.fstat(fd)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or not stat.S_ISREG(after.st_mode)
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            raise ValueError(f"{label} changed while reading")
    finally:
        os.close(fd)

    if len(encoded) != before.st_size:
        raise ValueError(f"{label} changed while reading")
    _assert_snapshot_path_stable(path, before, label=label)
    return path, encoded, before


def _assert_snapshot_path_stable(
    path: Path,
    opened_stat: os.stat_result,
    *,
    label: str,
) -> None:
    current = os.stat(path, follow_symlinks=False)
    if (
        current.st_dev != opened_stat.st_dev
        or current.st_ino != opened_stat.st_ino
        or current.st_size != opened_stat.st_size
        or not stat.S_ISREG(current.st_mode)
        or stat.S_IMODE(current.st_mode) != 0o600
    ):
        raise ValueError(f"{label} path changed after read")


def _json_object_bytes(value: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _manifest(root: Path) -> dict[str, Any]:
    _path, encoded, _opened_stat = _read_private_snapshot(
        root,
        MANIFEST_NAME,
        label="portable handoff bundle manifest",
    )
    return _json_object_bytes(
        encoded,
        label="portable handoff bundle manifest",
    )


def _hash_entry(value: Any, *, label: str) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError(f"portable handoff bundle {label} entry is invalid")
    path = value.get("path")
    digest = value.get("sha256")
    if not isinstance(path, str) or not path:
        raise ValueError(f"portable handoff bundle {label} path is invalid")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError(f"portable handoff bundle {label} hash is invalid")
    return path, digest


def _boundary_ok(report: Any, *, verified_field: str) -> bool:
    return bool(
        getattr(report, verified_field, False)
        and getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "mutation_executed", True)
        and not getattr(report, "authorizes_next_action", True)
    )


def verify_phase2_portable_handoff_bundle(
    *,
    bundle_directory: str | Path,
    repository_root: str | Path = HANDOFF_VERIFY.REPO_ROOT,
) -> Phase2PortableHandoffBundleVerification:
    root = _bundle_root(bundle_directory)
    manifest_path, manifest_bytes, manifest_stat = _read_private_snapshot(
        root,
        MANIFEST_NAME,
        label="portable handoff bundle manifest",
    )
    manifest = _json_object_bytes(
        manifest_bytes,
        label="portable handoff bundle manifest",
    )
    expected_fields = {
        "format_version",
        "artifact_type",
        "handoff_snapshot",
        "catalog_snapshot",
        "post_audits",
        "artifacts_seen",
        "evidence_lineage_verified",
        "fresh_reverification_verified",
        "historical_handoff_only",
        "current_lifecycle_rechecked",
        "authorizes_next_action",
        "requires_fresh_separate_mutation_authorization",
        "rpc_called",
        "database_write_performed",
        "service_control_performed",
        "mutation_executed",
        "bundle_sha256",
    }
    if set(manifest) != expected_fields:
        raise ValueError("portable handoff bundle manifest schema is invalid")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError("portable handoff bundle format version is unsupported")
    if manifest.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("portable handoff bundle artifact type is unsupported")

    bundle_sha = manifest.get("bundle_sha256")
    if not isinstance(bundle_sha, str) or not _SHA256.fullmatch(bundle_sha):
        raise ValueError("portable handoff bundle digest is invalid")
    identity = {
        key: value
        for key, value in manifest.items()
        if key != "bundle_sha256"
    }
    if _canonical_sha256(identity) != bundle_sha:
        raise ValueError("portable handoff bundle manifest digest mismatch")

    for field, expected in (
        ("evidence_lineage_verified", True),
        ("fresh_reverification_verified", True),
        ("historical_handoff_only", True),
        ("current_lifecycle_rechecked", False),
        ("authorizes_next_action", False),
        ("requires_fresh_separate_mutation_authorization", True),
        ("rpc_called", False),
        ("database_write_performed", False),
        ("service_control_performed", False),
        ("mutation_executed", False),
    ):
        if manifest.get(field) is not expected:
            raise ValueError(
                f"portable handoff bundle requires {field}={expected!r}"
            )

    handoff_rel, handoff_sha = _hash_entry(
        manifest.get("handoff_snapshot"),
        label="handoff snapshot",
    )
    catalog_rel, catalog_sha = _hash_entry(
        manifest.get("catalog_snapshot"),
        label="catalog snapshot",
    )
    if handoff_rel != HANDOFF_NAME or catalog_rel != CATALOG_NAME:
        raise ValueError(
            "portable handoff bundle snapshot filenames are invalid"
        )

    post_entries = manifest.get("post_audits")
    count = manifest.get("artifacts_seen")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError(
            "portable handoff bundle artifact count is invalid"
        )
    if not isinstance(post_entries, list) or len(post_entries) != count:
        raise ValueError(
            "portable handoff bundle post-audit entries are invalid"
        )

    handoff_path, handoff_bytes, handoff_stat = _read_private_snapshot(
        root,
        handoff_rel,
        label="portable handoff bundle handoff snapshot",
    )
    catalog_path, catalog_bytes, catalog_stat = _read_private_snapshot(
        root,
        catalog_rel,
        label="portable handoff bundle catalog snapshot",
    )
    captured_handoff_sha = _sha256_bytes(handoff_bytes)
    captured_catalog_sha = _sha256_bytes(catalog_bytes)
    if captured_handoff_sha != handoff_sha:
        raise ValueError(
            "portable handoff bundle handoff snapshot hash mismatch"
        )
    if captured_catalog_sha != catalog_sha:
        raise ValueError(
            "portable handoff bundle catalog snapshot hash mismatch"
        )

    expected_audit_paths: set[str] = set()
    expected_hashes: set[str] = set()
    audit_snapshots: dict[str, tuple[Path, os.stat_result]] = {}
    for item in post_entries:
        relative, digest = _hash_entry(item, label="post-audit")
        expected_relative = (
            f"{POST_AUDIT_DIR}/{digest}.post-audit.json"
        )
        if relative != expected_relative:
            raise ValueError(
                "portable handoff bundle post-audit filename/hash mismatch"
            )
        if digest in expected_hashes:
            raise ValueError(
                "portable handoff bundle duplicate post-audit hash"
            )
        expected_hashes.add(digest)
        expected_audit_paths.add(relative)
        path, encoded, opened_stat = _read_private_snapshot(
            root,
            relative,
            label="portable handoff bundle post-audit",
        )
        if _sha256_bytes(encoded) != digest:
            raise ValueError(
                "portable handoff bundle post-audit hash mismatch"
            )
        audit_snapshots[relative] = (path, opened_stat)

    audit_root = root / POST_AUDIT_DIR
    if audit_root.is_symlink() or not audit_root.is_dir():
        raise ValueError(
            "portable handoff bundle post-audit directory is invalid"
        )
    if stat.S_IMODE(audit_root.stat().st_mode) != 0o700:
        raise ValueError(
            "portable handoff bundle post-audit directory permissions "
            "must be 0700"
        )
    actual_audit_paths = {
        str(path.relative_to(root))
        for path in audit_root.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    if actual_audit_paths != expected_audit_paths:
        raise ValueError(
            "portable handoff bundle post-audit file set is invalid"
        )
    if any(
        path.is_symlink() or not path.is_file()
        for path in audit_root.iterdir()
    ):
        raise ValueError(
            "portable handoff bundle post-audit directory "
            "contains invalid entries"
        )

    root_entries = {path.name for path in root.iterdir()}
    if root_entries != {
        MANIFEST_NAME,
        HANDOFF_NAME,
        CATALOG_NAME,
        POST_AUDIT_DIR,
    }:
        raise ValueError(
            "portable handoff bundle contains unexpected top-level entries"
        )

    handoff = (
        HANDOFF_VERIFY.verify_phase2_post_audit_catalog_handoff_snapshot(
            snapshot_path=handoff_path,
            repository_root=repository_root,
        )
    )
    catalog = CATALOG_VERIFY.verify_phase2_post_audit_catalog_snapshot(
        snapshot_path=catalog_path,
        repository_root=repository_root,
    )
    fresh = FRESH_VERIFY.freshly_reverify_phase2_post_audit_catalog(
        snapshot_path=catalog_path,
        artifact_directory=audit_root,
        pattern="*.post-audit.json",
        repository_root=repository_root,
    )
    if not _boundary_ok(
        handoff,
        verified_field="handoff_snapshot_verified",
    ):
        raise ValueError(
            "portable bundle handoff snapshot is not verified"
        )
    if not _boundary_ok(catalog, verified_field="snapshot_verified"):
        raise ValueError(
            "portable bundle catalog snapshot is not verified"
        )
    if not _boundary_ok(
        fresh,
        verified_field="fresh_reverification_verified",
    ):
        raise ValueError(
            "portable bundle post-audit evidence is not freshly verified"
        )

    if str(getattr(handoff, "snapshot_sha256", "")) != captured_handoff_sha:
        raise ValueError(
            "portable bundle handoff snapshot changed during verification"
        )
    if str(getattr(catalog, "snapshot_sha256", "")) != captured_catalog_sha:
        raise ValueError(
            "portable bundle catalog snapshot changed during verification"
        )
    if str(getattr(fresh, "snapshot_sha256", "")) != captured_catalog_sha:
        raise ValueError(
            "portable bundle catalog snapshot changed during fresh verification"
        )

    handoff_payload = _json_object_bytes(
        handoff_bytes,
        label="portable bundle handoff payload",
    )
    handoff_record = handoff_payload.get("handoff")
    if not isinstance(handoff_record, dict):
        raise ValueError(
            "portable bundle handoff payload is invalid"
        )
    if handoff_record.get("snapshot_sha256") != catalog_sha:
        raise ValueError(
            "portable bundle handoff/catalog linkage is invalid"
        )
    if int(
        handoff_record.get("historical_artifacts_seen", -1)
    ) != count:
        raise ValueError(
            "portable bundle handoff artifact count is invalid"
        )

    catalog_payload = _json_object_bytes(
        catalog_bytes,
        label="portable bundle catalog payload",
    )
    entries = catalog_payload.get("catalog", {}).get("entries", [])
    catalog_hashes = {
        entry.get("artifact_sha256")
        for entry in entries
        if isinstance(entry, dict)
    }
    artifact_hash_set_matches = catalog_hashes == expected_hashes
    if not artifact_hash_set_matches:
        raise ValueError(
            "portable bundle artifact hashes do not match catalog snapshot"
        )

    _assert_snapshot_path_stable(
        manifest_path,
        manifest_stat,
        label="portable handoff bundle manifest",
    )
    _assert_snapshot_path_stable(
        handoff_path,
        handoff_stat,
        label="portable handoff bundle handoff snapshot",
    )
    _assert_snapshot_path_stable(
        catalog_path,
        catalog_stat,
        label="portable handoff bundle catalog snapshot",
    )
    for path, opened_stat in audit_snapshots.values():
        _assert_snapshot_path_stable(
            path,
            opened_stat,
            label="portable handoff bundle post-audit",
        )

    verified = bool(
        handoff.handoff_snapshot_verified
        and catalog.snapshot_verified
        and fresh.fresh_reverification_verified
        and artifact_hash_set_matches
        and len(expected_hashes) == count
    )

    return Phase2PortableHandoffBundleVerification(
        bundle_directory=str(root),
        bundle_sha256=bundle_sha,
        manifest_valid=True,
        handoff_snapshot_sha256=handoff_sha,
        handoff_snapshot_verified=bool(
            handoff.handoff_snapshot_verified
        ),
        catalog_snapshot_sha256=catalog_sha,
        catalog_snapshot_verified=bool(catalog.snapshot_verified),
        post_audits_expected=count,
        post_audits_verified=(
            count if fresh.fresh_reverification_verified else 0
        ),
        artifact_hash_set_matches=artifact_hash_set_matches,
        fresh_reverification_verified=bool(
            fresh.fresh_reverification_verified
        ),
        evidence_lineage_verified=verified,
        bundle_verified=verified,
        historical_handoff_only=True,
        current_lifecycle_rechecked=False,
        authorizes_next_action=False,
        requires_fresh_separate_mutation_authorization=True,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Offline-verify a portable Phase-2 handoff bundle from local Git "
            "history and bundled evidence. The bundle is historical evidence only."
        )
    )
    parser.add_argument("--bundle-directory", required=True)
    parser.add_argument("--repository-root", default=str(HANDOFF_VERIFY.REPO_ROOT))
    args = parser.parse_args()

    report = verify_phase2_portable_handoff_bundle(
        bundle_directory=args.bundle_directory,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.bundle_verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
