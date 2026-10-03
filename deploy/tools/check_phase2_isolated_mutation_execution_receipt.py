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
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
LIFECYCLE_TOOL = TOOLS_DIR / "check_phase2_isolated_lifecycle_handoff.py"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_TERMINAL_STATUSES = frozenset(
    {
        "COMPLETED",
        "ABORTED_BEFORE_LAUNCH",
        "OUTCOME_UNKNOWN_AFTER_LAUNCH",
    }
)


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LIFECYCLE = _load(LIFECYCLE_TOOL, "phase2_mutation_receipt_lifecycle")


@dataclass(frozen=True)
class Phase2MutationReceiptAudit:
    execution_receipt_path: str
    execution_receipt_sha256: str
    receipt_status: str
    receipt_terminal: bool
    receipt_integrity_valid: bool
    preview_path: str
    preview_present: bool
    preview_sha256_matches: bool
    preview_identity_matches: bool
    mutation_argv_sha256_matches: bool
    prior_state: str
    prior_next_action: str
    prior_next_tool: str
    current_state: str
    current_next_action: str
    current_next_tool: str | None
    lifecycle_attention_required: bool
    lifecycle_blockers: tuple[str, ...]
    state_changed: bool
    mutation_launched: bool
    mutation_completed: bool
    mutation_succeeded: bool
    outcome_known: bool
    exit_code: int | None
    execution_failure_category: str | None
    audit_integrity_valid: bool
    post_mutation_progress_observed: bool
    post_mutation_verified: bool
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
            key_folded = str(key).casefold()
            if any(
                marker in key_folded
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
                raise ValueError("execution receipt contains a sensitive field")
            _assert_credential_minimal(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_credential_minimal(item)
        return
    if isinstance(value, str) and _contains_sensitive_text(value):
        raise ValueError("execution receipt contains sensitive text")


def _hash_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"execution receipt {key} is invalid")
    return value


def _bool_field(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"execution receipt {key} is invalid")
    return value


def _validate_receipt(payload: dict[str, Any]) -> tuple[str, bool]:
    _assert_credential_minimal(payload)
    if payload.get("format_version") != 1:
        raise ValueError("execution receipt format version is unsupported")
    if payload.get("receipt_type") != "PHASE2_REVIEWED_MUTATION_EXECUTION_V1":
        raise ValueError("execution receipt type is unsupported")
    status = payload.get("status")
    if status not in _TERMINAL_STATUSES | {"PENDING"}:
        raise ValueError("execution receipt status is invalid")

    for key in (
        "preview_sha256",
        "deploy_surface_sha256",
        "mutation_fingerprint",
        "mutation_tool_sha256",
        "mutation_argv_sha256",
    ):
        _hash_field(payload, key)

    source_commit = payload.get("reviewed_source_commit")
    if not isinstance(source_commit, str) or not _COMMIT.fullmatch(source_commit):
        raise ValueError("execution receipt reviewed_source_commit is invalid")
    deploy_files = payload.get("deploy_surface_files")
    if (
        isinstance(deploy_files, bool)
        or not isinstance(deploy_files, int)
        or deploy_files <= 0
    ):
        raise ValueError("execution receipt deploy_surface_files is invalid")

    for key in (
        "mutation_launched",
        "mutation_completed",
        "mutation_succeeded",
        "outcome_known",
        "raw_stderr_persisted",
    ):
        _bool_field(payload, key)
    if payload["raw_stderr_persisted"]:
        raise ValueError("execution receipt reports persisted raw stderr")

    execution_sha = payload.get("execution_report_sha256")
    if execution_sha is not None and (
        not isinstance(execution_sha, str)
        or not _SHA256.fullmatch(execution_sha)
    ):
        raise ValueError("execution receipt execution_report_sha256 is invalid")

    launched = payload["mutation_launched"]
    completed = payload["mutation_completed"]
    succeeded = payload["mutation_succeeded"]
    known = payload["outcome_known"]
    exit_code = payload.get("exit_code")

    if status == "PENDING":
        consistent = bool(
            not launched
            and not completed
            and not succeeded
            and not known
            and exit_code is None
            and execution_sha is None
            and payload.get("completed_at") is None
        )
    elif status == "COMPLETED":
        consistent = bool(
            launched
            and completed
            and known
            and isinstance(exit_code, int)
            and not isinstance(exit_code, bool)
            and execution_sha is not None
            and isinstance(payload.get("completed_at"), str)
        )
        if succeeded and exit_code != 0:
            consistent = False
    elif status == "ABORTED_BEFORE_LAUNCH":
        consistent = bool(
            not launched
            and not completed
            and not succeeded
            and known
            and exit_code is None
            and execution_sha is None
            and payload.get("failure_category")
            == "EXECUTION_GUARD_FAILED_BEFORE_LAUNCH"
        )
    else:
        consistent = bool(
            launched
            and not completed
            and not succeeded
            and not known
            and exit_code is None
            and execution_sha is None
            and payload.get("failure_category")
            == "MUTATION_RUNNER_FAILED_OUTCOME_UNKNOWN"
        )

    if not consistent:
        raise ValueError("execution receipt outcome fields are inconsistent")
    return str(status), status in _TERMINAL_STATUSES


def _validate_preview_chain(
    *,
    receipt: dict[str, Any],
    preview_path: Path,
) -> tuple[dict[str, Any], bool, bool, bool]:
    payload = _load_json_object(preview_path, label="mutation preview")
    actual_sha = _sha256_bytes(preview_path.read_bytes())
    sha_matches = actual_sha == receipt["preview_sha256"]

    identity_matches = bool(
        payload.get("format_version") == 2
        and payload.get("fingerprint_schema") == "PHASE2_MUTATION_PREVIEW_V2"
        and isinstance(payload.get("state"), str)
        and bool(payload.get("state"))
        and isinstance(payload.get("next_action"), str)
        and bool(payload.get("next_action"))
        and isinstance(payload.get("next_tool"), str)
        and bool(payload.get("next_tool"))
        and payload.get("reviewed_source_commit")
        == receipt["reviewed_source_commit"]
        and payload.get("deploy_surface_sha256")
        == receipt["deploy_surface_sha256"]
        and payload.get("deploy_surface_files")
        == receipt["deploy_surface_files"]
        and payload.get("mutation_fingerprint")
        == receipt["mutation_fingerprint"]
        and payload.get("mutation_tool_sha256")
        == receipt["mutation_tool_sha256"]
        and bool(payload.get("preflight_succeeded"))
        and bool(payload.get("mutation_rendered"))
        and not bool(payload.get("mutation_executed"))
        and bool(payload.get("read_only"))
    )

    argv = payload.get("mutation_argv")
    argv_matches = bool(
        isinstance(argv, list)
        and argv
        and all(isinstance(item, str) for item in argv)
        and _canonical_sha256(argv) == receipt["mutation_argv_sha256"]
    )
    return payload, sha_matches, identity_matches, argv_matches


def _lifecycle_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
    )


def audit_mutation_execution_receipt(
    *,
    execution_receipt_path: str | Path,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = "/opt/pio/data/phase2-isolated-smoke-receipt.json",
    max_receipt_age_seconds: int = 1800,
    source_tree: str | Path | None = None,
    repository_url: str = LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
) -> Phase2MutationReceiptAudit:
    receipt_file = _private_json_file(
        execution_receipt_path,
        label="mutation execution receipt",
    )
    receipt = _load_json_object(
        receipt_file,
        label="mutation execution receipt",
    )
    status, terminal = _validate_receipt(receipt)
    receipt_sha = _sha256_bytes(receipt_file.read_bytes())

    preview_raw = receipt.get("preview_path")
    if not isinstance(preview_raw, str) or not preview_raw:
        raise ValueError("execution receipt preview_path is invalid")
    preview_file = _private_json_file(
        preview_raw,
        label="mutation preview",
    )
    preview, sha_matches, identity_matches, argv_matches = (
        _validate_preview_chain(
            receipt=receipt,
            preview_path=preview_file,
        )
    )

    lifecycle = LIFECYCLE.inspect_lifecycle_handoff(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
        source_tree=source_tree,
        repository_url=repository_url,
    )
    if not _lifecycle_boundary_ok(lifecycle):
        raise ValueError("Phase-2 lifecycle audit crossed the read-only boundary")

    prior_state = str(preview.get("state", ""))
    prior_action = str(preview.get("next_action", ""))
    prior_tool_raw = preview.get("next_tool")
    prior_tool = str(prior_tool_raw) if prior_tool_raw is not None else ""
    current_state = str(lifecycle.state)
    state_changed = bool(prior_state and current_state != prior_state)

    receipt_integrity_valid = True
    audit_integrity_valid = bool(
        receipt_integrity_valid
        and sha_matches
        and identity_matches
        and argv_matches
    )
    mutation_succeeded = bool(receipt["mutation_succeeded"])
    progress_observed = bool(
        terminal
        and status == "COMPLETED"
        and mutation_succeeded
        and state_changed
    )
    verified = bool(
        audit_integrity_valid
        and terminal
        and status == "COMPLETED"
        and mutation_succeeded
        and progress_observed
    )

    execution_failure_category = receipt.get("execution_failure_category")
    if execution_failure_category is not None:
        execution_failure_category = str(execution_failure_category)

    return Phase2MutationReceiptAudit(
        execution_receipt_path=str(receipt_file),
        execution_receipt_sha256=receipt_sha,
        receipt_status=status,
        receipt_terminal=terminal,
        receipt_integrity_valid=receipt_integrity_valid,
        preview_path=str(preview_file),
        preview_present=True,
        preview_sha256_matches=sha_matches,
        preview_identity_matches=identity_matches,
        mutation_argv_sha256_matches=argv_matches,
        prior_state=prior_state,
        prior_next_action=prior_action,
        prior_next_tool=prior_tool,
        current_state=current_state,
        current_next_action=str(lifecycle.next_action),
        current_next_tool=(
            str(lifecycle.next_tool)
            if lifecycle.next_tool is not None
            else None
        ),
        lifecycle_attention_required=bool(lifecycle.attention_required),
        lifecycle_blockers=tuple(str(item) for item in lifecycle.blockers),
        state_changed=state_changed,
        mutation_launched=bool(receipt["mutation_launched"]),
        mutation_completed=bool(receipt["mutation_completed"]),
        mutation_succeeded=mutation_succeeded,
        outcome_known=bool(receipt["outcome_known"]),
        exit_code=receipt.get("exit_code"),
        execution_failure_category=execution_failure_category,
        audit_integrity_valid=audit_integrity_valid,
        post_mutation_progress_observed=progress_observed,
        post_mutation_verified=verified,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a Phase-2 mutation execution receipt and report current "
            "zero-RPC lifecycle state without executing any mutation."
        )
    )
    parser.add_argument("--execution-receipt", required=True)
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument(
        "--receipt-path",
        default="/opt/pio/data/phase2-isolated-smoke-receipt.json",
    )
    parser.add_argument("--max-receipt-age-seconds", type=int, default=1800)
    parser.add_argument("--source-tree")
    parser.add_argument(
        "--repository-url",
        default=LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    args = parser.parse_args()

    report = audit_mutation_execution_receipt(
        execution_receipt_path=args.execution_receipt,
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt_path,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
        source_tree=args.source_tree,
        repository_url=args.repository_url,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.post_mutation_verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
