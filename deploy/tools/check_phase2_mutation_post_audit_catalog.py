#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_TOOL_RELATIVE = "deploy/tools/catalog_phase2_mutation_post_audits.py"
_ARTIFACT_TYPE = "PHASE2_MUTATION_POST_AUDIT_CATALOG_SNAPSHOT_V1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_ALLOWED_MUTATION_FLAGS = frozenset({"--apply", "--prepare"})
_CATALOG_FIELDS = frozenset(
    {
        "artifact_directory",
        "pattern",
        "artifacts_seen",
        "artifacts_verified",
        "artifacts_failed",
        "duplicate_artifact_hashes",
        "duplicate_receipt_hashes",
        "entries",
        "catalog_stable_during_scan",
        "all_verified",
        "read_only",
        "rpc_called",
        "database_write_performed",
        "service_control_performed",
        "mutation_executed",
    }
)
_ENTRY_FIELDS = frozenset(
    {
        "artifact_path",
        "artifact_sha256",
        "verification_status",
        "failure_category",
        "audit_source_commit",
        "execution_receipt_sha256",
        "prior_state",
        "current_state",
        "current_next_action",
        "current_next_tool",
        "current_next_mutation_flag",
    }
)


@dataclass(frozen=True)
class Phase2PostAuditCatalogSnapshotVerification:
    snapshot_path: str
    snapshot_sha256: str
    snapshot_format_valid: bool
    catalog_payload_sha256: str
    catalog_payload_sha256_matches: bool
    catalog_source_commit: str
    catalog_source_commit_present: bool
    catalog_source_is_ancestor_of_current_head: bool
    catalog_tool_sha256: str
    catalog_tool_sha256_matches: bool
    catalog_schema_valid: bool
    artifacts_seen: int
    artifacts_verified: int
    unique_artifact_identities: bool
    unique_receipt_identities: bool
    snapshot_verified: bool
    artifacts_reverified: bool
    historical_snapshot_only: bool
    authorizes_next_action: bool
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _private_json_file(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("post-audit catalog snapshot must not be a symlink")
    path = raw.resolve(strict=True)
    if not path.is_file():
        raise ValueError("post-audit catalog snapshot must be a regular file")
    size = path.stat().st_size
    if size <= 0 or size > _MAX_SNAPSHOT_BYTES:
        raise ValueError("post-audit catalog snapshot size is invalid")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("post-audit catalog snapshot permissions must be 0600")
    return path


def _read_snapshot_bytes(path: Path) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(
            "post-audit catalog snapshot cannot be opened safely"
        ) from exc

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(
                "post-audit catalog snapshot must be a regular file"
            )
        if opened.st_size <= 0 or opened.st_size > _MAX_SNAPSHOT_BYTES:
            raise ValueError(
                "post-audit catalog snapshot size is invalid"
            )
        if stat.S_IMODE(opened.st_mode) != 0o600:
            raise ValueError(
                "post-audit catalog snapshot permissions must be 0600"
            )

        with os.fdopen(os.dup(fd), "rb") as handle:
            payload = handle.read(_MAX_SNAPSHOT_BYTES + 1)

        finished = os.fstat(fd)
        if (
            finished.st_size != opened.st_size
            or finished.st_mtime_ns != opened.st_mtime_ns
            or finished.st_ctime_ns != opened.st_ctime_ns
        ):
            raise ValueError(
                "post-audit catalog snapshot changed while its byte snapshot was read"
            )
        if len(payload) != opened.st_size:
            raise ValueError(
                "post-audit catalog snapshot byte snapshot size changed during read"
            )
        return payload, opened
    finally:
        os.close(fd)


def _assert_snapshot_path_stable(
    path: Path,
    opened: os.stat_result,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(
            "post-audit catalog snapshot path changed during verification"
        ) from exc

    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
        or stat.S_IMODE(current.st_mode) != 0o600
    ):
        raise ValueError(
            "post-audit catalog snapshot path changed during verification"
        )


def _load_json_object_bytes(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("post-audit catalog snapshot is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("post-audit catalog snapshot must be a JSON object")
    return value


def _load_json_object(path: Path) -> dict[str, Any]:
    """
    Backward-compatible safe loader for reviewed sibling tools.

    New verification code should prefer one captured byte snapshot end-to-end.
    This wrapper still uses the no-follow bounded reader and path-stability check.
    """
    payload, opened = _read_snapshot_bytes(path)
    value = _load_json_object_bytes(payload)
    _assert_snapshot_path_stable(path, opened)
    return value


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
        )
    )


def _assert_credential_minimal(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            folded = str(key).casefold()
            if any(
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
            ):
                raise ValueError("post-audit catalog snapshot contains a sensitive field")
            _assert_credential_minimal(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_credential_minimal(item)
        return
    if isinstance(value, str) and _contains_sensitive_text(value):
        raise ValueError("post-audit catalog snapshot contains sensitive text")


def _repository_root(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("repository root must not be a symlink")
    root = raw.resolve(strict=True)
    if not root.is_dir() or not (root / ".git").exists():
        raise ValueError("repository root must be a Git working tree")
    return root


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        check=False,
    )


def _commit_present(root: Path, commit: str) -> bool:
    proc = _run_git(root, "cat-file", "-e", f"{commit}^{{commit}}")
    return proc.returncode == 0


def _is_ancestor_of_head(root: Path, commit: str) -> bool:
    proc = _run_git(root, "merge-base", "--is-ancestor", commit, "HEAD")
    return proc.returncode == 0


def _historical_file_sha256(root: Path, *, commit: str, relative_path: str) -> str:
    proc = _run_git(root, "show", f"{commit}:{relative_path}")
    if proc.returncode != 0:
        raise ValueError("historical catalog tool is unavailable")
    return hashlib.sha256(proc.stdout).hexdigest()


def _hash_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"post-audit catalog snapshot {key} is invalid")
    return value


def _commit_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not _COMMIT.fullmatch(value):
        raise ValueError(f"post-audit catalog snapshot {key} is invalid")
    return value


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_catalog_record(
    catalog: Any,
) -> tuple[int, int, bool, bool]:
    if not isinstance(catalog, dict) or set(catalog) != _CATALOG_FIELDS:
        raise ValueError("post-audit catalog snapshot catalog schema is invalid")
    _assert_credential_minimal(catalog)

    artifact_directory = catalog.get("artifact_directory")
    pattern = catalog.get("pattern")
    if not isinstance(artifact_directory, str) or not artifact_directory:
        raise ValueError("post-audit catalog snapshot artifact directory is invalid")
    if (
        not isinstance(pattern, str)
        or not pattern
        or "/" in pattern
        or "\\" in pattern
    ):
        raise ValueError("post-audit catalog snapshot pattern is invalid")

    seen = catalog.get("artifacts_seen")
    verified = catalog.get("artifacts_verified")
    failed = catalog.get("artifacts_failed")
    duplicate_artifacts = catalog.get("duplicate_artifact_hashes")
    duplicate_receipts = catalog.get("duplicate_receipt_hashes")
    if (
        not _positive_int(seen)
        or not _positive_int(verified)
        or verified != seen
        or not _nonnegative_int(failed)
        or failed != 0
    ):
        raise ValueError("post-audit catalog snapshot verification counts are invalid")
    if not _nonnegative_int(duplicate_artifacts) or duplicate_artifacts != 0:
        raise ValueError("post-audit catalog snapshot has duplicate artifact identities")
    if not _nonnegative_int(duplicate_receipts) or duplicate_receipts != 0:
        raise ValueError("post-audit catalog snapshot has duplicate receipt identities")
    if catalog.get("catalog_stable_during_scan") is not True:
        raise ValueError("post-audit catalog snapshot was not built from a stable scan")
    if catalog.get("all_verified") is not True:
        raise ValueError("post-audit catalog snapshot is not fully verified")
    if catalog.get("read_only") is not True:
        raise ValueError("post-audit catalog snapshot crossed read-only boundary")
    for field in (
        "rpc_called",
        "database_write_performed",
        "service_control_performed",
        "mutation_executed",
    ):
        if catalog.get(field) is not False:
            raise ValueError(
                f"post-audit catalog snapshot requires catalog {field}=false"
            )

    entries = catalog.get("entries")
    if not isinstance(entries, list) or len(entries) != seen:
        raise ValueError("post-audit catalog snapshot entry count is invalid")

    artifact_hashes: list[str] = []
    receipt_hashes: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
            raise ValueError("post-audit catalog snapshot entry schema is invalid")
        if entry.get("verification_status") != "VERIFIED":
            raise ValueError("post-audit catalog snapshot contains unverified entry")
        if entry.get("failure_category") is not None:
            raise ValueError("post-audit catalog snapshot verified entry has failure")
        artifact_sha = entry.get("artifact_sha256")
        receipt_sha = entry.get("execution_receipt_sha256")
        source_commit = entry.get("audit_source_commit")
        if not isinstance(artifact_sha, str) or not _SHA256.fullmatch(artifact_sha):
            raise ValueError("post-audit catalog snapshot artifact hash is invalid")
        if not isinstance(receipt_sha, str) or not _SHA256.fullmatch(receipt_sha):
            raise ValueError("post-audit catalog snapshot receipt hash is invalid")
        if not isinstance(source_commit, str) or not _COMMIT.fullmatch(source_commit):
            raise ValueError("post-audit catalog snapshot source commit is invalid")
        for field in (
            "artifact_path",
            "prior_state",
            "current_state",
            "current_next_action",
        ):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ValueError(
                    f"post-audit catalog snapshot entry {field} is invalid"
                )
        next_tool = entry.get("current_next_tool")
        if next_tool is not None and (
            not isinstance(next_tool, str) or not next_tool
        ):
            raise ValueError("post-audit catalog snapshot next tool is invalid")
        next_flag = entry.get("current_next_mutation_flag")
        if next_flag is not None and next_flag not in _ALLOWED_MUTATION_FLAGS:
            raise ValueError("post-audit catalog snapshot mutation flag is invalid")
        artifact_hashes.append(artifact_sha)
        receipt_hashes.append(receipt_sha)

    unique_artifacts = len(set(artifact_hashes)) == len(artifact_hashes)
    unique_receipts = len(set(receipt_hashes)) == len(receipt_hashes)
    if not unique_artifacts or not unique_receipts:
        raise ValueError("post-audit catalog snapshot identity uniqueness is invalid")
    return seen, verified, unique_artifacts, unique_receipts


def verify_phase2_post_audit_catalog_snapshot(
    *,
    snapshot_path: str | Path,
    repository_root: str | Path = REPO_ROOT,
) -> Phase2PostAuditCatalogSnapshotVerification:
    snapshot_file = _private_json_file(snapshot_path)
    snapshot_bytes, opened_stat = _read_snapshot_bytes(snapshot_file)
    payload = _load_json_object_bytes(snapshot_bytes)
    _assert_credential_minimal(payload)

    if payload.get("format_version") != 1:
        raise ValueError("post-audit catalog snapshot format version is unsupported")
    if payload.get("artifact_type") != _ARTIFACT_TYPE:
        raise ValueError("post-audit catalog snapshot artifact type is unsupported")

    catalog_payload_sha = _hash_field(payload, "catalog_payload_sha256")
    catalog_tool_sha = _hash_field(payload, "catalog_tool_sha256")
    source_commit = _commit_field(payload, "catalog_source_commit")
    catalog = payload.get("catalog")

    seen, verified, unique_artifacts, unique_receipts = _validate_catalog_record(
        catalog
    )
    catalog_payload_matches = _canonical_sha256(catalog) == catalog_payload_sha

    root = _repository_root(repository_root)
    commit_present = _commit_present(root, source_commit)
    ancestor = False
    tool_sha_matches = False
    if commit_present:
        ancestor = _is_ancestor_of_head(root, source_commit)
        try:
            historical_tool_sha = _historical_file_sha256(
                root,
                commit=source_commit,
                relative_path=CATALOG_TOOL_RELATIVE,
            )
        except ValueError:
            historical_tool_sha = ""
        tool_sha_matches = historical_tool_sha == catalog_tool_sha

    format_valid = True
    snapshot_verified = bool(
        format_valid
        and catalog_payload_matches
        and commit_present
        and ancestor
        and tool_sha_matches
        and unique_artifacts
        and unique_receipts
    )

    _assert_snapshot_path_stable(snapshot_file, opened_stat)

    return Phase2PostAuditCatalogSnapshotVerification(
        snapshot_path=str(snapshot_file),
        snapshot_sha256=hashlib.sha256(snapshot_bytes).hexdigest(),
        snapshot_format_valid=format_valid,
        catalog_payload_sha256=catalog_payload_sha,
        catalog_payload_sha256_matches=catalog_payload_matches,
        catalog_source_commit=source_commit,
        catalog_source_commit_present=commit_present,
        catalog_source_is_ancestor_of_current_head=ancestor,
        catalog_tool_sha256=catalog_tool_sha,
        catalog_tool_sha256_matches=tool_sha_matches,
        catalog_schema_valid=True,
        artifacts_seen=seen,
        artifacts_verified=verified,
        unique_artifact_identities=unique_artifacts,
        unique_receipt_identities=unique_receipts,
        snapshot_verified=snapshot_verified,
        artifacts_reverified=False,
        historical_snapshot_only=True,
        authorizes_next_action=False,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Statically verify a saved Phase-2 post-audit catalog snapshot and "
            "its historical catalog-tool lineage. Underlying post-audit files "
            "are not re-read or re-verified."
        )
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--repository-root", default=str(REPO_ROOT))
    args = parser.parse_args()

    report = verify_phase2_post_audit_catalog_snapshot(
        snapshot_path=args.snapshot,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.snapshot_verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
