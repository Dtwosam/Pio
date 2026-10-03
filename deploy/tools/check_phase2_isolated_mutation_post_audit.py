#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
AUDIT_TOOL = TOOLS_DIR / "check_phase2_isolated_mutation_execution_receipt.py"
_ARTIFACT_TYPE = "PHASE2_MUTATION_POST_AUDIT_V1"
_DEPLOY_SURFACE_DOMAIN = b"PIO_DEPLOY_SURFACE_V1\0"
_AUDIT_TOOL_RELATIVE = (
    "deploy/tools/check_phase2_isolated_mutation_execution_receipt.py"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_ALLOWED_MUTATION_FLAGS = frozenset({"--apply", "--prepare"})
_EXPECTED_POST_STATES_V1: dict[str, tuple[str, ...]] = {
    "SOURCE_BOOTSTRAP_REQUIRED": (
        "SOURCE_PREPARATION_REQUIRED",
    ),
    "SOURCE_PREPARATION_REQUIRED": (
        "RUNTIME_STAGING_READY",
    ),
    "RUNTIME_STAGING_READY": (
        "SYSTEMD_UNITS_NOT_READY",
        "DETECTOR_ACTIVATION_READY",
        "ACTIVE_TOPOLOGY_NOT_READY",
    ),
    "SYSTEMD_UNITS_NOT_READY": (
        "DETECTOR_ACTIVATION_READY",
        "ACTIVE_TOPOLOGY_NOT_READY",
    ),
    "DETECTOR_ACTIVATION_READY": (
        "SMOKE_REQUIRED",
        "ACTIVE_TOPOLOGY_NOT_READY",
    ),
    "SMOKE_REQUIRED": (
        "TIMER_ACTIVATION_READY",
    ),
    "TIMER_ACTIVATION_READY": (
        "RUNNING_HEALTHY",
        "RUNNING_ATTENTION_REQUIRED",
        "RATE_LIMIT_PAUSE_REQUIRED",
    ),
    "RATE_LIMIT_PAUSE_REQUIRED": (
        "RATE_LIMIT_PAUSED",
    ),
}


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AUDIT = _load(AUDIT_TOOL, "phase2_historical_mutation_post_audit")


@dataclass(frozen=True)
class Phase2MutationPostAuditVerification:
    artifact_path: str
    artifact_sha256: str
    artifact_format_valid: bool
    audit_payload_sha256: str
    audit_payload_sha256_matches: bool
    audit_source_commit: str
    audit_source_commit_present: bool
    audit_source_is_ancestor_of_current_head: bool
    audit_deploy_surface_sha256_matches: bool
    audit_deploy_surface_files_matches: bool
    audit_tool_sha256_matches: bool
    execution_receipt_path: str
    execution_receipt_sha256: str
    execution_receipt_override_used: bool
    execution_receipt_sha256_matches: bool
    execution_receipt_valid: bool
    preview_path: str
    preview_override_used: bool
    preview_sha256_matches: bool
    preview_identity_matches: bool
    mutation_argv_sha256_matches: bool
    prior_state: str
    prior_next_action: str
    prior_next_tool: str
    current_state: str
    current_next_action: str
    current_next_tool: str | None
    current_next_parameters: dict[str, Any]
    current_next_mutation_flag: str | None
    postcondition_record_valid: bool
    static_audit_verified: bool
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    mutation_executed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _private_json_file(value: str | Path, *, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    path = raw.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{label} must be a regular file")
    size = path.stat().st_size
    if size <= 0 or size > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{label} size is invalid")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError(f"{label} permissions must be 0600")
    return path


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} JSON must be an object")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
                raise ValueError("saved mutation post-audit contains a sensitive field")
            _assert_credential_minimal(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_credential_minimal(item)
        return
    if isinstance(value, str) and _contains_sensitive_text(value):
        raise ValueError("saved mutation post-audit contains sensitive text")


def _hash_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"saved mutation post-audit {key} is invalid")
    return value


def _validate_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    _assert_credential_minimal(payload)
    if payload.get("format_version") != 1:
        raise ValueError("saved mutation post-audit format version is unsupported")
    if payload.get("artifact_type") != _ARTIFACT_TYPE:
        raise ValueError("saved mutation post-audit artifact type is unsupported")

    for key in (
        "execution_receipt_sha256",
        "audit_payload_sha256",
        "audit_tool_sha256",
        "audit_deploy_surface_sha256",
    ):
        _hash_field(payload, key)

    source_commit = payload.get("audit_source_commit")
    if not isinstance(source_commit, str) or not _COMMIT.fullmatch(source_commit):
        raise ValueError("saved mutation post-audit source commit is invalid")

    file_count = payload.get("audit_deploy_surface_files")
    if (
        isinstance(file_count, bool)
        or not isinstance(file_count, int)
        or file_count <= 0
    ):
        raise ValueError("saved mutation post-audit deploy file count is invalid")

    audit = payload.get("audit")
    if not isinstance(audit, dict):
        raise ValueError("saved mutation post-audit audit payload is invalid")
    return audit


def _run_git(
    repository_root: Path,
    *args: str,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repository_root),
        capture_output=True,
        check=False,
    )


def _repository_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"repository root is missing: {root}")
    top = _run_git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise ValueError("repository root is not a Git worktree")
    try:
        observed = Path(top.stdout.decode("utf-8").strip()).resolve()
    except UnicodeDecodeError as exc:
        raise ValueError("repository root Git output is invalid") from exc
    if observed != root:
        raise ValueError("repository root is not the Git worktree root")
    return root


def _resolve_commit(repository_root: Path, commit: str) -> str:
    if not _COMMIT.fullmatch(commit):
        raise ValueError("historical audit source commit is invalid")
    proc = _run_git(repository_root, "rev-parse", f"{commit}^{{commit}}")
    if proc.returncode != 0:
        raise ValueError("historical audit source commit is unavailable")
    try:
        resolved = proc.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("historical audit source commit output is invalid") from exc
    if not _COMMIT.fullmatch(resolved):
        raise ValueError("historical audit source commit resolution is invalid")
    return resolved


def _is_ancestor_of_head(repository_root: Path, commit: str) -> bool:
    proc = _run_git(
        repository_root,
        "merge-base",
        "--is-ancestor",
        commit,
        "HEAD",
    )
    return proc.returncode == 0


def _deploy_surface_identity_at_commit(
    repository_root: Path,
    commit: str,
) -> tuple[str, str, int]:
    resolved = _resolve_commit(repository_root, commit)
    listing = _run_git(
        repository_root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        resolved,
        "--",
        "deploy",
    )
    if listing.returncode != 0:
        raise ValueError("cannot enumerate historical deploy surface")

    entries: list[tuple[str, str]] = []
    for raw in listing.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, path_raw = raw.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split()
            relative = path_raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("historical deploy tree entry is invalid") from exc
        if (
            object_type != "blob"
            or mode not in {"100644", "100755"}
            or not relative.startswith("deploy/")
        ):
            raise ValueError(
                f"historical deploy surface contains non-regular file: {relative}"
            )
        entries.append((relative, object_id))

    entries.sort(key=lambda item: item[0])
    if not entries:
        raise ValueError("historical deploy surface is empty")

    digest = hashlib.sha256()
    digest.update(_DEPLOY_SURFACE_DOMAIN)
    for relative, object_id in entries:
        blob = _run_git(repository_root, "cat-file", "blob", object_id)
        if blob.returncode != 0:
            raise ValueError(
                f"cannot read historical deploy blob: {relative}"
            )
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(blob.stdout).to_bytes(8, "big"))
        digest.update(blob.stdout)

    return resolved, digest.hexdigest(), len(entries)


def _historical_file_sha256(
    repository_root: Path,
    *,
    commit: str,
    relative_path: str,
) -> str:
    resolved = _resolve_commit(repository_root, commit)
    proc = _run_git(
        repository_root,
        "cat-file",
        "blob",
        f"{resolved}:{relative_path}",
    )
    if proc.returncode != 0:
        raise ValueError(f"historical reviewed file is unavailable: {relative_path}")
    return _sha256_bytes(proc.stdout)


def _postcondition_record_valid(audit: dict[str, Any]) -> bool:
    prior = audit.get("prior_state")
    current = audit.get("current_state")
    raw_expected = audit.get("expected_post_states")
    if (
        not isinstance(prior, str)
        or not prior
        or not isinstance(current, str)
        or not current
        or not isinstance(raw_expected, (list, tuple))
        or not raw_expected
        or not all(isinstance(item, str) and item for item in raw_expected)
    ):
        return False

    expected = tuple(raw_expected)
    contract = _EXPECTED_POST_STATES_V1.get(prior, ())
    if expected != contract:
        return False
    if current == prior or current not in expected:
        return False

    next_parameters = audit.get("current_next_parameters")
    if not isinstance(next_parameters, dict):
        return False
    _assert_credential_minimal(next_parameters)

    next_flag = audit.get("current_next_mutation_flag")
    if next_flag is not None and next_flag not in _ALLOWED_MUTATION_FLAGS:
        return False

    return bool(
        audit.get("receipt_terminal") is True
        and audit.get("audit_integrity_valid") is True
        and audit.get("mutation_succeeded") is True
        and audit.get("post_state_expected") is True
        and audit.get("post_mutation_progress_observed") is True
        and audit.get("post_mutation_verified") is True
        and audit.get("read_only") is True
        and audit.get("rpc_called") is False
        and audit.get("database_write_performed") is False
        and audit.get("service_control_performed") is False
        and audit.get("mutation_executed") is False
    )


def _receipt_record_consistent(
    *,
    audit: dict[str, Any],
    receipt: dict[str, Any],
    status: str,
    terminal: bool,
) -> bool:
    return bool(
        audit.get("receipt_status") == status
        and audit.get("receipt_terminal") is terminal
        and audit.get("mutation_launched") is receipt.get("mutation_launched")
        and audit.get("mutation_completed") is receipt.get("mutation_completed")
        and audit.get("mutation_succeeded") is receipt.get("mutation_succeeded")
        and audit.get("outcome_known") is receipt.get("outcome_known")
        and audit.get("exit_code") == receipt.get("exit_code")
        and audit.get("execution_failure_category")
        == receipt.get("execution_failure_category")
    )


def verify_saved_mutation_post_audit(
    *,
    artifact_path: str | Path,
    execution_receipt_path: str | Path | None = None,
    preview_path: str | Path | None = None,
    repository_root: str | Path = REPO_ROOT,
) -> Phase2MutationPostAuditVerification:
    artifact_file = _private_json_file(
        artifact_path,
        label="saved mutation post-audit",
    )
    artifact = _load_json_object(
        artifact_file,
        label="saved mutation post-audit",
    )
    audit = _validate_artifact(artifact)
    artifact_sha = _sha256_bytes(artifact_file.read_bytes())

    audit_payload_matches = (
        _canonical_sha256(audit) == artifact["audit_payload_sha256"]
    )
    postcondition_valid = _postcondition_record_valid(audit)

    root = _repository_root(repository_root)
    source_commit = str(artifact["audit_source_commit"])
    commit_present = False
    ancestor = False
    deploy_sha_matches = False
    deploy_files_matches = False
    tool_sha_matches = False
    try:
        (
            resolved_commit,
            historical_deploy_sha,
            historical_deploy_files,
        ) = _deploy_surface_identity_at_commit(root, source_commit)
        commit_present = True
        ancestor = _is_ancestor_of_head(root, resolved_commit)
        deploy_sha_matches = (
            historical_deploy_sha
            == artifact["audit_deploy_surface_sha256"]
        )
        deploy_files_matches = (
            historical_deploy_files
            == artifact["audit_deploy_surface_files"]
        )
        historical_tool_sha = _historical_file_sha256(
            root,
            commit=resolved_commit,
            relative_path=_AUDIT_TOOL_RELATIVE,
        )
        tool_sha_matches = historical_tool_sha == artifact["audit_tool_sha256"]
    except ValueError:
        resolved_commit = source_commit

    receipt_raw = (
        execution_receipt_path
        if execution_receipt_path is not None
        else audit.get("execution_receipt_path")
    )
    if not isinstance(receipt_raw, (str, Path)) or not str(receipt_raw):
        raise ValueError("saved mutation post-audit receipt path is invalid")
    receipt_file = _private_json_file(
        receipt_raw,
        label="mutation execution receipt",
    )
    receipt_override_used = execution_receipt_path is not None
    receipt_actual_sha = _sha256_bytes(receipt_file.read_bytes())
    receipt_sha_matches = bool(
        receipt_actual_sha == artifact["execution_receipt_sha256"]
        and audit.get("execution_receipt_sha256")
        == artifact["execution_receipt_sha256"]
    )
    receipt = _load_json_object(
        receipt_file,
        label="mutation execution receipt",
    )
    status, terminal = AUDIT._validate_receipt(receipt)
    receipt_valid = _receipt_record_consistent(
        audit=audit,
        receipt=receipt,
        status=status,
        terminal=terminal,
    )

    preview_raw = (
        preview_path
        if preview_path is not None
        else receipt.get("preview_path")
    )
    if not isinstance(preview_raw, (str, Path)) or not str(preview_raw):
        raise ValueError("saved mutation post-audit preview path is invalid")
    preview_file = _private_json_file(
        preview_raw,
        label="mutation preview",
    )
    preview_override_used = preview_path is not None
    (
        _preview,
        preview_sha_matches,
        preview_identity_matches,
        argv_matches,
    ) = AUDIT._validate_preview_chain(
        receipt=receipt,
        preview_path=preview_file,
    )
    preview_sha_matches = bool(
        preview_sha_matches
        and audit.get("preview_sha256_matches") is True
    )
    preview_identity_matches = bool(
        preview_identity_matches
        and audit.get("preview_identity_matches") is True
    )
    argv_matches = bool(
        argv_matches
        and audit.get("mutation_argv_sha256_matches") is True
    )

    format_valid = True
    static_verified = bool(
        format_valid
        and audit_payload_matches
        and commit_present
        and ancestor
        and deploy_sha_matches
        and deploy_files_matches
        and tool_sha_matches
        and receipt_sha_matches
        and receipt_valid
        and preview_sha_matches
        and preview_identity_matches
        and argv_matches
        and postcondition_valid
    )

    return Phase2MutationPostAuditVerification(
        artifact_path=str(artifact_file),
        artifact_sha256=artifact_sha,
        artifact_format_valid=format_valid,
        audit_payload_sha256=str(artifact["audit_payload_sha256"]),
        audit_payload_sha256_matches=audit_payload_matches,
        audit_source_commit=resolved_commit,
        audit_source_commit_present=commit_present,
        audit_source_is_ancestor_of_current_head=ancestor,
        audit_deploy_surface_sha256_matches=deploy_sha_matches,
        audit_deploy_surface_files_matches=deploy_files_matches,
        audit_tool_sha256_matches=tool_sha_matches,
        execution_receipt_path=str(receipt_file),
        execution_receipt_sha256=str(artifact["execution_receipt_sha256"]),
        execution_receipt_override_used=receipt_override_used,
        execution_receipt_sha256_matches=receipt_sha_matches,
        execution_receipt_valid=receipt_valid,
        preview_path=str(preview_file),
        preview_override_used=preview_override_used,
        preview_sha256_matches=preview_sha_matches,
        preview_identity_matches=preview_identity_matches,
        mutation_argv_sha256_matches=argv_matches,
        prior_state=str(audit.get("prior_state", "")),
        prior_next_action=str(audit.get("prior_next_action", "")),
        prior_next_tool=str(audit.get("prior_next_tool", "")),
        current_state=str(audit.get("current_state", "")),
        current_next_action=str(audit.get("current_next_action", "")),
        current_next_tool=(
            str(audit["current_next_tool"])
            if audit.get("current_next_tool") is not None
            else None
        ),
        current_next_parameters=dict(
            audit.get("current_next_parameters", {})
        ),
        current_next_mutation_flag=(
            str(audit["current_next_mutation_flag"])
            if audit.get("current_next_mutation_flag") is not None
            else None
        ),
        postcondition_record_valid=postcondition_valid,
        static_audit_verified=static_verified,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Historically verify a saved Phase-2 mutation post-audit without "
            "rerunning lifecycle state. The recorded deploy surface and audit "
            "tool are reconstructed from Git objects at the recorded commit."
        )
    )
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--execution-receipt")
    parser.add_argument("--preview")
    parser.add_argument("--repository-root", default=str(REPO_ROOT))
    args = parser.parse_args()

    report = verify_saved_mutation_post_audit(
        artifact_path=args.artifact,
        execution_receipt_path=args.execution_receipt,
        preview_path=args.preview,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.static_audit_verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
