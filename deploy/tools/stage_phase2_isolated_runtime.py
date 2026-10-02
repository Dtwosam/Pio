#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
CHECK_TOOL = TOOLS_DIR / "check_phase2_isolated_runtime.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load(CHECK_TOOL, "phase2_isolated_runtime_stage_check")


@dataclass(frozen=True)
class IsolatedRuntimeStageReport:
    source_tree: str
    destination_root: str
    release_path: str
    current_link: str
    pinned_source_head: str
    source_runtime_ready: bool
    existing_release_reused: bool
    applied: bool
    current_target: str | None
    production_tree_modified: bool
    rpc_called: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _raw_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def _resolved(value: str | Path) -> Path:
    return _raw_path(value).resolve()


def _assert_outside_production(path: Path, *, label: str) -> None:
    production = Path("/opt/pio").resolve()
    if path == production or production in path.parents:
        raise ValueError(f"{label} must not be inside /opt/pio")


def _source(value: str | Path) -> Path:
    raw = _raw_path(value)
    if raw.is_symlink():
        raise ValueError("source tree must not be a symlink")
    source = raw.resolve()
    _assert_outside_production(source, label="source tree")
    if not source.is_dir():
        raise ValueError(f"prepared runtime source is missing: {source}")
    return source


def _destination(value: str | Path) -> Path:
    raw = _raw_path(value)
    if raw.is_symlink():
        raise ValueError("destination root must not be a symlink")
    destination = raw.resolve()
    _assert_outside_production(destination, label="destination root")
    return destination


def _release_path(destination: Path) -> Path:
    return destination / "releases" / CHECK.PINNED_SOURCE_HEAD


def _current_target(current: Path) -> str | None:
    if not current.is_symlink():
        return None
    return os.readlink(current)


def _validate_source(source: Path) -> None:
    report = CHECK.inspect_runtime(source)
    if not report.runtime_ready:
        raise ValueError("source runtime failed reviewed validation")


def _validate_existing_release(release: Path) -> None:
    report = CHECK.inspect_runtime(release)
    if not report.runtime_ready:
        raise ValueError(
            "existing pinned release failed reviewed validation"
        )


def _atomic_current_link(destination: Path, release: Path) -> None:
    current = destination / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("current path exists and is not a symlink")
    if current.is_symlink():
        try:
            current.resolve(strict=True)
        except FileNotFoundError:
            pass

    relative_target = os.path.relpath(release, destination)
    temp = destination / f".current.{os.getpid()}.tmp"
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    temp.symlink_to(relative_target)
    try:
        os.replace(temp, current)
    finally:
        if temp.exists() or temp.is_symlink():
            temp.unlink()


def stage_runtime(
    *,
    source_tree: str | Path,
    destination_root: str | Path = "/opt/pio-phase2-runtime",
    apply: bool = False,
) -> IsolatedRuntimeStageReport:
    source = _source(source_tree)
    destination = _destination(destination_root)
    _validate_source(source)

    release = _release_path(destination)
    current = destination / "current"
    existing_release = release.exists()

    if existing_release:
        if release.is_symlink() or not release.is_dir():
            raise ValueError(
                "existing pinned release is not a regular directory"
            )
        _validate_existing_release(release)

    if not apply:
        return IsolatedRuntimeStageReport(
            source_tree=str(source),
            destination_root=str(destination),
            release_path=str(release),
            current_link=str(current),
            pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
            source_runtime_ready=True,
            existing_release_reused=existing_release,
            applied=False,
            current_target=_current_target(current),
            production_tree_modified=False,
            rpc_called=False,
            service_control_performed=False,
        )

    destination.mkdir(parents=True, exist_ok=True)
    releases = destination / "releases"
    releases.mkdir(parents=True, exist_ok=True)

    reused = release.exists()
    if reused:
        _validate_existing_release(release)
    else:
        staging = releases / (
            f".staging-{CHECK.PINNED_SOURCE_HEAD}.{os.getpid()}"
        )
        if staging.exists() or staging.is_symlink():
            raise ValueError(f"staging path already exists: {staging}")
        try:
            shutil.copytree(
                source,
                staging,
                symlinks=True,
                copy_function=shutil.copy2,
            )
            _validate_existing_release(staging)
            os.replace(staging, release)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    _atomic_current_link(destination, release)

    return IsolatedRuntimeStageReport(
        source_tree=str(source),
        destination_root=str(destination),
        release_path=str(release),
        current_link=str(current),
        pinned_source_head=CHECK.PINNED_SOURCE_HEAD,
        source_runtime_ready=True,
        existing_release_reused=reused,
        applied=True,
        current_target=_current_target(current),
        production_tree_modified=False,
        rpc_called=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or atomically stage a reviewed prepared Phase-2 "
            "runtime outside /opt/pio. This tool never controls systemd "
            "or makes RPC calls."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument(
        "--destination-root",
        default="/opt/pio-phase2-runtime",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = stage_runtime(
        source_tree=args.source_tree,
        destination_root=args.destination_root,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))


if __name__ == "__main__":
    main()
