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
HEALTH_TOOL = TOOLS_DIR / "check_phase2_isolated_timer_health.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HEALTH = _load(
    HEALTH_TOOL,
    "phase2_timer_pause_health",
)

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]
TIMER_UNIT = "pio-phase2-isolated-evidence-cycle.timer"


@dataclass(frozen=True)
class Phase2TimerPauseReport:
    pause_recommended: bool
    apply_requested: bool
    applied: bool
    timer_active_before: bool
    timer_enabled_before: bool
    timer_active_after: bool
    timer_enabled_after: bool
    failure_step: str | None
    detector_untouched: bool
    streams_untouched: bool
    evidence_service_untouched: bool
    legacy_services_untouched: bool
    direct_rpc_called: bool
    future_rpc_cycles_paused: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _state(
    runner: SystemctlRunner,
    action: str,
) -> bool:
    completed = runner(
        ["systemctl", action, TIMER_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    expected = "active" if action == "is-active" else "enabled"
    return (
        int(completed.returncode) == 0
        and (completed.stdout or "").strip() == expected
    )


def pause_timer(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    history_limit: int = 8,
    max_cycle_age_seconds: int = 2700,
    rate_limit_streak_threshold: int = 2,
    apply: bool = False,
    runner: SystemctlRunner = subprocess.run,
) -> Phase2TimerPauseReport:
    health = HEALTH.inspect_timer_health(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        history_limit=history_limit,
        max_cycle_age_seconds=max_cycle_age_seconds,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        runner=runner,
    )

    active_before = health.timer_active
    enabled_before = health.timer_enabled

    if not health.pause_recommended:
        return Phase2TimerPauseReport(
            pause_recommended=False,
            apply_requested=apply,
            applied=False,
            timer_active_before=active_before,
            timer_enabled_before=enabled_before,
            timer_active_after=active_before,
            timer_enabled_after=enabled_before,
            failure_step="PAUSE_NOT_RECOMMENDED" if apply else None,
            detector_untouched=True,
            streams_untouched=True,
            evidence_service_untouched=True,
            legacy_services_untouched=True,
            direct_rpc_called=False,
            future_rpc_cycles_paused=False,
            service_control_performed=False,
        )

    if not apply:
        return Phase2TimerPauseReport(
            pause_recommended=True,
            apply_requested=False,
            applied=False,
            timer_active_before=active_before,
            timer_enabled_before=enabled_before,
            timer_active_after=active_before,
            timer_enabled_after=enabled_before,
            failure_step=None,
            detector_untouched=True,
            streams_untouched=True,
            evidence_service_untouched=True,
            legacy_services_untouched=True,
            direct_rpc_called=False,
            future_rpc_cycles_paused=False,
            service_control_performed=False,
        )

    completed = runner(
        ["systemctl", "disable", "--now", TIMER_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    if int(completed.returncode) != 0:
        return Phase2TimerPauseReport(
            pause_recommended=True,
            apply_requested=True,
            applied=False,
            timer_active_before=active_before,
            timer_enabled_before=enabled_before,
            timer_active_after=_state(runner, "is-active"),
            timer_enabled_after=_state(runner, "is-enabled"),
            failure_step="DISABLE_NOW",
            detector_untouched=True,
            streams_untouched=True,
            evidence_service_untouched=True,
            legacy_services_untouched=True,
            direct_rpc_called=False,
            future_rpc_cycles_paused=False,
            service_control_performed=True,
        )

    active_after = _state(runner, "is-active")
    enabled_after = _state(runner, "is-enabled")
    applied = not active_after and not enabled_after

    return Phase2TimerPauseReport(
        pause_recommended=True,
        apply_requested=True,
        applied=applied,
        timer_active_before=active_before,
        timer_enabled_before=enabled_before,
        timer_active_after=active_after,
        timer_enabled_after=enabled_after,
        failure_step=None if applied else "VERIFY_PAUSED",
        detector_untouched=True,
        streams_untouched=True,
        evidence_service_untouched=True,
        legacy_services_untouched=True,
        direct_rpc_called=False,
        future_rpc_cycles_paused=applied,
        service_control_performed=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Pause only the recurring isolated Phase-2 evidence timer when "
            "the persisted health record shows repeated Solana RPC rate limits. "
            "Detector and prestate streams remain untouched."
        )
    )
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument("--history-limit", type=int, default=8)
    parser.add_argument("--max-cycle-age-seconds", type=int, default=2700)
    parser.add_argument("--rate-limit-streak-threshold", type=int, default=2)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = pause_timer(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        history_limit=args.history_limit,
        max_cycle_age_seconds=args.max_cycle_age_seconds,
        rate_limit_streak_threshold=args.rate_limit_streak_threshold,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))

    if args.apply and report.pause_recommended and not report.applied:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
