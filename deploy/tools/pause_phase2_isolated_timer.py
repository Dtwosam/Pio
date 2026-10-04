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
AUTOPAUSE_TOOL = TOOLS_DIR / "autopause_phase2_isolated_timer.py"


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
AUTOPAUSE = _load(
    AUTOPAUSE_TOOL,
    "phase2_timer_pause_stable_autopause",
)

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]


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


def _autopause_boundary_ok(report: Any) -> bool:
    return bool(
        not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
    )


def _base_report(
    *,
    pause_recommended: bool,
    apply_requested: bool,
    applied: bool,
    timer_active_before: bool,
    timer_enabled_before: bool,
    timer_active_after: bool,
    timer_enabled_after: bool,
    failure_step: str | None,
    future_rpc_cycles_paused: bool,
    service_control_performed: bool,
) -> Phase2TimerPauseReport:
    return Phase2TimerPauseReport(
        pause_recommended=pause_recommended,
        apply_requested=apply_requested,
        applied=applied,
        timer_active_before=timer_active_before,
        timer_enabled_before=timer_enabled_before,
        timer_active_after=timer_active_after,
        timer_enabled_after=timer_enabled_after,
        failure_step=failure_step,
        detector_untouched=True,
        streams_untouched=True,
        evidence_service_untouched=True,
        legacy_services_untouched=True,
        direct_rpc_called=False,
        future_rpc_cycles_paused=future_rpc_cycles_paused,
        service_control_performed=service_control_performed,
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
    """
    Compatibility wrapper around the reviewed stable-evidence autopause path.

    The dry-run preserves the richer topology-aware timer-health check. An
    explicit apply never issues systemctl directly from this module; it
    delegates the mutation to autopause_phase2_isolated_timer.py so all pause
    entrypoints share the same descriptor-bound evidence revalidation.
    """
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

    active_before = bool(health.timer_active)
    enabled_before = bool(health.timer_enabled)
    recommended = bool(health.pause_recommended)

    if not recommended:
        return _base_report(
            pause_recommended=False,
            apply_requested=apply,
            applied=False,
            timer_active_before=active_before,
            timer_enabled_before=enabled_before,
            timer_active_after=active_before,
            timer_enabled_after=enabled_before,
            failure_step="PAUSE_NOT_RECOMMENDED" if apply else None,
            future_rpc_cycles_paused=not enabled_before,
            service_control_performed=False,
        )

    if not apply:
        return _base_report(
            pause_recommended=True,
            apply_requested=False,
            applied=False,
            timer_active_before=active_before,
            timer_enabled_before=enabled_before,
            timer_active_after=active_before,
            timer_enabled_after=enabled_before,
            failure_step=None,
            future_rpc_cycles_paused=not enabled_before,
            service_control_performed=False,
        )

    autopause = AUTOPAUSE.autopause(
        database_path=Path(data_root).expanduser() / "pio.db",
        history_limit=history_limit,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        max_cycle_gap_seconds=max_cycle_age_seconds,
        max_latest_age_seconds=max_cycle_age_seconds,
        apply=True,
        runner=runner,
    )
    if not _autopause_boundary_ok(autopause):
        raise ValueError("stable autopause crossed the local safety boundary")

    if not bool(autopause.pause_recommended):
        failure_step = "AUTOPAUSE_NO_LONGER_RECOMMENDED"
    elif bool(autopause.applied):
        failure_step = None
    else:
        detail = str(autopause.failure_step or "NOT_APPLIED")
        failure_step = f"AUTOPAUSE_{detail}"

    return _base_report(
        pause_recommended=True,
        apply_requested=True,
        applied=bool(autopause.applied),
        timer_active_before=bool(autopause.timer_active_before),
        timer_enabled_before=bool(autopause.timer_enabled_before),
        timer_active_after=bool(autopause.timer_active_after),
        timer_enabled_after=bool(autopause.timer_enabled_after),
        failure_step=failure_step,
        future_rpc_cycles_paused=bool(
            autopause.future_timer_cycles_paused
        ),
        service_control_performed=bool(
            autopause.service_control_performed
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compatibility wrapper for pausing the recurring isolated Phase-2 "
            "evidence timer. Apply requests delegate to the stable-evidence "
            "standalone autopause guard. Detector and prestate streams remain "
            "untouched."
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
