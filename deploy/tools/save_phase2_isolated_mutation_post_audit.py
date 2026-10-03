#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
AUDIT_TOOL = TOOLS_DIR / "check_phase2_isolated_mutation_execution_receipt.py"
RENDER_TOOL = TOOLS_DIR / "render_phase2_isolated_mutation_command.py"
_PROTECTED_ROOTS = (
    Path("/opt/pio"),
    Path("/opt/pio/data"),
    Path("/opt/pio-phase2-runtime"),
    Path("/etc/pio"),
    Path("/etc/systemd/system"),
)
_ARTIFACT_TYPE = "PHASE2_MUTATION_POST_AUDIT_V1"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AUDIT = _load(AUDIT_TOOL, "phase2_saved_mutation_post_audit")
RENDER = _load(RENDER_TOOL, "phase2_saved_mutation_post_audit_surface")


@dataclass(frozen=True)
class Phase2SavedMutationPostAudit:
    output_path: str
    artifact_type: str
    artifact_sha256: str
    audit_payload_sha256: str
    audit_tool_sha256: str
    audit_source_commit: str
    audit_deploy_surface_sha256: str
    audit_deploy_surface_files: int
    execution_receipt_path: str
    execution_receipt_sha256: str
    prior_state: str
    current_state: str
    expected_post_states: tuple[str, ...]
    post_state_expected: bool
    post_mutation_verified: bool
    current_next_action: str
    current_next_tool: str | None
    current_next_parameters: dict[str, Any]
    current_next_mutation_flag: str | None
    bytes_written: int
    file_mode: str
    artifact_write_performed: bool
    read_only_audit: bool
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


def _output_path(
    *,
    execution_receipt_path: str | Path,
    output_path: str | Path | None,
) -> Path:
    if output_path is None:
        receipt = Path(execution_receipt_path).expanduser().resolve(strict=False)
        raw = receipt.with_name(f"{receipt.name}.post-audit.json")
    else:
        raw = Path(output_path).expanduser()

    if raw.is_symlink():
        raise ValueError("mutation post-audit output must not be a symlink")
    path = raw.resolve(strict=False)
    if any(_under(path, root.resolve()) for root in _PROTECTED_ROOTS):
        raise ValueError("mutation post-audit output is inside a protected production path")
    if path.exists():
        raise ValueError("mutation post-audit output already exists")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("mutation post-audit output parent must be an existing directory")
    return path


def _audit_tool_sha256() -> str:
    if AUDIT_TOOL.is_symlink() or not AUDIT_TOOL.is_file():
        raise ValueError("reviewed mutation post-audit tool is missing or symlinked")
    return hashlib.sha256(AUDIT_TOOL.read_bytes()).hexdigest()


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
                raise ValueError("mutation post-audit contains a sensitive field")
            _assert_credential_minimal(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_credential_minimal(item)
        return
    if isinstance(value, str) and _contains_sensitive_text(value):
        raise ValueError("mutation post-audit contains sensitive text")


def _audit_boundary_ok(audit: Any) -> bool:
    return bool(
        getattr(audit, "audit_integrity_valid", False)
        and getattr(audit, "receipt_terminal", False)
        and getattr(audit, "mutation_succeeded", False)
        and getattr(audit, "post_state_expected", False)
        and getattr(audit, "post_mutation_verified", False)
        and getattr(audit, "read_only", False)
        and not getattr(audit, "rpc_called", True)
        and not getattr(audit, "database_write_performed", True)
        and not getattr(audit, "service_control_performed", True)
        and not getattr(audit, "mutation_executed", True)
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
            raise ValueError("mutation post-audit output became a symlink")
        if path.exists():
            raise ValueError("mutation post-audit output appeared before publish")

        temp_stat = os.stat(temp_path, follow_symlinks=False)
        try:
            os.link(
                temp_path,
                path,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise ValueError(
                "mutation post-audit output appeared before publish"
            ) from exc

        published_stat = os.stat(path, follow_symlinks=False)
        if (
            published_stat.st_dev != temp_stat.st_dev
            or published_stat.st_ino != temp_stat.st_ino
            or published_stat.st_size != len(encoded)
            or not stat.S_ISREG(published_stat.st_mode)
            or stat.S_IMODE(published_stat.st_mode) != 0o600
        ):
            try:
                path.unlink()
            except OSError:
                pass
            raise ValueError(
                "mutation post-audit publication identity mismatch"
            )

        temp_path.unlink()
        temp_path = None  # type: ignore[assignment]

        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

        current = os.stat(path, follow_symlinks=False)
        if (
            current.st_dev != published_stat.st_dev
            or current.st_ino != published_stat.st_ino
            or current.st_size != len(encoded)
            or not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise ValueError(
                "mutation post-audit path changed after publish"
            )
    finally:
        if fd is not None:
            os.close(fd)
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    saved = path.read_bytes()
    if saved != encoded:
        raise ValueError("saved mutation post-audit bytes do not match")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("mutation post-audit permissions are not 0600")
    return hashlib.sha256(saved).hexdigest(), len(saved)


def save_verified_mutation_post_audit(
    *,
    execution_receipt_path: str | Path,
    output_path: str | Path | None = None,
    **audit_kwargs: Any,
) -> Phase2SavedMutationPostAudit:
    output = _output_path(
        execution_receipt_path=execution_receipt_path,
        output_path=output_path,
    )

    audit = AUDIT.audit_mutation_execution_receipt(
        execution_receipt_path=execution_receipt_path,
        **audit_kwargs,
    )
    if not _audit_boundary_ok(audit):
        raise ValueError("mutation receipt post-audit is not fully verified")

    audit_record = audit.to_record()
    _assert_credential_minimal(audit_record)
    audit_payload_sha = _canonical_sha256(audit_record)
    audit_tool_sha = _audit_tool_sha256()
    (
        audit_source_commit,
        audit_deploy_surface_sha,
        audit_deploy_surface_files,
    ) = RENDER._deploy_surface_identity()

    payload = {
        "format_version": 1,
        "artifact_type": _ARTIFACT_TYPE,
        "execution_receipt_sha256": str(audit.execution_receipt_sha256),
        "audit_payload_sha256": audit_payload_sha,
        "audit_tool_sha256": audit_tool_sha,
        "audit_source_commit": audit_source_commit,
        "audit_deploy_surface_sha256": audit_deploy_surface_sha,
        "audit_deploy_surface_files": audit_deploy_surface_files,
        "audit": audit_record,
    }
    _assert_credential_minimal(payload)

    artifact_sha, bytes_written = _atomic_write_new(output, payload)

    return Phase2SavedMutationPostAudit(
        output_path=str(output),
        artifact_type=_ARTIFACT_TYPE,
        artifact_sha256=artifact_sha,
        audit_payload_sha256=audit_payload_sha,
        audit_tool_sha256=audit_tool_sha,
        audit_source_commit=audit_source_commit,
        audit_deploy_surface_sha256=audit_deploy_surface_sha,
        audit_deploy_surface_files=audit_deploy_surface_files,
        execution_receipt_path=str(audit.execution_receipt_path),
        execution_receipt_sha256=str(audit.execution_receipt_sha256),
        prior_state=str(audit.prior_state),
        current_state=str(audit.current_state),
        expected_post_states=tuple(audit.expected_post_states),
        post_state_expected=bool(audit.post_state_expected),
        post_mutation_verified=bool(audit.post_mutation_verified),
        current_next_action=str(audit.current_next_action),
        current_next_tool=(
            str(audit.current_next_tool)
            if audit.current_next_tool is not None
            else None
        ),
        current_next_parameters=dict(audit.current_next_parameters),
        current_next_mutation_flag=(
            str(audit.current_next_mutation_flag)
            if audit.current_next_mutation_flag is not None
            else None
        ),
        bytes_written=bytes_written,
        file_mode="0600",
        artifact_write_performed=True,
        read_only_audit=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a Phase-2 mutation execution receipt and atomically save "
            "a private immutable post-audit artifact only when the expected "
            "lifecycle postcondition is proven."
        )
    )
    parser.add_argument("--execution-receipt", required=True)
    parser.add_argument("--output")
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
        default=AUDIT.LIFECYCLE.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    args = parser.parse_args()

    report = save_verified_mutation_post_audit(
        execution_receipt_path=args.execution_receipt,
        output_path=args.output,
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


if __name__ == "__main__":
    main()
