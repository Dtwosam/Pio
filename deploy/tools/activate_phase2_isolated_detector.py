#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
PREFLIGHT_TOOL = TOOLS_DIR / "check_phase2_isolated_activation.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PREFLIGHT = _load(
    PREFLIGHT_TOOL,
    "phase2_isolated_detector_activation_preflight",
)

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]

PRESTATE_STREAM_UNITS = (
    "pio-phase2-isolated-prestate-stream@54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ.service",
    "pio-phase2-isolated-prestate-stream@DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf.service",
)
DETECTOR_UNIT = "pio-phase2-isolated-add-detector.service"


@dataclass(frozen=True)
class Phase2DetectorActivationReport:
    preflight_ready: bool
    apply_requested: bool
    applied: bool
    daemon_reload_performed: bool
    service_control_performed: bool
    activated_units: tuple[str, ...]
    detector_enabled: bool
    failure_step: str | None
    rollback_performed: bool
    rollback_succeeded: bool
    evidence_timer_untouched: bool
    legacy_services_untouched: bool
    rpc_called: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _systemctl(
    runner: SystemctlRunner,
    *args: str,
) -> bool:
    completed = runner(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(completed.returncode) == 0


def _is_active(
    runner: SystemctlRunner,
    unit: str,
) -> bool:
    completed = runner(
        ["systemctl", "is-active", unit],
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        int(completed.returncode) == 0
        and (completed.stdout or "").strip() == "active"
    )


def _rollback(
    runner: SystemctlRunner,
    *,
    activated_units: list[str],
    detector_enabled: bool,
) -> bool:
    ok = True

    if DETECTOR_UNIT in activated_units:
        ok = _systemctl(runner, "stop", DETECTOR_UNIT) and ok
    if detector_enabled:
        ok = _systemctl(runner, "disable", DETECTOR_UNIT) and ok

    for unit in reversed(PRESTATE_STREAM_UNITS):
        if unit in activated_units:
            ok = _systemctl(runner, "stop", unit) and ok

    return ok


def activate_detector(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    apply: bool = False,
    runner: SystemctlRunner = subprocess.run,
) -> Phase2DetectorActivationReport:
    preflight = PREFLIGHT.inspect_activation(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        runner=runner,
    )

    if not preflight.activation_ready:
        return Phase2DetectorActivationReport(
            preflight_ready=False,
            apply_requested=apply,
            applied=False,
            daemon_reload_performed=False,
            service_control_performed=False,
            activated_units=(),
            detector_enabled=False,
            failure_step="PREFLIGHT_NOT_READY",
            rollback_performed=False,
            rollback_succeeded=True,
            evidence_timer_untouched=True,
            legacy_services_untouched=True,
            rpc_called=False,
        )

    if not apply:
        return Phase2DetectorActivationReport(
            preflight_ready=True,
            apply_requested=False,
            applied=False,
            daemon_reload_performed=False,
            service_control_performed=False,
            activated_units=(),
            detector_enabled=False,
            failure_step=None,
            rollback_performed=False,
            rollback_succeeded=True,
            evidence_timer_untouched=True,
            legacy_services_untouched=True,
            rpc_called=False,
        )

    activated: list[str] = []
    detector_enabled = False
    daemon_reload = False
    failure_step: str | None = None

    if not _systemctl(runner, "daemon-reload"):
        failure_step = "DAEMON_RELOAD"
    else:
        daemon_reload = True

    if failure_step is None:
        for unit in PRESTATE_STREAM_UNITS:
            if not _systemctl(runner, "start", unit):
                failure_step = f"START:{unit}"
                break
            activated.append(unit)
            if not _is_active(runner, unit):
                failure_step = f"VERIFY_ACTIVE:{unit}"
                break

    if failure_step is None:
        if not _systemctl(runner, "enable", DETECTOR_UNIT):
            failure_step = f"ENABLE:{DETECTOR_UNIT}"
        else:
            detector_enabled = True

    if failure_step is None:
        if not _systemctl(runner, "start", DETECTOR_UNIT):
            failure_step = f"START:{DETECTOR_UNIT}"
        else:
            activated.append(DETECTOR_UNIT)
            if not _is_active(runner, DETECTOR_UNIT):
                failure_step = f"VERIFY_ACTIVE:{DETECTOR_UNIT}"

    if failure_step is None:
        return Phase2DetectorActivationReport(
            preflight_ready=True,
            apply_requested=True,
            applied=True,
            daemon_reload_performed=daemon_reload,
            service_control_performed=True,
            activated_units=tuple(activated),
            detector_enabled=detector_enabled,
            failure_step=None,
            rollback_performed=False,
            rollback_succeeded=True,
            evidence_timer_untouched=True,
            legacy_services_untouched=True,
            rpc_called=False,
        )

    rollback_performed = bool(activated or detector_enabled)
    rollback_succeeded = (
        _rollback(
            runner,
            activated_units=activated,
            detector_enabled=detector_enabled,
        )
        if rollback_performed
        else True
    )
    return Phase2DetectorActivationReport(
        preflight_ready=True,
        apply_requested=True,
        applied=False,
        daemon_reload_performed=daemon_reload,
        service_control_performed=True,
        activated_units=tuple(activated),
        detector_enabled=detector_enabled,
        failure_step=failure_step,
        rollback_performed=rollback_performed,
        rollback_succeeded=rollback_succeeded,
        evidence_timer_untouched=True,
        legacy_services_untouched=True,
        rpc_called=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Activate only the isolated Phase-2 prestate streams and add "
            "detector after a fresh read-only preflight. The bounded evidence "
            "timer is intentionally not enabled by this tool."
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
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = activate_detector(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))

    if args.apply and not report.applied:
        raise SystemExit(2)
    if not args.apply and not report.preflight_ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
