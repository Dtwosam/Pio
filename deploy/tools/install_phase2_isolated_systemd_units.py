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


CHECK = _load(CHECK_TOOL, "phase2_isolated_systemd_runtime_check")

UNIT_CONTRACT = {
    "pio-phase2-isolated-prestate-stream@.service":
        "4270a0c7b3aec8844ae9f06c6a073aa7b9d247af",
    "pio-phase2-isolated-add-detector.service":
        "fdb5334cca2174421e084813b42052f5399c435b",
    "pio-phase2-isolated-evidence-cycle.service":
        "3a82fc92e9785f6dcbde8cfd9c3458a71e34c8e3",
    "pio-phase2-isolated-evidence-cycle.timer":
        "e6781b6e7f4d235ec170d7f08d8f9c25414100ef",
    "pio-phase2-isolated-rate-limit-pause.service":
        "4f3540118072e8608550b6c9f71b2b89f39c00ed",
}


@dataclass(frozen=True)
class UnitStatus:
    name: str
    expected_blob: str
    source_blob: str | None
    installed_blob: str | None
    status: str


@dataclass(frozen=True)
class InstallReport:
    source_tree: str
    runtime_current: str
    destination: str
    runtime_ready: bool
    ready: bool
    applied: bool
    units: tuple[UnitStatus, ...]
    daemon_reload_performed: bool
    service_control_performed: bool
    services_enabled: bool
    services_started: bool
    rpc_called: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def git_blob_sha(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _regular_directory(value: str | Path, *, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = raw.resolve()
    if not resolved.is_dir():
        raise ValueError(f"{label} is missing: {resolved}")
    return resolved


def _validate_runtime(runtime_root: str | Path) -> Path:
    root = Path(runtime_root).expanduser()
    if root.is_symlink():
        raise ValueError("runtime root must not be a symlink")
    root = root.resolve()
    current = root / "current"
    if not current.is_symlink():
        raise ValueError("isolated runtime current path must be a symlink")
    resolved = current.resolve(strict=True)
    releases = (root / "releases").resolve()
    try:
        resolved.relative_to(releases)
    except ValueError as exc:
        raise ValueError(
            "isolated runtime current target is outside releases"
        ) from exc
    report = CHECK.inspect_runtime(resolved)
    if not report.runtime_ready:
        raise ValueError("isolated runtime failed reviewed validation")
    return current


def inspect_install(
    *,
    source_tree: str | Path,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    destination: str | Path = "/etc/systemd/system",
) -> InstallReport:
    source = _regular_directory(source_tree, label="source tree")
    runtime_current = _validate_runtime(runtime_root)
    destination_path = _regular_directory(
        destination,
        label="systemd destination",
    )

    rows = []
    for name, expected in UNIT_CONTRACT.items():
        source_path = source / "deploy" / "systemd" / name
        target_path = destination_path / name
        source_blob = git_blob_sha(source_path)
        target_blob = git_blob_sha(target_path)

        if source_blob != expected:
            status = "SOURCE_DRIFT"
        elif target_path.is_symlink():
            status = "CONFLICT_SYMLINK"
        elif target_blob is None:
            status = "READY_CREATE"
        elif target_blob == expected:
            status = "ALREADY_TARGET"
        else:
            status = "CONFLICT_MODIFIED"

        rows.append(
            UnitStatus(
                name=name,
                expected_blob=expected,
                source_blob=source_blob,
                installed_blob=target_blob,
                status=status,
            )
        )

    ready = all(
        row.status in {"READY_CREATE", "ALREADY_TARGET"}
        for row in rows
    )
    return InstallReport(
        source_tree=str(source),
        runtime_current=str(runtime_current),
        destination=str(destination_path),
        runtime_ready=True,
        ready=ready,
        applied=False,
        units=tuple(rows),
        daemon_reload_performed=False,
        service_control_performed=False,
        services_enabled=False,
        services_started=False,
        rpc_called=False,
    )


def install_units(
    *,
    source_tree: str | Path,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    destination: str | Path = "/etc/systemd/system",
    apply: bool = False,
) -> InstallReport:
    report = inspect_install(
        source_tree=source_tree,
        runtime_root=runtime_root,
        destination=destination,
    )
    if not apply:
        return report
    if not report.ready:
        raise ValueError("isolated Phase-2 systemd install preflight is not ready")

    source = Path(report.source_tree)
    destination_path = Path(report.destination)
    created: list[Path] = []

    try:
        for row in report.units:
            if row.status == "ALREADY_TARGET":
                continue
            target = destination_path / row.name
            if target.exists() or target.is_symlink():
                raise ValueError(f"unit target changed after preflight: {row.name}")
            source_path = source / "deploy" / "systemd" / row.name
            temp = destination_path / f".{row.name}.{os.getpid()}.tmp"
            if temp.exists() or temp.is_symlink():
                temp.unlink()
            shutil.copy2(source_path, temp)
            os.chmod(temp, 0o644)
            if git_blob_sha(temp) != row.expected_blob:
                temp.unlink(missing_ok=True)
                raise ValueError(f"copied unit blob mismatch: {row.name}")
            os.replace(temp, target)
            created.append(target)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise

    final = inspect_install(
        source_tree=source,
        runtime_root=runtime_root,
        destination=destination_path,
    )
    if not final.ready or any(
        row.status != "ALREADY_TARGET" for row in final.units
    ):
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise ValueError("post-install unit validation failed")

    return InstallReport(
        source_tree=final.source_tree,
        runtime_current=final.runtime_current,
        destination=final.destination,
        runtime_ready=True,
        ready=True,
        applied=True,
        units=final.units,
        daemon_reload_performed=False,
        service_control_performed=False,
        services_enabled=False,
        services_started=False,
        rpc_called=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or install exact isolated Phase-2 systemd unit files. "
            "This tool never daemon-reloads, enables, starts, stops, or "
            "restarts services and never makes RPC calls."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument(
        "--runtime-root",
        default="/opt/pio-phase2-runtime",
    )
    parser.add_argument(
        "--destination",
        default="/etc/systemd/system",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = install_units(
        source_tree=args.source_tree,
        runtime_root=args.runtime_root,
        destination=args.destination,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
