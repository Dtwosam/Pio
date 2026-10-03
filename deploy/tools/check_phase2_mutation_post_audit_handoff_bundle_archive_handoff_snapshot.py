#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFF_TOOL_RELATIVE = (
    "deploy/tools/"
    "check_phase2_mutation_post_audit_handoff_bundle_archive_handoff.py"
)
ARTIFACT_TYPE = (
    "PHASE2_MUTATION_POST_AUDIT_HANDOFF_BUNDLE_ARCHIVE_HANDOFF_SNAPSHOT_V1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_ALLOWED_MUTATION_FLAGS = frozenset({"--apply", "--prepare"})
_HANDOFF_FIELDS = frozenset(
    {
        "archive_path",
        "archive_sha256",
        "archive_size",
        "archive_verified",
        "source_bundle_sha256",
        "source_bundle_verified",
        "historical_archive_only",
        "historical_authorizes_next_action",
        "current_state",
        "current_next_action",
        "current_next_tool",
        "current_next_parameters",
        "current_next_mutation_flag",
        "current_attention_required",
        "current_blockers",
        "provider_rate_limit_incident",
        "provider_rate_limit_paused",
        "evidence_lineage_verified",
        "archive_influenced_current_action",
        "next_action_source",
        "attention_required",
        "blockers",
        "authorizes_next_action",
        "requires_fresh_separate_mutation_authorization",
        "read_only",
        "rpc_called",
        "database_write_performed",
        "service_control_performed",
        "mutation_executed",
    }
)


@dataclass(frozen=True)
class Phase2PortableArchiveHandoffSnapshotVerification:
    snapshot_path: str
    snapshot_sha256: str
    snapshot_format_valid: bool
    handoff_payload_sha256: str
    handoff_payload_sha256_matches: bool
    handoff_source_commit: str
    handoff_source_commit_present: bool
    handoff_source_is_ancestor_of_current_head: bool
    handoff_tool_sha256: str
    handoff_tool_sha256_matches: bool
    handoff_schema_valid: bool
    archive_sha256: str
    source_bundle_sha256: str
    evidence_lineage_verified: bool
    non_authorizing_boundary_valid: bool
    recorded_current_state: str
    recorded_current_next_action: str
    recorded_current_next_tool: str | None
    recorded_current_next_mutation_flag: str | None
    handoff_snapshot_verified: bool
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


def _private_json_file(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("archive handoff snapshot must not be a symlink")
    path = raw.resolve(strict=True)
    if not path.is_file():
        raise ValueError("archive handoff snapshot must be a regular file")
    size = path.stat().st_size
    if size <= 0 or size > _MAX_SNAPSHOT_BYTES:
        raise ValueError("archive handoff snapshot size is invalid")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("archive handoff snapshot permissions must be 0600")
    return path


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("archive handoff snapshot is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("archive handoff snapshot must be a JSON object")
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
                raise ValueError(
                    "archive handoff snapshot contains a sensitive field"
                )
            _assert_credential_minimal(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_credential_minimal(item)
        return
    if isinstance(value, str) and _contains_sensitive_text(value):
        raise ValueError("archive handoff snapshot contains sensitive text")


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
    return _run_git(root, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0


def _is_ancestor_of_head(root: Path, commit: str) -> bool:
    return _run_git(root, "merge-base", "--is-ancestor", commit, "HEAD").returncode == 0


def _historical_file_sha256(
    root: Path,
    *,
    commit: str,
    relative_path: str,
) -> str:
    proc = _run_git(root, "show", f"{commit}:{relative_path}")
    if proc.returncode != 0:
        raise ValueError("historical archive handoff tool is unavailable")
    return hashlib.sha256(proc.stdout).hexdigest()


def _hash_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"archive handoff snapshot {key} is invalid")
    return value


def _commit_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not _COMMIT.fullmatch(value):
        raise ValueError(f"archive handoff snapshot {key} is invalid")
    return value


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item for item in value
    )


def _validate_handoff(
    handoff: Any,
) -> tuple[bool, bool, str, str, str, str, str | None, str | None]:
    if not isinstance(handoff, dict) or set(handoff) != _HANDOFF_FIELDS:
        raise ValueError("archive handoff snapshot handoff schema is invalid")
    _assert_credential_minimal(handoff)

    archive_path = handoff.get("archive_path")
    archive_sha = handoff.get("archive_sha256")
    archive_size = handoff.get("archive_size")
    source_bundle_sha = handoff.get("source_bundle_sha256")
    if not isinstance(archive_path, str) or not archive_path:
        raise ValueError("archive handoff recorded archive path is invalid")
    if not isinstance(archive_sha, str) or not _SHA256.fullmatch(archive_sha):
        raise ValueError("archive handoff recorded archive hash is invalid")
    if not _positive_int(archive_size):
        raise ValueError("archive handoff recorded archive size is invalid")
    if (
        not isinstance(source_bundle_sha, str)
        or not _SHA256.fullmatch(source_bundle_sha)
    ):
        raise ValueError("archive handoff source bundle hash is invalid")
    if handoff.get("archive_verified") is not True:
        raise ValueError("archive handoff archive was not verified")
    if handoff.get("source_bundle_verified") is not True:
        raise ValueError("archive handoff source bundle was not verified")
    if handoff.get("historical_archive_only") is not True:
        raise ValueError("archive handoff historical boundary is invalid")
    if handoff.get("historical_authorizes_next_action") is not False:
        raise ValueError("archive handoff historical evidence became authorizing")

    current_state = handoff.get("current_state")
    current_action = handoff.get("current_next_action")
    current_tool = handoff.get("current_next_tool")
    current_flag = handoff.get("current_next_mutation_flag")
    if not isinstance(current_state, str) or not current_state:
        raise ValueError("archive handoff current state is invalid")
    if not isinstance(current_action, str) or not current_action:
        raise ValueError("archive handoff current next action is invalid")
    if current_tool is not None and (
        not isinstance(current_tool, str) or not current_tool
    ):
        raise ValueError("archive handoff current next tool is invalid")
    if current_flag is not None and current_flag not in _ALLOWED_MUTATION_FLAGS:
        raise ValueError("archive handoff mutation flag is invalid")
    if not isinstance(handoff.get("current_next_parameters"), dict):
        raise ValueError("archive handoff current parameters are invalid")
    if not isinstance(handoff.get("current_attention_required"), bool):
        raise ValueError("archive handoff current attention flag is invalid")
    if not _string_list(handoff.get("current_blockers")):
        if handoff.get("current_blockers") != []:
            raise ValueError("archive handoff current blockers are invalid")
    if not isinstance(handoff.get("provider_rate_limit_incident"), bool):
        raise ValueError("archive handoff rate-limit incident flag is invalid")
    if not isinstance(handoff.get("provider_rate_limit_paused"), bool):
        raise ValueError("archive handoff rate-limit pause flag is invalid")
    if not isinstance(handoff.get("attention_required"), bool):
        raise ValueError("archive handoff attention flag is invalid")
    if not _string_list(handoff.get("blockers")):
        if handoff.get("blockers") != []:
            raise ValueError("archive handoff blockers are invalid")

    evidence_verified = handoff.get("evidence_lineage_verified") is True
    boundary_valid = bool(
        handoff.get("archive_influenced_current_action") is False
        and handoff.get("next_action_source")
        == "CURRENT_LIFECYCLE_HANDOFF"
        and handoff.get("authorizes_next_action") is False
        and handoff.get(
            "requires_fresh_separate_mutation_authorization"
        )
        is True
        and handoff.get("read_only") is True
        and handoff.get("rpc_called") is False
        and handoff.get("database_write_performed") is False
        and handoff.get("service_control_performed") is False
        and handoff.get("mutation_executed") is False
    )
    if not evidence_verified:
        raise ValueError("archive handoff evidence lineage is not verified")
    if not boundary_valid:
        raise ValueError("archive handoff non-authorizing boundary is invalid")

    return (
        evidence_verified,
        boundary_valid,
        archive_sha,
        source_bundle_sha,
        current_state,
        current_action,
        current_tool,
        current_flag,
    )


def verify_phase2_portable_archive_current_handoff_snapshot(
    *,
    snapshot_path: str | Path,
    repository_root: str | Path = REPO_ROOT,
) -> Phase2PortableArchiveHandoffSnapshotVerification:
    snapshot_file = _private_json_file(snapshot_path)
    payload = _load_json_object(snapshot_file)
    _assert_credential_minimal(payload)

    if set(payload) != {
        "format_version",
        "artifact_type",
        "handoff_payload_sha256",
        "handoff_source_commit",
        "handoff_tool_sha256",
        "handoff",
    }:
        raise ValueError("archive handoff snapshot top-level schema is invalid")
    if payload.get("format_version") != 1:
        raise ValueError("archive handoff snapshot format version is unsupported")
    if payload.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("archive handoff snapshot artifact type is unsupported")

    payload_sha = _hash_field(payload, "handoff_payload_sha256")
    tool_sha = _hash_field(payload, "handoff_tool_sha256")
    source_commit = _commit_field(payload, "handoff_source_commit")
    handoff = payload.get("handoff")

    (
        evidence_verified,
        boundary_valid,
        archive_sha,
        source_bundle_sha,
        current_state,
        current_action,
        current_tool,
        current_flag,
    ) = _validate_handoff(handoff)
    payload_sha_matches = _canonical_sha256(handoff) == payload_sha

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
                relative_path=HANDOFF_TOOL_RELATIVE,
            )
        except ValueError:
            historical_tool_sha = ""
        tool_sha_matches = historical_tool_sha == tool_sha

    verified = bool(
        payload_sha_matches
        and commit_present
        and ancestor
        and tool_sha_matches
        and evidence_verified
        and boundary_valid
    )

    return Phase2PortableArchiveHandoffSnapshotVerification(
        snapshot_path=str(snapshot_file),
        snapshot_sha256=hashlib.sha256(snapshot_file.read_bytes()).hexdigest(),
        snapshot_format_valid=True,
        handoff_payload_sha256=payload_sha,
        handoff_payload_sha256_matches=payload_sha_matches,
        handoff_source_commit=source_commit,
        handoff_source_commit_present=commit_present,
        handoff_source_is_ancestor_of_current_head=ancestor,
        handoff_tool_sha256=tool_sha,
        handoff_tool_sha256_matches=tool_sha_matches,
        handoff_schema_valid=True,
        archive_sha256=archive_sha,
        source_bundle_sha256=source_bundle_sha,
        evidence_lineage_verified=evidence_verified,
        non_authorizing_boundary_valid=boundary_valid,
        recorded_current_state=current_state,
        recorded_current_next_action=current_action,
        recorded_current_next_tool=current_tool,
        recorded_current_next_mutation_flag=current_flag,
        handoff_snapshot_verified=verified,
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
            "Statically verify a saved portable Phase-2 archive + lifecycle "
            "handoff snapshot. The recorded lifecycle is historical evidence "
            "only and is not rerun or authorized."
        )
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--repository-root", default=str(REPO_ROOT))
    args = parser.parse_args()

    report = verify_phase2_portable_archive_current_handoff_snapshot(
        snapshot_path=args.snapshot,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.handoff_snapshot_verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
