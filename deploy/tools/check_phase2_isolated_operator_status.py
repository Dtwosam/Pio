#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
HEALTH_TOOL = TOOLS_DIR / "check_phase2_isolated_timer_health.py"
EFFICIENCY_TOOL = TOOLS_DIR / "check_phase2_rpc_efficiency.py"
AUTOPAUSE_UNIT = "pio-phase2-isolated-rate-limit-pause.service"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed Phase-2 tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HEALTH = _load(HEALTH_TOOL, "phase2_operator_timer_health")
EFFICIENCY = _load(EFFICIENCY_TOOL, "phase2_operator_rpc_efficiency")

Now = Callable[[], datetime]
SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Phase2OperatorStatusReport:
    state: str
    attention_required: bool
    topology_ready: bool
    collection_running: bool
    provider_rate_limit_incident: bool
    provider_rate_limit_paused: bool
    autopause_service_failed: bool
    autopause_failure_relevant: bool
    future_timer_cycles_paused: bool
    rate_limit_waste_guard_satisfied: bool
    discovery_cache_reusable_now: bool
    collector_attempts_are_not_provider_credits: bool
    provider_credit_count_available: bool
    timer_health: dict[str, Any]
    rpc_efficiency: dict[str, Any]
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _boundary_ok(report: Any) -> bool:
    return bool(
        getattr(report, "read_only", False)
        and not getattr(report, "rpc_called", True)
        and not getattr(report, "database_write_performed", True)
        and not getattr(report, "service_control_performed", True)
    )


def _unit_failed(
    runner: SystemctlRunner,
    unit: str,
) -> bool:
    completed = runner(
        ["systemctl", "is-failed", unit],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(
        int(completed.returncode) == 0
        and (completed.stdout or "").strip() == "failed"
    )


def _health_projection(report: Any) -> tuple[Any, ...]:
    return (
        getattr(report, "runtime_ready", None),
        getattr(report, "installed_units_exact", None),
        getattr(report, "env_ready", None),
        getattr(report, "data_ready", None),
        getattr(report, "detector_state_ready", None),
        getattr(report, "detector_active_enabled", None),
        getattr(report, "streams_active", None),
        getattr(report, "timer_active", None),
        getattr(report, "timer_enabled", None),
        getattr(report, "timer_active_enabled", None),
        getattr(report, "legacy_collectors_quiescent", None),
        getattr(report, "evidence_service_active", None),
        getattr(report, "cycles", ()),
        getattr(report, "latest_cycle_recent", None),
        getattr(report, "latest_cycle_failed", None),
        getattr(report, "consecutive_rpc_rate_limited", None),
        getattr(report, "rate_limit_streak_threshold", None),
        getattr(report, "pause_recommended", None),
        getattr(report, "collection_healthy", None),
    )


def _discovery_cache_projection(cache: Any) -> tuple[Any, ...]:
    return (
        getattr(cache, "path", None),
        getattr(cache, "exists", None),
        getattr(cache, "regular_file", None),
        getattr(cache, "symlink", None),
        getattr(cache, "format_valid", None),
        getattr(cache, "pool_matches", None),
        getattr(cache, "complete", None),
        getattr(cache, "captured_at", None),
        getattr(cache, "max_age_seconds", None),
        getattr(cache, "reusable_now", None),
        getattr(cache, "positions_found", None),
        getattr(cache, "positions_returned", None),
        getattr(cache, "positions_cached", None),
    )


def _efficiency_projection(report: Any) -> tuple[Any, ...]:
    return (
        getattr(report, "pool_address", None),
        getattr(report, "pool_configured", None),
        getattr(report, "database_ready", None),
        getattr(report, "database_path", None),
        _discovery_cache_projection(
            getattr(report, "discovery_cache", None)
        ),
        getattr(report, "position_attempts", None),
        getattr(report, "reinspection_attempts", None),
        getattr(report, "prestate_attempts", None),
        getattr(report, "recent_cycles", ()),
        getattr(report, "consecutive_rpc_rate_limited_cycles", None),
        getattr(report, "rate_limit_streak_threshold", None),
        getattr(report, "repeated_provider_rejection", None),
        getattr(report, "latest_cycle_recent", None),
        getattr(report, "timer_active", None),
        getattr(report, "timer_enabled", None),
        getattr(report, "timer_paused", None),
        getattr(report, "pause_recommended", None),
        getattr(report, "protected_from_future_timer_cycles", None),
        getattr(report, "attention_required", None),
    )


def inspect_operator_status(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    lookback_hours: int = 24,
    history_limit: int = 8,
    rate_limit_streak_threshold: int = 2,
    max_cycle_age_seconds: int = 2700,
    now: Now = lambda: datetime.now(timezone.utc),
    runner: SystemctlRunner = subprocess.run,
) -> Phase2OperatorStatusReport:
    health = HEALTH.inspect_timer_health(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        history_limit=history_limit,
        max_cycle_age_seconds=max_cycle_age_seconds,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        now=now,
        runner=runner,
    )
    efficiency = EFFICIENCY.inspect_rpc_efficiency(
        data_root=data_root,
        env_file=env_file,
        lookback_hours=lookback_hours,
        cycle_history_limit=history_limit,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        max_cycle_gap_seconds=max_cycle_age_seconds,
        max_latest_age_seconds=max_cycle_age_seconds,
        now=now,
        runner=runner,
    )

    if not _boundary_ok(health) or not _boundary_ok(efficiency):
        raise ValueError("Phase-2 operator status crossed the read-only boundary")

    health_projection = _health_projection(health)
    efficiency_projection = _efficiency_projection(efficiency)
    autopause_service_failed = _unit_failed(
        runner,
        AUTOPAUSE_UNIT,
    )

    topology_ready = bool(
        health.runtime_ready
        and health.installed_units_exact
        and health.env_ready
        and health.data_ready
        and health.detector_state_ready
        and health.detector_active_enabled
        and health.streams_active
        and health.legacy_collectors_quiescent
    )
    collection_running = bool(health.timer_active_enabled)

    rate_limit_incident = bool(
        efficiency.repeated_provider_rejection
        and efficiency.latest_cycle_recent
    )
    rate_limit_paused = bool(
        rate_limit_incident
        and efficiency.timer_paused
    )
    pause_required = bool(
        rate_limit_incident
        and efficiency.pause_recommended
    )
    autopause_failure_relevant = bool(
        autopause_service_failed
        and rate_limit_incident
        and not efficiency.timer_paused
    )

    if not topology_ready:
        state = "TOPOLOGY_NOT_READY"
    elif pause_required:
        state = "RATE_LIMIT_PAUSE_REQUIRED"
    elif rate_limit_paused:
        state = "RATE_LIMIT_PAUSED"
    elif not collection_running:
        state = "TIMER_NOT_RUNNING"
    elif health.collection_healthy and not efficiency.attention_required:
        state = "HEALTHY"
    else:
        state = "COLLECTION_ATTENTION"

    attention = state != "HEALTHY"
    future_timer_cycles_paused = bool(not efficiency.timer_enabled)
    waste_guard_satisfied = bool(
        not rate_limit_incident
        or future_timer_cycles_paused
    )

    health_after = HEALTH.inspect_timer_health(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        history_limit=history_limit,
        max_cycle_age_seconds=max_cycle_age_seconds,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        now=now,
        runner=runner,
    )
    efficiency_after = EFFICIENCY.inspect_rpc_efficiency(
        data_root=data_root,
        env_file=env_file,
        lookback_hours=lookback_hours,
        cycle_history_limit=history_limit,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        max_cycle_gap_seconds=max_cycle_age_seconds,
        max_latest_age_seconds=max_cycle_age_seconds,
        now=now,
        runner=runner,
    )
    autopause_failed_after = _unit_failed(
        runner,
        AUTOPAUSE_UNIT,
    )
    if not _boundary_ok(health_after) or not _boundary_ok(efficiency_after):
        raise ValueError("Phase-2 operator status crossed the read-only boundary")
    if (
        _health_projection(health_after) != health_projection
        or _efficiency_projection(efficiency_after) != efficiency_projection
        or autopause_failed_after != autopause_service_failed
    ):
        raise ValueError(
            "Phase-2 operator inputs changed during status inspection"
        )

    return Phase2OperatorStatusReport(
        state=state,
        attention_required=attention,
        topology_ready=topology_ready,
        collection_running=collection_running,
        provider_rate_limit_incident=rate_limit_incident,
        provider_rate_limit_paused=rate_limit_paused,
        autopause_service_failed=autopause_service_failed,
        autopause_failure_relevant=autopause_failure_relevant,
        future_timer_cycles_paused=future_timer_cycles_paused,
        rate_limit_waste_guard_satisfied=waste_guard_satisfied,
        discovery_cache_reusable_now=bool(
            efficiency.discovery_cache.reusable_now
        ),
        collector_attempts_are_not_provider_credits=True,
        provider_credit_count_available=False,
        timer_health=health.to_record(),
        rpc_efficiency=efficiency.to_record(),
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Report one local read-only Phase-2 operator status by combining "
            "isolated timer health with RPC-efficiency/autopause evidence. "
            "No RPC calls or provider-credit estimates are made."
        )
    )
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--history-limit", type=int, default=8)
    parser.add_argument("--rate-limit-streak-threshold", type=int, default=2)
    parser.add_argument("--max-cycle-age-seconds", type=int, default=2700)
    args = parser.parse_args()

    report = inspect_operator_status(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        lookback_hours=args.lookback_hours,
        history_limit=args.history_limit,
        rate_limit_streak_threshold=args.rate_limit_streak_threshold,
        max_cycle_age_seconds=args.max_cycle_age_seconds,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.attention_required:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
