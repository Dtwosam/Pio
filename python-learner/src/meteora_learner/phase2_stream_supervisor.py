from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Sequence


BACKOFF_SECONDS = (5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0)
RunOnce = Callable[[Sequence[str]], tuple[int, float]]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class Phase2StreamSupervisorReport:
    runs_completed: int
    last_returncode: int | None
    consecutive_failures: int
    last_runtime_seconds: float | None
    stable_reset_seconds: float
    failure_only_backoff: bool
    healthy_stream_throttled: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def restart_backoff_seconds(consecutive_failures: int) -> float:
    if consecutive_failures <= 0:
        return 0.0
    index = min(consecutive_failures - 1, len(BACKOFF_SECONDS) - 1)
    return BACKOFF_SECONDS[index]


def build_event_stream_command(
    *,
    python_executable: str,
    executor_path: str | Path,
    watch_executor_path: str | Path,
    cache_database: str | Path,
    pool_address: str | None = None,
) -> tuple[str, ...]:
    command = [
        str(python_executable),
        "-m",
        "meteora_learner.phase2_event_prestate",
        "--executor",
        str(executor_path),
        "--watch-executor",
        str(watch_executor_path),
        "--cache-database",
        str(cache_database),
    ]
    if pool_address:
        command.extend(["--pool", pool_address])
    return tuple(command)


def _run_once(command: Sequence[str]) -> tuple[int, float]:
    started = time.monotonic()
    completed = subprocess.run(
        list(command),
        check=False,
    )
    elapsed = max(0.0, time.monotonic() - started)
    return int(completed.returncode), elapsed


def supervise_prestate_stream(
    command: Sequence[str],
    *,
    stable_reset_seconds: float = 300.0,
    max_runs: int = 0,
    run_once: RunOnce = _run_once,
    sleeper: Sleeper = time.sleep,
) -> Phase2StreamSupervisorReport:
    """
    Restart the event-driven prestate stream with failure-only backoff.

    A running stream is never delayed or rate limited. Backoff is applied only
    after the child process exits. Any run that remains alive for at least
    stable_reset_seconds resets the failure streak before the next reconnect.
    """
    if stable_reset_seconds <= 0:
        raise ValueError("stable_reset_seconds must be positive")
    if max_runs < 0:
        raise ValueError("max_runs cannot be negative")
    if not command:
        raise ValueError("stream command is required")

    runs = 0
    consecutive_failures = 0
    last_returncode: int | None = None
    last_runtime: float | None = None

    while max_runs == 0 or runs < max_runs:
        returncode, runtime_seconds = run_once(command)
        runs += 1
        last_returncode = int(returncode)
        last_runtime = max(0.0, float(runtime_seconds))

        if last_runtime >= stable_reset_seconds:
            consecutive_failures = 0
        consecutive_failures += 1

        if max_runs > 0 and runs >= max_runs:
            break

        delay = restart_backoff_seconds(consecutive_failures)
        print(
            json.dumps(
                {
                    "kind": "STREAM_RESTART_BACKOFF",
                    "returncode": last_returncode,
                    "runtime_seconds": round(last_runtime, 3),
                    "consecutive_failures": consecutive_failures,
                    "sleep_seconds": delay,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        sleeper(delay)

    return Phase2StreamSupervisorReport(
        runs_completed=runs,
        last_returncode=last_returncode,
        consecutive_failures=consecutive_failures,
        last_runtime_seconds=last_runtime,
        stable_reset_seconds=stable_reset_seconds,
        failure_only_backoff=True,
        healthy_stream_throttled=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Supervise the event-driven Phase-2 prestate stream. "
            "Healthy streams are never throttled; reconnect backoff applies "
            "only after the stream process exits."
        )
    )
    parser.add_argument("--executor", required=True)
    parser.add_argument("--watch-executor", required=True)
    parser.add_argument("--cache-database", required=True)
    parser.add_argument("--pool")
    parser.add_argument(
        "--stable-reset-seconds",
        type=float,
        default=300.0,
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=0,
        help="0 means supervise indefinitely",
    )
    args = parser.parse_args()

    command = build_event_stream_command(
        python_executable=sys.executable,
        executor_path=args.executor,
        watch_executor_path=args.watch_executor,
        cache_database=args.cache_database,
        pool_address=args.pool,
    )
    report = supervise_prestate_stream(
        command,
        stable_reset_seconds=args.stable_reset_seconds,
        max_runs=args.max_runs,
    )
    if args.max_runs > 0:
        print(json.dumps(report.to_record(), indent=2))


if __name__ == "__main__":
    main()
