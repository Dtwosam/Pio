#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
CHECK_TOOL = TOOLS_DIR / "check_phase2_isolated_runtime.py"
CHECK_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_runtime.py"
DEFAULT_REPOSITORY_URL = "https://github.com/Dtwosam/Pio.git"
_MAX_TOOL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _CapturedFile:
    path: Path
    encoded: bytes
    opened: os.stat_result


def _assert_file_path_stable(
    path: Path,
    opened: os.stat_result,
    *,
    label: str,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} path changed after capture") from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
        or current.st_mode != opened.st_mode
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} path changed after capture")


def _capture_regular_file(
    path: Path,
    *,
    label: str,
) -> _CapturedFile:
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
            or after.st_mode != before.st_mode
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
    _assert_file_path_stable(resolved, before, label=label)
    return _CapturedFile(
        path=resolved,
        encoded=encoded,
        opened=before,
    )


def _load_captured_check(
    path: Path,
    name: str,
) -> tuple[Any, _CapturedFile]:
    label = "reviewed isolated-runtime validator"
    captured = _capture_regular_file(path, label=label)
    spec = importlib.util.spec_from_file_location(name, captured.path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {captured.path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        code = compile(captured.encoded, str(captured.path), "exec")
        exec(code, module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    _assert_file_path_stable(
        captured.path,
        captured.opened,
        label=label,
    )
    return module, captured


CHECK, _CHECK_CAPTURE = _load_captured_check(
    CHECK_TOOL,
    "phase2_pinned_source_runtime_check",
)
_CHECK_SHA256 = hashlib.sha256(_CHECK_CAPTURE.encoded).hexdigest()


def _repo_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("cannot resolve reviewed bootstrap source commit")
    commit = completed.stdout.strip()
    if len(commit) not in {40, 64}:
        raise ValueError("reviewed bootstrap source commit is invalid")
    return commit


def _check_source_identity() -> tuple[str, str]:
    _assert_file_path_stable(
        _CHECK_CAPTURE.path,
        _CHECK_CAPTURE.opened,
        label="reviewed isolated-runtime validator",
    )
    commit = _repo_head()
    historical = subprocess.run(
        ["git", "show", f"{commit}:{CHECK_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed isolated-runtime validator is not present at reviewed source commit"
        )
    if historical.stdout != _CHECK_CAPTURE.encoded:
        raise ValueError(
            "reviewed isolated-runtime validator bytes do not match reviewed source commit"
        )
    _assert_file_path_stable(
        _CHECK_CAPTURE.path,
        _CHECK_CAPTURE.opened,
        label="reviewed isolated-runtime validator",
    )
    return commit, _CHECK_SHA256


@dataclass(frozen=True)
class PinnedSourceBootstrapReport:
    destination: str
    repository_url: str
    pinned_source_head: str
    reviewed_check_commit: str
    reviewed_check_sha256: str
    observed_source_head: str | None
    tracked_clean: bool
    status: str
    ready: bool
    applied: bool
    existing_reused: bool
    network_fetch_performed: bool
    read_only: bool
    source_tree_modified: bool
    production_tree_modified: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _validated_repository_url(value: str) -> tuple[str, str]:
    raw = value.strip()
    if not raw:
        raise ValueError("repository URL is required")

    parsed = urlsplit(raw)
    if parsed.scheme in {"http", "https"}:
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "credential-bearing or query-bearing repository URLs are not allowed"
            )
        host = parsed.hostname or ""
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        safe = urlunsplit(
            (parsed.scheme, host, parsed.path, "", "")
        )
        return raw, safe

    # Local paths and SSH-style Git remotes are supported for offline tests or
    # operator environments. Never preserve URL query/fragment material.
    if "?" in raw or "#" in raw:
        raise ValueError("repository URL query/fragment is not allowed")
    return raw, raw


def _protected_roots() -> tuple[Path, ...]:
    return (
        Path("/opt/pio").resolve(),
        Path("/opt/pio-phase2-runtime").resolve(),
    )


def _destination(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError("source bootstrap destination must not be a symlink")
    resolved = raw.resolve(strict=False)
    for protected in _protected_roots():
        if resolved == protected or protected in resolved.parents:
            raise ValueError(
                "source bootstrap destination must be outside protected runtime trees"
            )
    return resolved


def _git(
    source: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(source),
        text=True,
        capture_output=True,
        check=False,
    )


def _head(source: Path) -> str | None:
    proc = _git(source, "rev-parse", "HEAD")
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _tracked_clean(source: Path) -> bool:
    proc = _git(
        source,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
    )
    return proc.returncode == 0 and not proc.stdout.strip()


def inspect_pinned_source(
    *,
    destination: str | Path,
    repository_url: str = DEFAULT_REPOSITORY_URL,
) -> PinnedSourceBootstrapReport:
    check_identity = _check_source_identity()
    target = _destination(destination)
    _raw_repository_url, safe_repository_url = _validated_repository_url(
        repository_url
    )
    observed_head: str | None = None
    tracked_clean = False

    if target.exists():
        if target.is_symlink() or not target.is_dir():
            status = "CONFLICT_EXISTING"
        elif not (target / ".git").is_dir():
            status = "CONFLICT_NOT_GIT_TREE"
        else:
            observed_head = _head(target)
            tracked_clean = _tracked_clean(target)
            if (
                observed_head == CHECK.PINNED_SOURCE_HEAD
                and tracked_clean
            ):
                status = "ALREADY_PINNED"
            else:
                status = "CONFLICT_SOURCE_DRIFT"
    else:
        status = "READY_CREATE"

    ready = status in {"READY_CREATE", "ALREADY_PINNED"}
    if _check_source_identity() != check_identity:
        raise ValueError(
            "reviewed isolated-runtime validator changed during bootstrap preflight"
        )
    return PinnedSourceBootstrapReport(
        destination=str(target),
        repository_url=safe_repository_url,
        pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
        reviewed_check_commit=check_identity[0],
        reviewed_check_sha256=check_identity[1],
        observed_source_head=observed_head,
        tracked_clean=tracked_clean,
        status=status,
        ready=ready,
        applied=False,
        existing_reused=status == "ALREADY_PINNED",
        network_fetch_performed=False,
        read_only=True,
        source_tree_modified=False,
        production_tree_modified=False,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def _fetch_exact_source(
    *,
    destination: Path,
    repository_url: str,
    pinned_source_head: str,
    check_identity: tuple[str, str],
) -> None:
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{destination.name}.{os.getpid()}.staging"
    if staging.exists() or staging.is_symlink():
        raise ValueError(f"source bootstrap staging path exists: {staging}")

    staging.mkdir()
    try:
        commands = (
            ("init", "--quiet"),
            ("remote", "add", "origin", repository_url),
            (
                "fetch",
                "--quiet",
                "--depth=1",
                "origin",
                pinned_source_head,
            ),
            ("checkout", "--quiet", "--detach", "FETCH_HEAD"),
        )
        for args in commands:
            proc = _git(staging, *args)
            if proc.returncode != 0:
                raise ValueError(
                    f"pinned source Git step failed: {args[0]}"
                )

        if _head(staging) != pinned_source_head:
            raise ValueError("fetched source HEAD does not match runtime pin")
        if not _tracked_clean(staging):
            raise ValueError("fetched source tree is not tracked-clean")
        if _check_source_identity() != check_identity:
            raise ValueError(
                "reviewed isolated-runtime validator changed during source fetch"
            )
        if destination.exists() or destination.is_symlink():
            raise ValueError("source bootstrap destination changed during fetch")
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def bootstrap_pinned_source(
    *,
    destination: str | Path,
    repository_url: str = DEFAULT_REPOSITORY_URL,
    apply: bool = False,
) -> PinnedSourceBootstrapReport:
    check_identity = _check_source_identity()
    report = inspect_pinned_source(
        destination=destination,
        repository_url=repository_url,
    )
    if _check_source_identity() != check_identity:
        raise ValueError(
            "reviewed isolated-runtime validator changed after bootstrap preflight"
        )
    if not apply:
        return report
    if not report.ready:
        raise ValueError(
            f"pinned source bootstrap is not ready: {report.status}"
        )
    if report.status == "ALREADY_PINNED":
        if _check_source_identity() != check_identity:
            raise ValueError(
                "reviewed isolated-runtime validator changed before source reuse"
            )
        return PinnedSourceBootstrapReport(
            **{
                **report.to_record(),
                "applied": True,
                "existing_reused": True,
                "read_only": True,
                "source_tree_modified": False,
            }
        )

    target = Path(report.destination)
    pinned_source_head = str(report.pinned_source_head)
    if _check_source_identity() != check_identity:
        raise ValueError(
            "reviewed isolated-runtime validator changed before source fetch"
        )
    _fetch_exact_source(
        destination=target,
        repository_url=repository_url,
        pinned_source_head=pinned_source_head,
        check_identity=check_identity,
    )
    if _check_source_identity() != check_identity:
        raise ValueError(
            "reviewed isolated-runtime validator changed after source fetch"
        )
    final = inspect_pinned_source(
        destination=target,
        repository_url=repository_url,
    )
    if final.status != "ALREADY_PINNED":
        raise ValueError("post-fetch pinned source validation failed")

    return PinnedSourceBootstrapReport(
        **{
            **final.to_record(),
            "applied": True,
            "existing_reused": False,
            "network_fetch_performed": True,
            "read_only": False,
            "source_tree_modified": True,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or fetch the exact pinned Phase-2 runtime source into "
            "a side build tree outside /opt/pio. This tool never controls "
            "systemd, touches production data, or makes Solana/Jupiter RPC calls."
        )
    )
    parser.add_argument(
        "--destination",
        default=(
            "/opt/pio-phase2-build/"
            + CHECK.PINNED_SOURCE_HEAD
        ),
    )
    parser.add_argument(
        "--repository-url",
        default=DEFAULT_REPOSITORY_URL,
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = bootstrap_pinned_source(
        destination=args.destination,
        repository_url=args.repository_url,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
