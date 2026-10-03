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
import subprocess
import sys
import tempfile
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
HANDOFF_TOOL = TOOLS_DIR / "check_phase2_mutation_post_audit_catalog_handoff.py"
HANDOFF_TOOL_RELATIVE = (
    "deploy/tools/check_phase2_mutation_post_audit_catalog_handoff.py"
)
_ARTIFACT_TYPE = "PHASE2_MUTATION_POST_AUDIT_CATALOG_HANDOFF_SNAPSHOT_V1"
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
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


HANDOFF = _load(HANDOFF_TOOL, "phase2_saved_post_audit_catalog_handoff")
_HANDOFF_TOOL_SHA256_AT_LOAD = hashlib.sha256(
    HANDOFF_TOOL.read_bytes()
).hexdigest()


@dataclass(frozen=True)
class Phase2SavedPostAuditCatalogHandoff:
    output_path: str
    artifact_type: str
    artifact_sha256: str
    handoff_payload_sha256: str
    handoff_source_commit: str
    handoff_tool_sha256: str
    snapshot_sha256: str
    evidence_lineage_verified: bool
    fresh_reverification_requested: bool
    current_state: str
    current_next_action: str
    current_next_tool: str | None
    current_next_mutation_flag: str | None
    attention_required: bool
    bytes_written: int
    file_mode: str
    artifact_write_performed: bool
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


def _output_path(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("catalog handoff snapshot output must not be a symlink")
    path = raw.resolve(strict=False)
    if any(_under(path, root.resolve()) for root in _PROTECTED_ROOTS):
        raise ValueError(
            "catalog handoff snapshot output is inside a protected production path"
        )
    if path.exists():
        raise ValueError("catalog handoff snapshot output already exists")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError(
            "catalog handoff snapshot parent must be an existing directory"
        )
    return path


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
                raise ValueError(
                    "catalog handoff snapshot contains a sensitive field"
                )
            _assert_credential_minimal(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_credential_minimal(item)
        return
    if isinstance(value, str) and _contains_sensitive_text(value):
        raise ValueError("catalog handoff snapshot contains sensitive text")


def _run_git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )


def _handoff_source_identity() -> tuple[str, str]:
    if HANDOFF_TOOL.is_symlink() or not HANDOFF_TOOL.is_file():
        raise ValueError("reviewed catalog handoff tool is missing or symlinked")

    head = _run_git("rev-parse", "HEAD")
    if head.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = head.stdout.decode("utf-8").strip()
    if not _COMMIT.fullmatch(commit):
        raise ValueError("reviewed source commit is invalid")

    historical = _run_git("show", f"{commit}:{HANDOFF_TOOL_RELATIVE}")
    if historical.returncode != 0:
        raise ValueError(
            "catalog handoff tool is not present at reviewed source commit"
        )
    current = HANDOFF_TOOL.read_bytes()
    current_sha = hashlib.sha256(current).hexdigest()
    if current_sha != _HANDOFF_TOOL_SHA256_AT_LOAD:
        raise ValueError("catalog handoff tool bytes changed after module load")
    if historical.stdout != current:
        raise ValueError(
            "catalog handoff tool bytes do not match reviewed source commit"
        )
    return commit, current_sha


def _handoff_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "evidence_lineage_verified", False)
        and not getattr(report, "snapshot_influenced_current_action", True)
        and getattr(report, "next_action_source", None)
        == "CURRENT_LIFECYCLE_HANDOFF"
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


def _atomic_write_new(path: Path, payload: dict[str, Any]) -> tuple[str, int]:
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )

    fd: int | None = None
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        temp_path = Path(temp_name)
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

        if path.is_symlink():
            raise ValueError("catalog handoff snapshot output became a symlink")
        if path.exists():
            raise ValueError(
                "catalog handoff snapshot output appeared before publish"
            )
        os.replace(temp_path, path)
        temp_path = None
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd is not None:
            os.close(fd)
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    saved = path.read_bytes()
    if saved != encoded:
        raise ValueError("saved catalog handoff snapshot bytes do not match")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("catalog handoff snapshot permissions are not 0600")
    return hashlib.sha256(saved).hexdigest(), len(saved)


def save_phase2_post_audit_catalog_handoff_snapshot(
    *,
    snapshot_path: str | Path,
    output_path: str | Path,
    artifact_directory: str | Path | None = None,
    artifact_pattern: str = "*.post-audit.json",
    repository_root: str | Path = HANDOFF.CATALOG_VERIFY.REPO_ROOT,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    receipt_path: str | Path = (
        "/opt/pio/data/phase2-isolated-smoke-receipt.json"
    ),
    max_receipt_age_seconds: int = 1800,
    source_tree: str | Path | None = None,
    repository_url: str = HANDOFF.LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    runner: Any = subprocess.run,
) -> Phase2SavedPostAuditCatalogHandoff:
    output = _output_path(output_path)

    source_before = _handoff_source_identity()
    report = HANDOFF.inspect_phase2_post_audit_catalog_handoff(
        snapshot_path=snapshot_path,
        artifact_directory=artifact_directory,
        artifact_pattern=artifact_pattern,
        repository_root=repository_root,
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        receipt_path=receipt_path,
        max_receipt_age_seconds=max_receipt_age_seconds,
        source_tree=source_tree,
        repository_url=repository_url,
        runner=runner,
    )
    source_after = _handoff_source_identity()
    if source_after != source_before:
        raise ValueError(
            "reviewed catalog handoff source changed during snapshot build"
        )
    if not _handoff_boundary_ok(report):
        raise ValueError(
            "catalog handoff is not verified and safely non-authorizing"
        )

    handoff_record = report.to_record()
    _assert_credential_minimal(handoff_record)
    handoff_payload_sha = _canonical_sha256(handoff_record)
    source_commit, handoff_tool_sha = source_before

    payload = {
        "format_version": 1,
        "artifact_type": _ARTIFACT_TYPE,
        "handoff_payload_sha256": handoff_payload_sha,
        "handoff_source_commit": source_commit,
        "handoff_tool_sha256": handoff_tool_sha,
        "handoff": handoff_record,
    }
    _assert_credential_minimal(payload)

    artifact_sha, bytes_written = _atomic_write_new(output, payload)

    return Phase2SavedPostAuditCatalogHandoff(
        output_path=str(output),
        artifact_type=_ARTIFACT_TYPE,
        artifact_sha256=artifact_sha,
        handoff_payload_sha256=handoff_payload_sha,
        handoff_source_commit=source_commit,
        handoff_tool_sha256=handoff_tool_sha,
        snapshot_sha256=str(report.snapshot_sha256),
        evidence_lineage_verified=bool(report.evidence_lineage_verified),
        fresh_reverification_requested=bool(
            report.fresh_reverification_requested
        ),
        current_state=str(report.current_state),
        current_next_action=str(report.current_next_action),
        current_next_tool=(
            str(report.current_next_tool)
            if report.current_next_tool is not None
            else None
        ),
        current_next_mutation_flag=(
            str(report.current_next_mutation_flag)
            if report.current_next_mutation_flag is not None
            else None
        ),
        attention_required=bool(report.attention_required),
        bytes_written=bytes_written,
        file_mode="0600",
        artifact_write_performed=True,
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
            "Save a private immutable snapshot of a verified Phase-2 historical "
            "catalog + current lifecycle handoff. The saved handoff is evidence "
            "only and never authorizes the recorded next action."
        )
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--artifact-directory")
    parser.add_argument(
        "--artifact-pattern",
        default="*.post-audit.json",
    )
    parser.add_argument(
        "--repository-root",
        default=str(HANDOFF.CATALOG_VERIFY.REPO_ROOT),
    )
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
        default=HANDOFF.LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    args = parser.parse_args()

    result = save_phase2_post_audit_catalog_handoff_snapshot(
        snapshot_path=args.snapshot,
        output_path=args.output,
        artifact_directory=args.artifact_directory,
        artifact_pattern=args.artifact_pattern,
        repository_root=args.repository_root,
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        receipt_path=args.receipt_path,
        max_receipt_age_seconds=args.max_receipt_age_seconds,
        source_tree=args.source_tree,
        repository_url=args.repository_url,
    )
    print(json.dumps(result.to_record(), indent=2))


if __name__ == "__main__":
    main()
