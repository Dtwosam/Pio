#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
ACTIVATION_TOOL = TOOLS_DIR / "check_phase2_isolated_activation.py"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed deployment tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ACTIVATION = _load(
    ACTIVATION_TOOL,
    "phase2_timer_health_activation_helpers",
)

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]
Now = Callable[[], datetime]

PROGRESS_EDGE_TYPE = "PHASE2_EVIDENCE_CYCLE_PROGRESS_V1"
TIMER_UNIT = "pio-phase2-isolated-evidence-cycle.timer"
EVIDENCE_SERVICE = "pio-phase2-isolated-evidence-cycle.service"
DETECTOR_UNIT = "pio-phase2-isolated-add-detector.service"

ADDITIONAL_LEGACY_UNITS = (
    "pio-phase2-position-observer.service",
    "pio-phase2-position-observer.timer",
    "pio-phase2-evidence-cycle.service",
    "pio-phase2-evidence-cycle.timer",
)


@dataclass(frozen=True)
class TimerHealthCycle:
    evidence_id: int
    as_of: str
    status: str
    rpc_rate_limited: bool
    rpc_circuit_open: bool
    stages_failed: int
    stages_skipped: int


@dataclass(frozen=True)
class TimerHealthUnit:
    name: str
    active: bool
    enabled: bool


@dataclass(frozen=True)
class Phase2TimerHealthReport:
    runtime_ready: bool
    installed_units_exact: bool
    env_ready: bool
    data_ready: bool
    detector_state_ready: bool
    detector_active_enabled: bool
    streams_active: bool
    timer_active: bool
    timer_enabled: bool
    timer_active_enabled: bool
    legacy_collectors_quiescent: bool
    evidence_service_active: bool
    cycles: tuple[TimerHealthCycle, ...]
    latest_cycle_age_seconds: float | None
    latest_cycle_recent: bool
    latest_cycle_failed: bool
    consecutive_rpc_rate_limited: int
    rate_limit_streak_threshold: int
    pause_recommended: bool
    collection_healthy: bool
    read_only: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _systemctl_state(
    unit: str,
    *,
    runner: SystemctlRunner,
) -> TimerHealthUnit:
    def query(action: str, expected: str) -> bool:
        completed = runner(
            ["systemctl", action, unit],
            capture_output=True,
            text=True,
            check=False,
        )
        return (
            int(completed.returncode) == 0
            and (completed.stdout or "").strip() == expected
        )

    return TimerHealthUnit(
        name=unit,
        active=query("is-active", "active"),
        enabled=query("is-enabled", "enabled"),
    )


def _read_recent_cycles(
    database_path: Path,
    *,
    pool_address: str,
    limit: int,
) -> tuple[TimerHealthCycle, ...]:
    if (
        limit <= 0
        or database_path.is_symlink()
        or not database_path.is_file()
    ):
        return ()

    uri = f"file:{database_path.resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT id, as_of, status, evidence_json
                FROM advanced_edge_evidence
                WHERE edge_type = ?
                  AND pool_address = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (PROGRESS_EDGE_TYPE, pool_address, limit),
            ).fetchall()
    except sqlite3.Error:
        return ()

    cycles = []
    for evidence_id, as_of, status, evidence_json in rows:
        try:
            payload = json.loads(str(evidence_json))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        rpc_rate_limited = payload.get("rpc_rate_limited")
        rpc_circuit_open = payload.get("rpc_circuit_open")
        if not isinstance(rpc_rate_limited, bool):
            rpc_rate_limited = False
            outcomes = payload.get("stage_outcomes")
            if isinstance(outcomes, list):
                rpc_rate_limited = any(
                    isinstance(item, list)
                    and len(item) == 3
                    and item[2] == "RPC_RATE_LIMITED"
                    for item in outcomes
                )
        if not isinstance(rpc_circuit_open, bool):
            rpc_circuit_open = False
            outcomes = payload.get("stage_outcomes")
            if isinstance(outcomes, list):
                rpc_circuit_open = any(
                    isinstance(item, list)
                    and len(item) == 3
                    and item[2] == "RPC_CIRCUIT_OPEN"
                    for item in outcomes
                )

        cycles.append(
            TimerHealthCycle(
                evidence_id=int(evidence_id),
                as_of=str(as_of),
                status=str(status),
                rpc_rate_limited=rpc_rate_limited,
                rpc_circuit_open=rpc_circuit_open,
                stages_failed=int(payload.get("stages_failed", 0) or 0),
                stages_skipped=int(payload.get("stages_skipped", 0) or 0),
            )
        )
    return tuple(cycles)


def _consecutive_rate_limits(
    cycles: tuple[TimerHealthCycle, ...],
) -> int:
    count = 0
    for cycle in cycles:
        if not cycle.rpc_rate_limited:
            break
        count += 1
    return count


def inspect_timer_health(
    *,
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    data_root: str | Path = "/opt/pio/data",
    history_limit: int = 8,
    max_cycle_age_seconds: int = 2700,
    rate_limit_streak_threshold: int = 2,
    now: Now = lambda: datetime.now(timezone.utc),
    runner: SystemctlRunner = subprocess.run,
) -> Phase2TimerHealthReport:
    if history_limit <= 0:
        raise ValueError("history_limit must be positive")
    if max_cycle_age_seconds <= 0:
        raise ValueError("max_cycle_age_seconds must be positive")
    if rate_limit_streak_threshold <= 0:
        raise ValueError("rate_limit_streak_threshold must be positive")

    base = ACTIVATION.inspect_activation(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        runner=runner,
    )

    env_ready = bool(
        base.env_file_regular
        and all(item.configured for item in base.env_keys)
        and base.position_pool_matches_detector_topology
    )
    data_ready = bool(
        base.data_files
        and all(
            item.exists
            and item.regular_file
            and not item.symlink
            for item in base.data_files
        )
    )
    detector_state_ready = bool(
        base.detector_state_valid
        and base.detector_cursors_complete
    )

    base_states = {
        item.name: item
        for item in base.unit_states
    }
    detector = base_states.get(DETECTOR_UNIT)
    detector_active_enabled = bool(
        detector and detector.active and detector.enabled
    )

    stream_names = tuple(
        name
        for name in ACTIVATION.NEW_INACTIVE_UNITS
        if name.startswith("pio-phase2-isolated-prestate-stream@")
    )
    streams_active = bool(
        stream_names
        and all(
            name in base_states
            and base_states[name].active
            for name in stream_names
        )
    )

    timer = base_states.get(TIMER_UNIT)
    timer_active = bool(timer and timer.active)
    timer_enabled = bool(timer and timer.enabled)
    timer_active_enabled = timer_active and timer_enabled
    evidence_service = base_states.get(EVIDENCE_SERVICE)
    evidence_service_active = bool(
        evidence_service and evidence_service.active
    )

    legacy_states = {
        name: _systemctl_state(name, runner=runner)
        for name in ADDITIONAL_LEGACY_UNITS
    }
    legacy_clear = all(
        not state.active and not state.enabled
        for state in legacy_states.values()
    ) and all(
        name in base_states
        and not base_states[name].active
        and not base_states[name].enabled
        for name in ACTIVATION.LEGACY_UNITS
    )

    env_values = ACTIVATION._read_env(Path(env_file).expanduser())
    pool_address = env_values.get(
        "PIO_PHASE2_POSITION_POOL",
        "",
    ).strip()
    cycles = (
        _read_recent_cycles(
            Path(data_root).expanduser() / "pio.db",
            pool_address=pool_address,
            limit=history_limit,
        )
        if pool_address
        else ()
    )

    latest_age: float | None = None
    latest_recent = False
    if cycles:
        try:
            latest_age = (
                now().astimezone(timezone.utc)
                - _parse_time(cycles[0].as_of)
            ).total_seconds()
        except (TypeError, ValueError):
            latest_age = None
        latest_recent = bool(
            latest_age is not None
            and 0 <= latest_age <= max_cycle_age_seconds
        )

    rate_limit_streak = _consecutive_rate_limits(cycles)
    latest_failed = bool(
        cycles and cycles[0].status == "COLLECTION_FAILED"
    )

    topology_ready = bool(
        base.runtime_ready
        and base.installed_units_exact
        and env_ready
        and data_ready
        and detector_state_ready
        and detector_active_enabled
        and streams_active
        and timer_active_enabled
        and legacy_clear
    )
    pause_recommended = bool(
        topology_ready
        and latest_recent
        and rate_limit_streak >= rate_limit_streak_threshold
    )
    healthy = bool(
        topology_ready
        and latest_recent
        and not latest_failed
        and rate_limit_streak == 0
    )

    return Phase2TimerHealthReport(
        runtime_ready=bool(base.runtime_ready),
        installed_units_exact=bool(base.installed_units_exact),
        env_ready=env_ready,
        data_ready=data_ready,
        detector_state_ready=detector_state_ready,
        detector_active_enabled=detector_active_enabled,
        streams_active=streams_active,
        timer_active=timer_active,
        timer_enabled=timer_enabled,
        timer_active_enabled=timer_active_enabled,
        legacy_collectors_quiescent=legacy_clear,
        evidence_service_active=evidence_service_active,
        cycles=cycles,
        latest_cycle_age_seconds=latest_age,
        latest_cycle_recent=latest_recent,
        latest_cycle_failed=latest_failed,
        consecutive_rpc_rate_limited=rate_limit_streak,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        pause_recommended=pause_recommended,
        collection_healthy=healthy,
        read_only=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only health check for the running isolated Phase-2 evidence "
            "timer. Repeated provider rate limits are surfaced separately from "
            "ordinary evidence gaps; healthy collection is never throttled."
        )
    )
    parser.add_argument("--runtime-root", default="/opt/pio-phase2-runtime")
    parser.add_argument("--unit-destination", default="/etc/systemd/system")
    parser.add_argument("--env-file", default="/etc/pio/pio.env")
    parser.add_argument("--data-root", default="/opt/pio/data")
    parser.add_argument("--history-limit", type=int, default=8)
    parser.add_argument("--max-cycle-age-seconds", type=int, default=2700)
    parser.add_argument("--rate-limit-streak-threshold", type=int, default=2)
    args = parser.parse_args()

    report = inspect_timer_health(
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        data_root=args.data_root,
        history_limit=args.history_limit,
        max_cycle_age_seconds=args.max_cycle_age_seconds,
        rate_limit_streak_threshold=args.rate_limit_streak_threshold,
    )
    print(json.dumps(report.to_record(), indent=2))

    if not report.collection_healthy:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
