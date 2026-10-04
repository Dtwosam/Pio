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
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
PREFLIGHT_TOOL = TOOLS_DIR / "check_phase2_isolated_activation.py"
PREFLIGHT_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_activation.py"
_MAX_TOOL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _CapturedFile:
    path: Path
    encoded: bytes
    opened: os.stat_result


def _assert_regular_path_stable(
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
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} path changed after capture")


def _capture_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
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
        if before.st_size < 0 or before.st_size > max_bytes:
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
    _assert_regular_path_stable(resolved, before, label=label)
    return _CapturedFile(path=resolved, encoded=encoded, opened=before)


def _load_captured_tool(path: Path, name: str) -> tuple[Any, _CapturedFile]:
    captured = _capture_regular_file(
        path,
        label="reviewed activation checker",
        max_bytes=_MAX_TOOL_BYTES,
    )
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
    _assert_regular_path_stable(
        captured.path,
        captured.opened,
        label="reviewed activation checker",
    )
    return module, captured


PREFLIGHT, _PREFLIGHT_CAPTURE = _load_captured_tool(
    PREFLIGHT_TOOL,
    "phase2_isolated_smoke_readiness_preflight",
)
_PREFLIGHT_SHA256 = hashlib.sha256(_PREFLIGHT_CAPTURE.encoded).hexdigest()


def _repo_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("cannot resolve reviewed source commit")
    commit = completed.stdout.strip()
    if len(commit) not in {40, 64}:
        raise ValueError("reviewed source commit is invalid")
    return commit


def _preflight_source_identity() -> tuple[str, str]:
    _assert_regular_path_stable(
        _PREFLIGHT_CAPTURE.path,
        _PREFLIGHT_CAPTURE.opened,
        label="reviewed activation checker",
    )
    commit = _repo_head()
    historical = subprocess.run(
        ["git", "show", f"{commit}:{PREFLIGHT_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed activation checker is not present at reviewed source commit"
        )
    if historical.stdout != _PREFLIGHT_CAPTURE.encoded:
        raise ValueError(
            "reviewed activation checker bytes do not match reviewed source commit"
        )
    _assert_regular_path_stable(
        _PREFLIGHT_CAPTURE.path,
        _PREFLIGHT_CAPTURE.opened,
        label="reviewed activation checker",
    )
    return commit, _PREFLIGHT_SHA256

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]

STREAM_UNITS = (
    "pio-phase2-isolated-prestate-stream@54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ.service",
    "pio-phase2-isolated-prestate-stream@DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf.service",
)
DETECTOR_UNIT = "pio-phase2-isolated-add-detector.service"
EVIDENCE_SERVICE = "pio-phase2-isolated-evidence-cycle.service"
EVIDENCE_TIMER = "pio-phase2-isolated-evidence-cycle.timer"


@dataclass(frozen=True)
class Phase2SmokeReadinessReport:
    runtime_ready: bool
    installed_units_exact: bool
    env_ready: bool
    data_ready: bool
    detector_state_ready: bool
    legacy_collectors_quiescent: bool
    streams_active: bool
    detector_active: bool
    detector_enabled: bool
    evidence_service_inactive: bool
    evidence_timer_inactive: bool
    evidence_timer_disabled: bool
    smoke_ready: bool
    read_only: bool
    rpc_called: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def inspect_smoke_readiness(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    runner: SystemctlRunner = subprocess.run,
) -> Phase2SmokeReadinessReport:
    preflight_source_before = _preflight_source_identity()
    report = PREFLIGHT.inspect_activation(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        runner=runner,
    )
    if _preflight_source_identity() != preflight_source_before:
        raise ValueError(
            "reviewed activation checker changed during smoke-readiness inspection"
        )

    states = {
        item.name: item
        for item in report.unit_states
    }

    env_ready = bool(
        report.env_file_regular
        and all(item.configured for item in report.env_keys)
        and report.position_pool_matches_detector_topology
    )
    data_ready = bool(
        report.data_files
        and all(
            item.exists
            and item.regular_file
            and not item.symlink
            for item in report.data_files
        )
    )
    detector_state_ready = bool(
        report.detector_state_valid
        and report.detector_cursors_complete
    )

    legacy_quiet = all(
        name in states
        and not states[name].active
        and not states[name].enabled
        for name in PREFLIGHT.LEGACY_UNITS
    )
    streams_active = all(
        name in states and states[name].active
        for name in STREAM_UNITS
    )

    detector = states.get(DETECTOR_UNIT)
    evidence_service = states.get(EVIDENCE_SERVICE)
    evidence_timer = states.get(EVIDENCE_TIMER)

    detector_active = bool(detector and detector.active)
    detector_enabled = bool(detector and detector.enabled)
    evidence_service_inactive = bool(
        evidence_service and not evidence_service.active
    )
    evidence_timer_inactive = bool(
        evidence_timer and not evidence_timer.active
    )
    evidence_timer_disabled = bool(
        evidence_timer and not evidence_timer.enabled
    )

    ready = bool(
        report.runtime_ready
        and report.installed_units_exact
        and env_ready
        and data_ready
        and detector_state_ready
        and legacy_quiet
        and streams_active
        and detector_active
        and detector_enabled
        and evidence_service_inactive
        and evidence_timer_inactive
        and evidence_timer_disabled
    )

    return Phase2SmokeReadinessReport(
        runtime_ready=bool(report.runtime_ready),
        installed_units_exact=bool(report.installed_units_exact),
        env_ready=env_ready,
        data_ready=data_ready,
        detector_state_ready=detector_state_ready,
        legacy_collectors_quiescent=legacy_quiet,
        streams_active=streams_active,
        detector_active=detector_active,
        detector_enabled=detector_enabled,
        evidence_service_inactive=evidence_service_inactive,
        evidence_timer_inactive=evidence_timer_inactive,
        evidence_timer_disabled=evidence_timer_disabled,
        smoke_ready=ready,
        read_only=True,
        rpc_called=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only readiness gate for one manual isolated Phase-2 "
            "bounded evidence smoke cycle. The evidence timer must remain "
            "disabled until the smoke cycle is validated."
        )
    )
    parser.add_argument(
        "--runtime-root",
        default="/opt/pio-phase2-runtime",
    )
    parser.add_argument(
        "--unit-destination",
        default="/etc/systemd/system",
    )
    parser.add_argument(
        "--env-file",
        default="/etc/pio/pio.env",
    )
    parser.add_argument(
        "--data-root",
        default="/opt/pio/data",
    )
    args = parser.parse_args()

    report = inspect_smoke_readiness(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
    )
    print(json.dumps(report.to_record(), indent=2))
    if not report.smoke_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
