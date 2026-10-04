#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
HEALTH_TOOL = TOOLS_DIR / "check_phase2_isolated_timer_health.py"
EFFICIENCY_TOOL = TOOLS_DIR / "check_phase2_rpc_efficiency.py"
HEALTH_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_timer_health.py"
EFFICIENCY_TOOL_RELATIVE = "deploy/tools/check_phase2_rpc_efficiency.py"
AUTOPAUSE_UNIT = "pio-phase2-isolated-rate-limit-pause.service"
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
        or current.st_mode != opened.st_mode
        or current.st_size != opened.st_size
        or current.st_mtime_ns != opened.st_mtime_ns
        or current.st_ctime_ns != opened.st_ctime_ns
    ):
        raise ValueError(f"{label} path changed after capture")


def _capture_regular_file(
    path: Path,
    *,
    label: str,
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
        if before.st_size < 0 or before.st_size > _MAX_TOOL_BYTES:
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
            or after.st_mode != before.st_mode
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
    return _CapturedFile(
        path=resolved,
        encoded=encoded,
        opened=before,
    )


def _load_captured(
    path: Path,
    name: str,
    *,
    label: str,
) -> tuple[Any, _CapturedFile]:
    captured = _capture_regular_file(path, label=label)
    spec = importlib.util.spec_from_file_location(name, captured.path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {label}: {captured.path}")
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
        label=label,
    )
    return module, captured


HEALTH, _HEALTH_CAPTURE = _load_captured(
    HEALTH_TOOL,
    "phase2_operator_timer_health",
    label="reviewed timer-health tool",
)
EFFICIENCY, _EFFICIENCY_CAPTURE = _load_captured(
    EFFICIENCY_TOOL,
    "phase2_operator_rpc_efficiency",
    label="reviewed RPC-efficiency tool",
)
_HEALTH_SHA256 = hashlib.sha256(_HEALTH_CAPTURE.encoded).hexdigest()
_EFFICIENCY_SHA256 = hashlib.sha256(
    _EFFICIENCY_CAPTURE.encoded
).hexdigest()

Now = Callable[[], datetime]
SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]


def _repo_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("cannot resolve reviewed operator source commit")
    commit = completed.stdout.strip()
    if len(commit) not in {40, 64}:
        raise ValueError("reviewed operator source commit is invalid")
    return commit


def _dependency_source_identity() -> tuple[str, str, str]:
    captures = (
        (
            _HEALTH_CAPTURE,
            HEALTH_TOOL_RELATIVE,
            "reviewed timer-health tool",
        ),
        (
            _EFFICIENCY_CAPTURE,
            EFFICIENCY_TOOL_RELATIVE,
            "reviewed RPC-efficiency tool",
        ),
    )
    for captured, _relative, label in captures:
        _assert_regular_path_stable(
            captured.path,
            captured.opened,
            label=label,
        )

    commit = _repo_head()
    for captured, relative, label in captures:
        historical = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            check=False,
        )
        if historical.returncode != 0:
            raise ValueError(
                f"{label} is not present at reviewed source commit"
            )
        if historical.stdout != captured.encoded:
            raise ValueError(
                f"{label} bytes do not match reviewed source commit"
            )
        _assert_regular_path_stable(
            captured.path,
            captured.opened,
            label=label,
        )

    return commit, _HEALTH_SHA256, _EFFICIENCY_SHA256


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
    dependency_source = _dependency_source_identity()
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
    if _dependency_source_identity() != dependency_source:
        raise ValueError(
            "reviewed Phase-2 operator dependencies changed during status inspection"
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
