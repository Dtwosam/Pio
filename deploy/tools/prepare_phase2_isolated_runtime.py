#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
COMPAT_TOOL = TOOLS_DIR / "apply_phase2_add_index_compat_patch.py"
CHECK_TOOL = TOOLS_DIR / "check_phase2_isolated_runtime.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COMPAT = _load(COMPAT_TOOL, "phase2_isolated_runtime_compat")
CHECK = _load(CHECK_TOOL, "phase2_isolated_runtime_check")


@dataclass(frozen=True)
class IsolatedRuntimePreparationReport:
    source_tree: str
    pinned_source_head: str
    observed_source_head: str | None
    initial_tracked_clean: bool
    compat_status: str
    compat_ready: bool
    compat_applied: bool
    build_requested: bool
    build_succeeded: bool
    runtime_ready: bool
    executor_path: str
    production_tree_modified: bool
    rpc_called: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _source(value: str | Path) -> Path:
    source = Path(value).resolve()
    if source == Path("/opt/pio").resolve():
        raise ValueError("isolated runtime source must not be /opt/pio")
    if not source.is_dir() or not (source / ".git").exists():
        raise ValueError(f"isolated runtime Git tree is missing: {source}")
    return source


def _head(source: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(source),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _tracked_dirty(source: Path) -> tuple[str, ...]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=str(source),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError("cannot inspect isolated runtime Git status")
    return tuple(line for line in proc.stdout.splitlines() if line.strip())


def _build_executor(source: Path) -> None:
    manifest = source / "rust-executor" / "Cargo.toml"
    if not manifest.is_file():
        raise ValueError("isolated runtime Cargo.toml is missing")
    proc = subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            str(manifest),
        ],
        cwd=str(source / "rust-executor"),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(
            f"isolated runtime cargo build failed with status {proc.returncode}"
        )


def prepare_runtime(
    *,
    source_tree: str | Path,
    prepare: bool = False,
) -> IsolatedRuntimePreparationReport:
    source = _source(source_tree)
    observed_head = _head(source)
    if observed_head != CHECK.PINNED_SOURCE_HEAD:
        raise ValueError(
            "isolated runtime source HEAD does not match reviewed pinned HEAD"
        )

    dirty = _tracked_dirty(source)
    compat_report = COMPAT.evaluate(source_tree=source, apply=False)

    if not prepare:
        return IsolatedRuntimePreparationReport(
            source_tree=str(source),
            pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
            observed_source_head=observed_head,
            initial_tracked_clean=not dirty,
            compat_status=compat_report.status,
            compat_ready=compat_report.ready,
            compat_applied=False,
            build_requested=False,
            build_succeeded=False,
            runtime_ready=False,
            executor_path=str(source / CHECK.EXECUTOR_RELATIVE),
            production_tree_modified=False,
            rpc_called=False,
            service_control_performed=False,
        )

    if dirty:
        raise ValueError("isolated runtime source has unexpected tracked changes")
    if compat_report.status != "READY_APPLY" or not compat_report.ready:
        raise ValueError(
            f"compatibility preflight is not ready: {compat_report.status}"
        )

    applied = COMPAT.evaluate(source_tree=source, apply=True)
    if not applied.applied:
        raise ValueError("compatibility patch was not applied")

    _build_executor(source)
    final = CHECK.inspect_runtime(source)
    if not final.runtime_ready:
        raise ValueError("prepared isolated runtime failed final validation")

    return IsolatedRuntimePreparationReport(
        source_tree=str(source),
        pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
        observed_source_head=observed_head,
        initial_tracked_clean=True,
        compat_status=applied.status,
        compat_ready=applied.ready,
        compat_applied=True,
        build_requested=True,
        build_succeeded=True,
        runtime_ready=True,
        executor_path=final.executor_path,
        production_tree_modified=False,
        rpc_called=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or prepare a pinned isolated Phase-2 runtime. "
            "Preparation only mutates the supplied side source tree."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="apply the reviewed compatibility patch and build the Rust executor",
    )
    args = parser.parse_args()

    report = prepare_runtime(
        source_tree=args.source_tree,
        prepare=args.prepare,
    )
    print(json.dumps(report.to_record(), indent=2))
    if args.prepare and not report.runtime_ready:
        raise SystemExit(2)
    if not args.prepare and not report.compat_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
