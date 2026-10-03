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
RENDER_TOOL = TOOLS_DIR / "render_phase2_isolated_mutation_command.py"
RENDER_TOOL_RELATIVE = "deploy/tools/render_phase2_isolated_mutation_command.py"
_MAX_TOOL_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")

PROTECTED_ROOTS = (
    Path("/opt/pio"),
    Path("/opt/pio/data"),
    Path("/opt/pio-phase2-runtime"),
    Path("/etc/pio"),
    Path("/etc/systemd/system"),
)


def _assert_tool_path_stable(
    path: Path,
    opened: os.stat_result,
    *,
    label: str,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} path changed after load") from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} path changed after load")


def _capture_tool(
    path: Path,
    *,
    label: str,
) -> tuple[Path, bytes, os.stat_result]:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} is missing") from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(resolved, flags)
    except OSError as exc:
        raise ValueError(f"{label} cannot be opened safely") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if before.st_size <= 0 or before.st_size > _MAX_TOOL_BYTES:
            raise ValueError(f"{label} size is invalid")

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
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or not stat.S_ISREG(after.st_mode)
        ):
            raise ValueError(f"{label} changed while reading")
    finally:
        os.close(fd)

    if len(encoded) != before.st_size:
        raise ValueError(f"{label} changed while reading")
    _assert_tool_path_stable(resolved, before, label=label)
    return resolved, encoded, before


def _load_captured(
    path: Path,
    name: str,
) -> tuple[Any, Path, bytes, os.stat_result]:
    label = "reviewed mutation preview renderer"
    resolved, encoded, opened = _capture_tool(path, label=label)
    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {resolved}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        code = compile(encoded, str(resolved), "exec")
        exec(code, module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    _assert_tool_path_stable(resolved, opened, label=label)
    return module, resolved, encoded, opened


(
    RENDER,
    _RENDER_TOOL_PATH_AT_LOAD,
    _RENDER_TOOL_BYTES_AT_LOAD,
    _RENDER_TOOL_STAT_AT_LOAD,
) = _load_captured(
    RENDER_TOOL,
    "phase2_atomic_mutation_preview_renderer",
)
_RENDER_TOOL_SHA256_AT_LOAD = hashlib.sha256(
    _RENDER_TOOL_BYTES_AT_LOAD
).hexdigest()


@dataclass(frozen=True)
class Phase2SavedMutationPreview:
    output_path: str
    preview_sha256: str
    bytes_written: int
    file_mode: str
    replaced_existing: bool
    format_version: int
    fingerprint_schema: str
    reviewed_source_commit: str
    deploy_surface_sha256: str
    deploy_surface_files: int
    preflight_fingerprint: str
    mutation_fingerprint: str
    mutation_tool_sha256: str
    preview_saved: bool
    artifact_write_performed: bool
    read_only_preflight: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool
    daemon_reload_performed: bool
    production_tree_modified: bool
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
        raise ValueError("mutation preview output must not be a symlink")
    path = raw.resolve(strict=False)
    if any(_under(path, root.resolve()) for root in PROTECTED_ROOTS):
        raise ValueError("mutation preview output is inside a protected production path")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("mutation preview output parent must be an existing directory")
    if path.exists() and not path.is_file():
        raise ValueError("mutation preview output must be a regular file")
    return path


def _run_git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )


def _renderer_source_identity() -> tuple[str, str]:
    raw = Path(RENDER_TOOL).expanduser()
    if raw.is_symlink():
        raise ValueError("reviewed mutation preview renderer is missing or symlinked")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            "reviewed mutation preview renderer is missing or symlinked"
        ) from exc
    if resolved != _RENDER_TOOL_PATH_AT_LOAD:
        raise ValueError("mutation preview renderer path changed after module load")
    _assert_tool_path_stable(
        _RENDER_TOOL_PATH_AT_LOAD,
        _RENDER_TOOL_STAT_AT_LOAD,
        label="reviewed mutation preview renderer",
    )

    head = _run_git("rev-parse", "HEAD")
    if head.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = head.stdout.decode("utf-8").strip()
    if not _COMMIT.fullmatch(commit):
        raise ValueError("reviewed source commit is invalid")

    historical = _run_git("show", f"{commit}:{RENDER_TOOL_RELATIVE}")
    if historical.returncode != 0:
        raise ValueError(
            "mutation preview renderer is not present at reviewed source commit"
        )
    if historical.stdout != _RENDER_TOOL_BYTES_AT_LOAD:
        raise ValueError(
            "mutation preview renderer bytes do not match reviewed source commit"
        )

    _assert_tool_path_stable(
        _RENDER_TOOL_PATH_AT_LOAD,
        _RENDER_TOOL_STAT_AT_LOAD,
        label="reviewed mutation preview renderer",
    )
    return commit, _RENDER_TOOL_SHA256_AT_LOAD


def _preview_boundary_ok(report: Any) -> bool:
    mutation_tool_sha256 = getattr(report, "mutation_tool_sha256", None)
    return bool(
        getattr(report, "format_version", None)
        == RENDER.MUTATION_PREVIEW_FORMAT_VERSION
        and getattr(report, "fingerprint_schema", None)
        == RENDER.MUTATION_FINGERPRINT_SCHEMA
        and isinstance(getattr(report, "reviewed_source_commit", None), str)
        and _COMMIT.fullmatch(getattr(report, "reviewed_source_commit"))
        and isinstance(getattr(report, "deploy_surface_sha256", None), str)
        and _SHA256.fullmatch(getattr(report, "deploy_surface_sha256"))
        and isinstance(getattr(report, "deploy_surface_files", None), int)
        and not isinstance(getattr(report, "deploy_surface_files", None), bool)
        and getattr(report, "deploy_surface_files") > 0
        and getattr(report, "preflight_succeeded", False)
        and getattr(report, "mutation_rendered", False)
        and not getattr(report, "mutation_executed", True)
        and isinstance(getattr(report, "preflight_fingerprint", None), str)
        and _SHA256.fullmatch(getattr(report, "preflight_fingerprint"))
        and isinstance(getattr(report, "mutation_fingerprint", None), str)
        and _SHA256.fullmatch(getattr(report, "mutation_fingerprint"))
        and isinstance(mutation_tool_sha256, str)
        and _SHA256.fullmatch(mutation_tool_sha256)
        and getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
        and not getattr(report, "daemon_reload_performed", True)
        and not getattr(report, "production_tree_modified", True)
    )


def _encode_preview(report: Any) -> bytes:
    payload = report.to_record()
    encoded = json.dumps(
        payload,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    return encoded


def _atomic_write(path: Path, payload: bytes, *, replace: bool) -> bool:
    existed = path.exists()
    if existed and not replace:
        raise ValueError("mutation preview output already exists; use --replace")

    fd: int | None = None
    temp_path: Path | None = None
    published_stat: os.stat_result | None = None
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
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        temp_stat = os.stat(temp_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(temp_stat.st_mode)
            or stat.S_IMODE(temp_stat.st_mode) != 0o600
            or temp_stat.st_size != len(payload)
        ):
            raise ValueError("mutation preview temp artifact identity mismatch")

        if replace:
            if path.exists() and path.is_symlink():
                raise ValueError("mutation preview output became a symlink")
            if path.exists() and not path.is_file():
                raise ValueError(
                    "mutation preview output is no longer a regular file"
                )
            os.replace(temp_path, path)
            temp_path = None
            published_stat = os.stat(path, follow_symlinks=False)
        else:
            if path.is_symlink():
                raise ValueError("mutation preview output became a symlink")
            if path.exists():
                raise ValueError(
                    "mutation preview output appeared before publish"
                )
            try:
                os.link(
                    temp_path,
                    path,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise ValueError(
                    "mutation preview output appeared before publish"
                ) from exc

            published_stat = os.stat(path, follow_symlinks=False)
            if (
                published_stat.st_dev != temp_stat.st_dev
                or published_stat.st_ino != temp_stat.st_ino
            ):
                try:
                    path.unlink()
                except OSError:
                    pass
                raise ValueError(
                    "mutation preview publication identity mismatch"
                )
            temp_path.unlink()
            temp_path = None

        assert published_stat is not None
        if (
            not stat.S_ISREG(published_stat.st_mode)
            or stat.S_IMODE(published_stat.st_mode) != 0o600
            or published_stat.st_size != len(payload)
        ):
            raise ValueError("mutation preview publication identity mismatch")

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
            or current.st_size != len(payload)
            or not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise ValueError("mutation preview path changed after publish")
        return existed
    finally:
        if fd is not None:
            os.close(fd)
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def save_mutation_preview(
    *,
    output_path: str | Path,
    replace: bool = False,
    timeout_seconds: int = 60,
    **handoff_kwargs: Any,
) -> Phase2SavedMutationPreview:
    output = _output_path(output_path)
    source_before = _renderer_source_identity()
    preview = RENDER.render_reviewed_mutation_command(
        timeout_seconds=timeout_seconds,
        **handoff_kwargs,
    )
    source_after = _renderer_source_identity()
    if source_after != source_before:
        raise ValueError("reviewed mutation preview renderer changed during render")
    if not _preview_boundary_ok(preview):
        raise ValueError("mutation preview is not safe and mutation-ready")
    if str(preview.reviewed_source_commit) != source_before[0]:
        raise ValueError(
            "mutation preview source commit does not match executed renderer source"
        )

    payload = _encode_preview(preview)
    replaced = _atomic_write(output, payload, replace=replace)

    mode = stat.S_IMODE(output.stat().st_mode)
    if mode != 0o600:
        raise ValueError("saved mutation preview permissions are not 0600")

    return Phase2SavedMutationPreview(
        output_path=str(output),
        preview_sha256=hashlib.sha256(payload).hexdigest(),
        bytes_written=len(payload),
        file_mode=f"{mode:04o}",
        replaced_existing=replaced,
        format_version=int(preview.format_version),
        fingerprint_schema=str(preview.fingerprint_schema),
        reviewed_source_commit=str(preview.reviewed_source_commit),
        deploy_surface_sha256=str(preview.deploy_surface_sha256),
        deploy_surface_files=int(preview.deploy_surface_files),
        preflight_fingerprint=str(preview.preflight_fingerprint),
        mutation_fingerprint=str(preview.mutation_fingerprint),
        mutation_tool_sha256=str(preview.mutation_tool_sha256),
        preview_saved=True,
        artifact_write_performed=True,
        read_only_preflight=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
        daemon_reload_performed=False,
        production_tree_modified=False,
        mutation_executed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Render and atomically save a reviewed Phase-2 mutation preview. "
            "The mutation is never executed."
        )
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--replace", action="store_true")
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
        default=RENDER.RUNNER.RENDER.HANDOFF.BOOTSTRAP.DEFAULT_REPOSITORY_URL,
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()

    report = save_mutation_preview(
        output_path=args.output,
        replace=args.replace,
        timeout_seconds=args.timeout_seconds,
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
