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
HEALTH_TOOL_RELATIVE = "deploy/tools/check_phase2_isolated_timer_health.py"

_MAX_TOOL_BYTES = 4 * 1024 * 1024

SystemctlRunner = Callable[..., subprocess.CompletedProcess[str]]
Now = Callable[[], datetime]
TIMER_UNIT = "pio-phase2-isolated-evidence-cycle.timer"


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
    return _CapturedFile(path=resolved, encoded=encoded, opened=before)


def _load_captured_health(
    path: Path,
    name: str,
) -> tuple[Any, _CapturedFile]:
    captured = _capture_regular_file(
        path,
        label="reviewed timer health checker",
        max_bytes=_MAX_TOOL_BYTES,
    )
    spec = importlib.util.spec_from_file_location(name, captured.path)
    if spec is None or spec.loader is None:
        raise ValueError(
            f"cannot load reviewed deployment tool: {captured.path}"
        )
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
        label="reviewed timer health checker",
    )
    return module, captured


HEALTH, _HEALTH_CAPTURE = _load_captured_health(
    HEALTH_TOOL,
    "phase2_standalone_autopause_health",
)
_HEALTH_SHA256 = hashlib.sha256(_HEALTH_CAPTURE.encoded).hexdigest()


@dataclass(frozen=True)
class Phase2RateLimitAutopauseReport:
    database_ready: bool
    database_path: str
    pool_address: str | None
    cycles_checked: int
    consecutive_rpc_rate_limited: int
    rate_limit_streak_threshold: int
    repeated_provider_rejection: bool
    latest_cycle_age_seconds: float | None
    latest_cycle_recent: bool
    timer_enabled_before: bool
    timer_active_before: bool
    pause_recommended: bool
    apply_requested: bool
    applied: bool
    timer_enabled_after: bool
    timer_active_after: bool
    future_timer_cycles_paused: bool
    failure_step: str | None
    read_only_evidence_check: bool
    rpc_called: bool
    database_write_performed: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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


def _health_source_identity() -> tuple[str, str]:
    _assert_regular_path_stable(
        _HEALTH_CAPTURE.path,
        _HEALTH_CAPTURE.opened,
        label="reviewed timer health checker",
    )
    commit = _repo_head()
    historical = subprocess.run(
        ["git", "show", f"{commit}:{HEALTH_TOOL_RELATIVE}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    if historical.returncode != 0:
        raise ValueError(
            "reviewed timer health checker is not present at reviewed source commit"
        )
    if historical.stdout != _HEALTH_CAPTURE.encoded:
        raise ValueError(
            "reviewed timer health checker bytes do not match reviewed source commit"
        )
    _assert_regular_path_stable(
        _HEALTH_CAPTURE.path,
        _HEALTH_CAPTURE.opened,
        label="reviewed timer health checker",
    )
    return commit, _HEALTH_SHA256


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
    return bool(
        int(completed.returncode) == 0
        and (completed.stdout or "").strip() == expected
    )


def _decision_identity(health: Any) -> tuple[Any, ...]:
    cycles = tuple(
        (
            int(item.evidence_id),
            str(item.as_of),
            str(item.status),
            bool(item.rpc_rate_limited),
            bool(item.rpc_circuit_open),
            int(item.stages_failed),
            int(item.stages_skipped),
        )
        for item in health.cycles
    )
    return (
        health.pool_address,
        cycles,
        bool(health.latest_cycle_recent),
        bool(health.latest_cycle_failed),
        int(health.consecutive_rpc_rate_limited),
        int(health.rate_limit_streak_threshold),
        bool(health.timer_enabled),
        bool(health.timer_active),
        bool(health.pause_recommended),
    )


def _inspect_health(
    *,
    runtime_root: str | Path,
    unit_destination: str | Path,
    env_file: str | Path,
    data_root: str | Path,
    history_limit: int,
    max_latest_age_seconds: int,
    max_cycle_gap_seconds: int,
    rate_limit_streak_threshold: int,
    decision_time: datetime,
    runner: SystemctlRunner,
) -> Any:
    source_before = _health_source_identity()
    report = HEALTH.inspect_timer_health(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        history_limit=history_limit,
        max_cycle_age_seconds=max_latest_age_seconds,
        max_rate_limit_gap_seconds=max_cycle_gap_seconds,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        now=lambda: decision_time,
        runner=runner,
    )
    if _health_source_identity() != source_before:
        raise ValueError(
            "reviewed timer health checker changed during autopause inspection"
        )
    return report


def _report(
    *,
    health: Any,
    database_path: Path,
    apply: bool,
    applied: bool,
    timer_enabled_after: bool,
    timer_active_after: bool,
    failure_step: str | None,
    service_control_performed: bool,
) -> Phase2RateLimitAutopauseReport:
    repeated = (
        int(health.consecutive_rpc_rate_limited)
        >= int(health.rate_limit_streak_threshold)
    )
    return Phase2RateLimitAutopauseReport(
        database_ready=bool(health.data_ready),
        database_path=str(database_path),
        pool_address=health.pool_address,
        cycles_checked=len(health.cycles),
        consecutive_rpc_rate_limited=int(
            health.consecutive_rpc_rate_limited
        ),
        rate_limit_streak_threshold=int(
            health.rate_limit_streak_threshold
        ),
        repeated_provider_rejection=repeated,
        latest_cycle_age_seconds=health.latest_cycle_age_seconds,
        latest_cycle_recent=bool(health.latest_cycle_recent),
        timer_enabled_before=bool(health.timer_enabled),
        timer_active_before=bool(health.timer_active),
        pause_recommended=bool(health.pause_recommended),
        apply_requested=apply,
        applied=applied,
        timer_enabled_after=timer_enabled_after,
        timer_active_after=timer_active_after,
        future_timer_cycles_paused=(
            not timer_enabled_after and not timer_active_after
        ),
        failure_step=failure_step,
        read_only_evidence_check=True,
        rpc_called=False,
        database_write_performed=False,
        service_control_performed=service_control_performed,
    )


def autopause(
    *,
    database_path: str | Path = "/opt/pio/data/pio.db",
    runtime_root: str | Path = "/opt/pio-phase2-runtime",
    unit_destination: str | Path = "/etc/systemd/system",
    env_file: str | Path = "/etc/pio/pio.env",
    history_limit: int = 8,
    rate_limit_streak_threshold: int = 2,
    max_cycle_gap_seconds: int = 2700,
    max_latest_age_seconds: int = 2700,
    apply: bool = False,
    now: Now = lambda: datetime.now(timezone.utc),
    runner: SystemctlRunner = subprocess.run,
) -> Phase2RateLimitAutopauseReport:
    if history_limit <= 0:
        raise ValueError("history_limit must be positive")
    if rate_limit_streak_threshold <= 0:
        raise ValueError("rate_limit_streak_threshold must be positive")
    if max_cycle_gap_seconds <= 0:
        raise ValueError("max_cycle_gap_seconds must be positive")
    if max_latest_age_seconds <= 0:
        raise ValueError("max_latest_age_seconds must be positive")

    database = Path(database_path).expanduser()
    if database.name != "pio.db":
        raise ValueError("autopause database must be named pio.db")
    data_root = database.parent
    decision_time = now().astimezone(timezone.utc)

    initial = _inspect_health(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        history_limit=history_limit,
        max_latest_age_seconds=max_latest_age_seconds,
        max_cycle_gap_seconds=max_cycle_gap_seconds,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        decision_time=decision_time,
        runner=runner,
    )

    if not apply or not initial.pause_recommended:
        return _report(
            health=initial,
            database_path=database,
            apply=apply,
            applied=False,
            timer_enabled_after=bool(initial.timer_enabled),
            timer_active_after=bool(initial.timer_active),
            failure_step=None,
            service_control_performed=False,
        )

    revalidated = _inspect_health(
        runtime_root=runtime_root,
        unit_destination=unit_destination,
        env_file=env_file,
        data_root=data_root,
        history_limit=history_limit,
        max_latest_age_seconds=max_latest_age_seconds,
        max_cycle_gap_seconds=max_cycle_gap_seconds,
        rate_limit_streak_threshold=rate_limit_streak_threshold,
        decision_time=decision_time,
        runner=runner,
    )
    if (
        not revalidated.pause_recommended
        or _decision_identity(revalidated) != _decision_identity(initial)
    ):
        return _report(
            health=initial,
            database_path=database,
            apply=True,
            applied=False,
            timer_enabled_after=bool(revalidated.timer_enabled),
            timer_active_after=bool(revalidated.timer_active),
            failure_step="HEALTH_CHANGED_BEFORE_APPLY",
            service_control_performed=False,
        )

    enabled_now = _state(runner, "is-enabled")
    active_now = _state(runner, "is-active")
    if (
        enabled_now != bool(revalidated.timer_enabled)
        or active_now != bool(revalidated.timer_active)
        or not enabled_now
    ):
        return _report(
            health=initial,
            database_path=database,
            apply=True,
            applied=False,
            timer_enabled_after=enabled_now,
            timer_active_after=active_now,
            failure_step="TIMER_STATE_CHANGED_BEFORE_APPLY",
            service_control_performed=False,
        )

    if _health_source_identity() != _health_source_identity():
        # This branch is intentionally unreachable in stable execution, but
        # keep source validation directly adjacent to the mutation boundary.
        raise ValueError(
            "reviewed timer health checker changed before autopause mutation"
        )

    completed = runner(
        ["systemctl", "disable", "--now", TIMER_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    if int(completed.returncode) != 0:
        enabled_after = _state(runner, "is-enabled")
        active_after = _state(runner, "is-active")
        return _report(
            health=initial,
            database_path=database,
            apply=True,
            applied=False,
            timer_enabled_after=enabled_after,
            timer_active_after=active_after,
            failure_step="DISABLE_NOW",
            service_control_performed=True,
        )

    enabled_after = _state(runner, "is-enabled")
    active_after = _state(runner, "is-active")
    applied = not enabled_after and not active_after
    return _report(
        health=initial,
        database_path=database,
        apply=True,
        applied=applied,
        timer_enabled_after=enabled_after,
        timer_active_after=active_after,
        failure_step=None if applied else "VERIFY_PAUSED",
        service_control_performed=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone local guard used after a failed Phase-2 evidence cycle. "
            "It consumes the reviewed timer-health decision for the configured "
            "Phase-2 pool and may disable only the recurring evidence timer "
            "after repeated provider rejection. It makes no network calls."
        )
    )
    parser.add_argument(
        "--database",
        default="/opt/pio/data/pio.db",
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
    parser.add_argument("--history-limit", type=int, default=8)
    parser.add_argument("--rate-limit-streak-threshold", type=int, default=2)
    parser.add_argument("--max-cycle-gap-seconds", type=int, default=2700)
    parser.add_argument("--max-latest-age-seconds", type=int, default=2700)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = autopause(
        database_path=args.database,
        runtime_root=args.runtime_root,
        unit_destination=args.unit_destination,
        env_file=args.env_file,
        history_limit=args.history_limit,
        rate_limit_streak_threshold=args.rate_limit_streak_threshold,
        max_cycle_gap_seconds=args.max_cycle_gap_seconds,
        max_latest_age_seconds=args.max_latest_age_seconds,
        apply=args.apply,
    )
    print(json.dumps(report.to_record(), indent=2))
    if args.apply and report.pause_recommended and not report.applied:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
