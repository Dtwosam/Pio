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
    "phase2_isolated_smoke_readiness_preflight",
)

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
    report = PREFLIGHT.inspect_activation(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        runner=runner,
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
