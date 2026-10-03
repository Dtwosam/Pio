#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit


TOOLS_DIR = Path(__file__).resolve().parent
CHECK_TOOL = TOOLS_DIR / "check_phase2_isolated_runtime.py"
DEFAULT_REPOSITORY_URL = "https://github.com/Dtwosam/Pio.git"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load(CHECK_TOOL, "phase2_pinned_source_runtime_check")


@dataclass(frozen=True)
class PinnedSourceBootstrapReport:
    destination: str
    repository_url: str
    pinned_source_head: str
    observed_source_head: str | None
    tracked_clean: bool
    status: str
    ready: bool
    applied: bool
    existing_reused: bool
    network_fetch_performed: bool
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
    return PinnedSourceBootstrapReport(
        destination=str(target),
        repository_url=safe_repository_url,
        pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
        observed_source_head=observed_head,
        tracked_clean=tracked_clean,
        status=status,
        ready=ready,
        applied=False,
        existing_reused=status == "ALREADY_PINNED",
        network_fetch_performed=False,
        production_tree_modified=False,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def _fetch_exact_source(
    *,
    destination: Path,
    repository_url: str,
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
                CHECK.PINNED_SOURCE_HEAD,
            ),
            ("checkout", "--quiet", "--detach", "FETCH_HEAD"),
        )
        for args in commands:
            proc = _git(staging, *args)
            if proc.returncode != 0:
                raise ValueError(
                    f"pinned source Git step failed: {args[0]}"
                )

        if _head(staging) != CHECK.PINNED_SOURCE_HEAD:
            raise ValueError("fetched source HEAD does not match runtime pin")
        if not _tracked_clean(staging):
            raise ValueError("fetched source tree is not tracked-clean")
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
    report = inspect_pinned_source(
        destination=destination,
        repository_url=repository_url,
    )
    if not apply:
        return report
    if not report.ready:
        raise ValueError(
            f"pinned source bootstrap is not ready: {report.status}"
        )
    if report.status == "ALREADY_PINNED":
        return PinnedSourceBootstrapReport(
            **{
                **report.to_record(),
                "applied": True,
                "existing_reused": True,
            }
        )

    target = Path(report.destination)
    _fetch_exact_source(
        destination=target,
        repository_url=repository_url,
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
