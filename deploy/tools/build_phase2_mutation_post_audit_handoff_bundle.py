#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
from dataclasses import asdict, dataclass
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
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
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_PROTECTED_ROOTS = (
    Path("/opt/pio"),
    Path("/opt/pio/data"),
    Path("/opt/pio-phase2-runtime"),
    Path("/etc/pio"),
    Path("/etc/systemd/system"),
)


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
    "phase2_portable_bundle_handoff_verify",
)
CATALOG_VERIFY = _load(
    CATALOG_VERIFY_TOOL,
    "phase2_portable_bundle_catalog_verify",
)
FRESH_VERIFY = _load(
    FRESH_VERIFY_TOOL,
    "phase2_portable_bundle_fresh_verify",
)


@dataclass(frozen=True)
class Phase2PortableHandoffBundleReport:
    output_directory: str
    artifact_type: str
    bundle_sha256: str
    handoff_snapshot_sha256: str
    catalog_snapshot_sha256: str
    post_audits_copied: int
    evidence_lineage_verified: bool
    fresh_reverification_verified: bool
    historical_handoff_only: bool
    current_lifecycle_rechecked: bool
    bundle_write_performed: bool
    authorizes_next_action: bool
    requires_fresh_separate_mutation_authorization: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _output_directory(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("portable handoff bundle output must not be a symlink")
    path = raw.resolve(strict=False)
    if any(_under(path, root.resolve()) for root in _PROTECTED_ROOTS):
        raise ValueError("portable handoff bundle output is inside a protected production path")
    if path.exists():
        raise ValueError("portable handoff bundle output already exists")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("portable handoff bundle parent must be an existing directory")
    return path


def _json_object(path: str | Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = raw.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    if resolved.stat().st_size <= 0 or resolved.stat().st_size > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} size is invalid")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return resolved, value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contains_sensitive_text(value: str) -> bool:
    folded = value.casefold()
    return any(
        marker in folded
        for marker in (
            "solana_rpc_url=",
            "solana_ws_url=",
            "jupiter_api_key=",
            "helius_api_key=",
            "api-key=",
            "x-api-key",
            "authorization: bearer ",
        )
    )


_SAFE_AUTHORITY_FIELDS = frozenset(
    {
        "authorizes_next_action",
        "historical_authorizes_next_action",
        "requires_fresh_separate_mutation_authorization",
    }
)


def _assert_credential_minimal(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            folded = str(key).casefold()
            if (
                folded not in _SAFE_AUTHORITY_FIELDS
                and any(
                    marker in folded
                    for marker in (
                        "api_key",
                        "apikey",
                        "rpc_url",
                        "rpc_endpoint",
                        "authorization",
                        "secret_key",
                        "password",
                        "credential_value",
                    )
                )
            ):
                raise ValueError("portable handoff bundle contains a sensitive field")
            _assert_credential_minimal(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_credential_minimal(item)
        return
    if isinstance(value, str) and _contains_sensitive_text(value):
        raise ValueError("portable handoff bundle contains sensitive text")


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


def _expected_artifact_hashes(catalog_payload: dict[str, Any]) -> tuple[str, ...]:
    catalog = catalog_payload.get("catalog")
    if not isinstance(catalog, dict):
        raise ValueError("catalog snapshot catalog payload is invalid")
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("catalog snapshot has no post-audit entries")
    hashes: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("catalog snapshot entry is invalid")
        value = entry.get("artifact_sha256")
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise ValueError("catalog snapshot artifact hash is invalid")
        hashes.append(value)
    if len(set(hashes)) != len(hashes):
        raise ValueError("catalog snapshot artifact hashes are not unique")
    return tuple(sorted(hashes))


def _artifact_files(
    directory: str | Path,
    *,
    pattern: str,
    expected_hashes: tuple[str, ...],
) -> dict[str, Path]:
    root = Path(directory).expanduser()
    if root.is_symlink():
        raise ValueError("post-audit artifact directory must not be a symlink")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("post-audit artifact directory must be a directory")
    found: dict[str, Path] = {}
    for path in sorted(root.glob(pattern)):
        if path.is_symlink() or not path.is_file():
            continue
        size = path.stat().st_size
        if size <= 0 or size > _MAX_ARTIFACT_BYTES:
            raise ValueError("post-audit artifact size is invalid")
        digest = _sha256(path)
        if digest in found:
            raise ValueError("duplicate post-audit artifact bytes found")
        found[digest] = path
    expected = set(expected_hashes)
    if set(found) != expected:
        raise ValueError("post-audit artifact hash set does not match catalog snapshot")
    return found


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    data = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    path.write_bytes(data)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _publish_directory_noreplace(source: Path, destination: Path) -> None:
    """
    Atomically publish a completed private directory without replacing a peer.

    Linux renameat2(RENAME_NOREPLACE) is required because plain os.replace()
    can overwrite an empty directory created after the preflight exists check.
    Failing closed is safer than silently degrading to clobber-prone behavior.
    """
    source_stat = os.stat(source, follow_symlinks=False)
    if not stat.S_ISDIR(source_stat.st_mode):
        raise ValueError("portable handoff bundle temp path is not a directory")
    if stat.S_IMODE(source_stat.st_mode) != 0o700:
        raise ValueError("portable handoff bundle temp directory permissions are not 0700")

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ValueError(
            "atomic no-clobber directory publication is unavailable"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int

    at_fdcwd = -100
    rename_noreplace = 1
    result = renameat2(
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(destination),
        rename_noreplace,
    )
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise ValueError(
                "portable handoff bundle output appeared before publish"
            )
        if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
            raise ValueError(
                "atomic no-clobber directory publication is unavailable"
            )
        raise OSError(
            error,
            os.strerror(error),
            str(destination),
        )

    published_stat = os.stat(destination, follow_symlinks=False)
    if (
        published_stat.st_dev != source_stat.st_dev
        or published_stat.st_ino != source_stat.st_ino
        or not stat.S_ISDIR(published_stat.st_mode)
        or stat.S_IMODE(published_stat.st_mode) != 0o700
    ):
        raise ValueError(
            "portable handoff bundle publication identity mismatch"
        )

    parent_fd = os.open(
        destination.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)

    current = os.stat(destination, follow_symlinks=False)
    if (
        current.st_dev != published_stat.st_dev
        or current.st_ino != published_stat.st_ino
        or not stat.S_ISDIR(current.st_mode)
        or stat.S_IMODE(current.st_mode) != 0o700
    ):
        raise ValueError(
            "portable handoff bundle path changed after publish"
        )


def build_phase2_portable_handoff_bundle(
    *,
    handoff_snapshot_path: str | Path,
    catalog_snapshot_path: str | Path,
    artifact_directory: str | Path,
    output_directory: str | Path,
    artifact_pattern: str = "*.post-audit.json",
    repository_root: str | Path = HANDOFF_VERIFY.REPO_ROOT,
) -> Phase2PortableHandoffBundleReport:
    output = _output_directory(output_directory)

    handoff_path, handoff_payload = _json_object(
        handoff_snapshot_path,
        label="catalog handoff snapshot",
    )
    catalog_path, catalog_payload = _json_object(
        catalog_snapshot_path,
        label="catalog snapshot",
    )
    _assert_credential_minimal(handoff_payload)
    _assert_credential_minimal(catalog_payload)

    handoff = HANDOFF_VERIFY.verify_phase2_post_audit_catalog_handoff_snapshot(
        snapshot_path=handoff_path,
        repository_root=repository_root,
    )
    catalog = CATALOG_VERIFY.verify_phase2_post_audit_catalog_snapshot(
        snapshot_path=catalog_path,
        repository_root=repository_root,
    )
    fresh = FRESH_VERIFY.freshly_reverify_phase2_post_audit_catalog(
        snapshot_path=catalog_path,
        artifact_directory=artifact_directory,
        pattern=artifact_pattern,
        repository_root=repository_root,
    )

    if not _boundary_ok(handoff, verified_field="handoff_snapshot_verified"):
        raise ValueError("catalog handoff snapshot is not verified and non-authorizing")
    if not _boundary_ok(catalog, verified_field="snapshot_verified"):
        raise ValueError("catalog snapshot is not verified and non-authorizing")
    if not _boundary_ok(fresh, verified_field="fresh_reverification_verified"):
        raise ValueError("post-audit artifacts are not freshly reverified")

    handoff_record = handoff_payload.get("handoff")
    if not isinstance(handoff_record, dict):
        raise ValueError("catalog handoff snapshot payload is invalid")
    catalog_sha = _sha256(catalog_path)
    if handoff_record.get("snapshot_sha256") != catalog_sha:
        raise ValueError("handoff snapshot does not reference the supplied catalog snapshot")
    if int(handoff_record.get("historical_artifacts_seen", -1)) != int(
        catalog.artifacts_seen
    ):
        raise ValueError("handoff and catalog artifact counts do not match")

    expected_hashes = _expected_artifact_hashes(catalog_payload)
    artifacts = _artifact_files(
        artifact_directory,
        pattern=artifact_pattern,
        expected_hashes=expected_hashes,
    )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "handoff_snapshot": {
            "path": HANDOFF_NAME,
            "sha256": _sha256(handoff_path),
        },
        "catalog_snapshot": {
            "path": CATALOG_NAME,
            "sha256": catalog_sha,
        },
        "post_audits": [
            {
                "path": f"{POST_AUDIT_DIR}/{digest}.post-audit.json",
                "sha256": digest,
            }
            for digest in expected_hashes
        ],
        "artifacts_seen": len(expected_hashes),
        "evidence_lineage_verified": True,
        "fresh_reverification_verified": True,
        "historical_handoff_only": True,
        "current_lifecycle_rechecked": False,
        "authorizes_next_action": False,
        "requires_fresh_separate_mutation_authorization": True,
        "rpc_called": False,
        "database_write_performed": False,
        "service_control_performed": False,
        "mutation_executed": False,
    }
    _assert_credential_minimal(identity)
    manifest = {**identity, "bundle_sha256": _canonical_sha256(identity)}

    temp = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=str(output.parent),
        )
    )
    try:
        os.chmod(temp, stat.S_IRWXU)
        audit_root = temp / POST_AUDIT_DIR
        audit_root.mkdir(mode=0o700)
        shutil.copyfile(handoff_path, temp / HANDOFF_NAME)
        shutil.copyfile(catalog_path, temp / CATALOG_NAME)
        os.chmod(temp / HANDOFF_NAME, 0o600)
        os.chmod(temp / CATALOG_NAME, 0o600)

        for digest in expected_hashes:
            destination = audit_root / f"{digest}.post-audit.json"
            shutil.copyfile(artifacts[digest], destination)
            os.chmod(destination, 0o600)
            if _sha256(destination) != digest:
                raise ValueError("copied post-audit artifact hash mismatch")

        _write_json(temp / MANIFEST_NAME, manifest)

        if output.exists() or output.is_symlink():
            raise ValueError("portable handoff bundle output appeared before publish")
        _publish_directory_noreplace(temp, output)
        temp = None  # type: ignore[assignment]
    finally:
        if isinstance(temp, Path) and temp.exists():
            shutil.rmtree(temp)

    if stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise ValueError("portable handoff bundle directory permissions are not 0700")

    return Phase2PortableHandoffBundleReport(
        output_directory=str(output),
        artifact_type=ARTIFACT_TYPE,
        bundle_sha256=manifest["bundle_sha256"],
        handoff_snapshot_sha256=manifest["handoff_snapshot"]["sha256"],
        catalog_snapshot_sha256=manifest["catalog_snapshot"]["sha256"],
        post_audits_copied=len(expected_hashes),
        evidence_lineage_verified=True,
        fresh_reverification_verified=True,
        historical_handoff_only=True,
        current_lifecycle_rechecked=False,
        bundle_write_performed=True,
        authorizes_next_action=False,
        requires_fresh_separate_mutation_authorization=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a private portable Phase-2 handoff bundle from verified "
            "catalog/handoff snapshots and freshly reverified post-audit evidence. "
            "The bundle is historical evidence only and authorizes no mutation."
        )
    )
    parser.add_argument("--handoff-snapshot", required=True)
    parser.add_argument("--catalog-snapshot", required=True)
    parser.add_argument("--artifact-directory", required=True)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--artifact-pattern", default="*.post-audit.json")
    parser.add_argument("--repository-root", default=str(HANDOFF_VERIFY.REPO_ROOT))
    args = parser.parse_args()

    report = build_phase2_portable_handoff_bundle(
        handoff_snapshot_path=args.handoff_snapshot,
        catalog_snapshot_path=args.catalog_snapshot,
        artifact_directory=args.artifact_directory,
        output_directory=args.output_directory,
        artifact_pattern=args.artifact_pattern,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))


if __name__ == "__main__":
    main()
