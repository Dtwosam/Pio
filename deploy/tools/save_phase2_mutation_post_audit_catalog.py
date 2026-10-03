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
CATALOG_TOOL = TOOLS_DIR / "catalog_phase2_mutation_post_audits.py"
CATALOG_TOOL_RELATIVE = "deploy/tools/catalog_phase2_mutation_post_audits.py"
_ARTIFACT_TYPE = "PHASE2_MUTATION_POST_AUDIT_CATALOG_SNAPSHOT_V1"
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


CATALOG = _load(CATALOG_TOOL, "phase2_saved_post_audit_catalog")
_CATALOG_TOOL_SHA256_AT_LOAD = hashlib.sha256(
    CATALOG_TOOL.read_bytes()
).hexdigest()


@dataclass(frozen=True)
class Phase2SavedPostAuditCatalog:
    output_path: str
    artifact_type: str
    artifact_sha256: str
    catalog_payload_sha256: str
    catalog_source_commit: str
    catalog_tool_sha256: str
    artifacts_seen: int
    artifacts_verified: int
    bytes_written: int
    file_mode: str
    artifact_write_performed: bool
    read_only_catalog: bool
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
        raise ValueError("post-audit catalog snapshot output must not be a symlink")
    path = raw.resolve(strict=False)
    if any(_under(path, root.resolve()) for root in _PROTECTED_ROOTS):
        raise ValueError("post-audit catalog snapshot output is inside a protected production path")
    if path.exists():
        raise ValueError("post-audit catalog snapshot output already exists")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("post-audit catalog snapshot parent must be an existing directory")
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


def _run_git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )


def _catalog_source_identity() -> tuple[str, str]:
    if CATALOG_TOOL.is_symlink() or not CATALOG_TOOL.is_file():
        raise ValueError("reviewed post-audit catalog tool is missing or symlinked")

    head = _run_git("rev-parse", "HEAD")
    if head.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = head.stdout.decode("utf-8").strip()
    if not _COMMIT.fullmatch(commit):
        raise ValueError("reviewed source commit is invalid")

    historical = _run_git("show", f"{commit}:{CATALOG_TOOL_RELATIVE}")
    if historical.returncode != 0:
        raise ValueError("catalog tool is not present at reviewed source commit")
    current = CATALOG_TOOL.read_bytes()
    current_sha = hashlib.sha256(current).hexdigest()
    if current_sha != _CATALOG_TOOL_SHA256_AT_LOAD:
        raise ValueError("catalog tool bytes changed after module load")
    if historical.stdout != current:
        raise ValueError("catalog tool bytes do not match reviewed source commit")

    return commit, current_sha


def _catalog_boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "artifacts_seen", 0) > 0
        and getattr(report, "artifacts_verified", 0)
        == getattr(report, "artifacts_seen", -1)
        and getattr(report, "artifacts_failed", 1) == 0
        and getattr(report, "duplicate_artifact_hashes", 1) == 0
        and getattr(report, "duplicate_receipt_hashes", 1) == 0
        and getattr(report, "catalog_stable_during_scan", False)
        and getattr(report, "all_verified", False)
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
            raise ValueError("post-audit catalog snapshot output became a symlink")
        if path.exists():
            raise ValueError("post-audit catalog snapshot output appeared before publish")

        temp_stat = os.stat(temp_path, follow_symlinks=False)
        try:
            os.link(
                temp_path,
                path,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise ValueError(
                "post-audit catalog snapshot output appeared before publish"
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
                "post-audit catalog snapshot publication identity mismatch"
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
                "post-audit catalog snapshot path changed after publish"
            )
    finally:
        if fd is not None:
            os.close(fd)
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    saved = path.read_bytes()
    if saved != encoded:
        raise ValueError("saved post-audit catalog snapshot bytes do not match")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("post-audit catalog snapshot permissions are not 0600")
    return hashlib.sha256(saved).hexdigest(), len(saved)


def save_phase2_post_audit_catalog_snapshot(
    *,
    artifact_directory: str | Path,
    output_path: str | Path,
    pattern: str = "*.post-audit.json",
    repository_root: str | Path = CATALOG.VERIFY.REPO_ROOT,
) -> Phase2SavedPostAuditCatalog:
    output = _output_path(output_path)
    artifact_root = Path(artifact_directory).expanduser().resolve(strict=True)
    if output.parent == artifact_root and Path(output.name).match(pattern):
        raise ValueError(
            "post-audit catalog snapshot output must not match the catalog pattern"
        )

    source_before = _catalog_source_identity()
    catalog = CATALOG.build_phase2_post_audit_catalog(
        artifact_directory=artifact_directory,
        pattern=pattern,
        repository_root=repository_root,
    )
    source_after = _catalog_source_identity()
    if source_after != source_before:
        raise ValueError("reviewed catalog source changed during snapshot build")
    if not _catalog_boundary_ok(catalog):
        raise ValueError("post-audit catalog is not uniquely and fully verified")

    catalog_record = catalog.to_record()
    _assert_credential_minimal(catalog_record)
    catalog_payload_sha = _canonical_sha256(catalog_record)
    source_commit, catalog_tool_sha = source_before

    payload = {
        "format_version": 1,
        "artifact_type": _ARTIFACT_TYPE,
        "catalog_payload_sha256": catalog_payload_sha,
        "catalog_source_commit": source_commit,
        "catalog_tool_sha256": catalog_tool_sha,
        "catalog": catalog_record,
    }
    _assert_credential_minimal(payload)

    artifact_sha, bytes_written = _atomic_write_new(output, payload)

    return Phase2SavedPostAuditCatalog(
        output_path=str(output),
        artifact_type=_ARTIFACT_TYPE,
        artifact_sha256=artifact_sha,
        catalog_payload_sha256=catalog_payload_sha,
        catalog_source_commit=source_commit,
        catalog_tool_sha256=catalog_tool_sha,
        artifacts_seen=int(catalog.artifacts_seen),
        artifacts_verified=int(catalog.artifacts_verified),
        bytes_written=bytes_written,
        file_mode="0600",
        artifact_write_performed=True,
        read_only_catalog=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Historically verify a Phase-2 post-audit directory and atomically "
            "save a private immutable catalog snapshot only when every artifact "
            "is verified and evidence identities are unique."
        )
    )
    parser.add_argument("--artifact-directory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pattern", default="*.post-audit.json")
    parser.add_argument("--repository-root", default=str(CATALOG.VERIFY.REPO_ROOT))
    args = parser.parse_args()

    report = save_phase2_post_audit_catalog_snapshot(
        artifact_directory=args.artifact_directory,
        output_path=args.output,
        pattern=args.pattern,
        repository_root=args.repository_root,
    )
    print(json.dumps(report.to_record(), indent=2))


if __name__ == "__main__":
    main()
