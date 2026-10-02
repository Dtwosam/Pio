from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from typing import Any, Callable, Iterable, Iterator, Sequence

from .phase2_rpc_guard import raise_for_executor_failure
from .settings import Settings


ExecutorRunner = Callable[..., subprocess.CompletedProcess[str]]
SnapshotCapture = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class Phase2EventPrestateReport:
    pool_address: str
    baseline_slot: int
    notifications_seen: int
    stale_notifications: int
    refreshes_attempted: int
    refreshes_saved: int
    cache_rows_saved: int
    last_capture_slot: int
    event_driven: bool
    polling_loop_used: bool
    detector_cursor_untouched: bool
    service_control_performed: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_cache(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def initialize_cache(path: Path) -> None:
    if path.exists() and path.is_symlink():
        raise ValueError("prestate cache path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_cache(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS prestate_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                capture_slot_start INTEGER NOT NULL,
                capture_slot_end INTEGER NOT NULL,
                active_bin_id INTEGER NOT NULL,
                bin_array_address TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                UNIQUE(
                    pool_address,
                    capture_slot_end,
                    active_bin_id
                )
            );

            CREATE INDEX IF NOT EXISTS
            idx_prestate_pool_slot
            ON prestate_snapshots(
                pool_address,
                capture_slot_end,
                active_bin_id
            );
            """
        )


def trim_to_active_bin(snapshot: dict[str, Any]) -> tuple[dict[str, Any], str]:
    active_bin_id = int(snapshot["active_bin_id"])
    matched_array = None
    matched_bin = None

    for array in snapshot.get("bin_arrays") or []:
        for bin_row in array.get("bins") or []:
            if int(bin_row["bin_id"]) == active_bin_id:
                matched_array = array
                matched_bin = bin_row
                break
        if matched_bin is not None:
            break

    if matched_array is None or matched_bin is None:
        raise ValueError("active bin is missing from pool snapshot")

    array_address = str(matched_array["address"])
    trimmed = dict(snapshot)
    trimmed["bin_arrays"] = [
        {
            "address": array_address,
            "index": matched_array["index"],
            "lower_bin_id": matched_array["lower_bin_id"],
            "upper_bin_id": matched_array["upper_bin_id"],
            "bins": [matched_bin],
        }
    ]
    return trimmed, array_address


def _capture_slot(snapshot: dict[str, Any], *, pool_address: str) -> int:
    if str(snapshot.get("pool_address", "")) != pool_address:
        raise ValueError("pool snapshot address mismatch")
    start = int(snapshot["capture_slot_start"])
    end = int(snapshot["capture_slot_end"])
    if start < 0 or end < 0:
        raise ValueError("capture slots cannot be negative")
    if start != end:
        raise ValueError("pool snapshot is not single-context")
    return end


def persist_cache(
    path: Path,
    snapshot: dict[str, Any],
    *,
    observed_at: str,
) -> bool:
    trimmed, array_address = trim_to_active_bin(snapshot)
    with connect_cache(path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO prestate_snapshots(
                observed_at,
                pool_address,
                capture_slot_start,
                capture_slot_end,
                active_bin_id,
                bin_array_address,
                raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observed_at,
                str(trimmed["pool_address"]),
                int(trimmed["capture_slot_start"]),
                int(trimmed["capture_slot_end"]),
                int(trimmed["active_bin_id"]),
                array_address,
                json.dumps(trimmed, separators=(",", ":")),
            ),
        )
        return cursor.rowcount == 1


def capture_pool_snapshot(
    *,
    executor_path: str | Path,
    pool_address: str,
    timeout_seconds: int = 90,
    runner: ExecutorRunner = subprocess.run,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    command = [
        str(executor_path),
        "inspect-pool-env",
        pool_address,
        "0",
    ]
    completed = runner(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
    raise_for_executor_failure(completed)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("executor returned invalid pool JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("executor pool snapshot must be a JSON object")
    return payload


def run_event_prestate_session(
    *,
    cache_path: Path,
    pool_address: str,
    notifications: Iterable[dict[str, Any]],
    capture_snapshot: SnapshotCapture,
    observed_at: Callable[[], str] = utc_now_iso,
    max_notifications: int = 0,
) -> Phase2EventPrestateReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if max_notifications < 0:
        raise ValueError("max_notifications cannot be negative")

    initialize_cache(cache_path)

    baseline = capture_snapshot()
    baseline_slot = _capture_slot(
        baseline,
        pool_address=pool_address,
    )
    rows_saved = int(
        persist_cache(
            cache_path,
            baseline,
            observed_at=observed_at(),
        )
    )
    last_capture_slot = baseline_slot
    notifications_seen = 0
    stale_notifications = 0
    refreshes_attempted = 0
    refreshes_saved = 0

    for notification in notifications:
        if not isinstance(notification, dict):
            raise ValueError("account notification must be a JSON object")
        if str(notification.get("account_address", "")) != pool_address:
            raise ValueError("account notification address mismatch")
        notification_slot = int(notification["slot"])
        if notification_slot < 0:
            raise ValueError("notification slot cannot be negative")

        notifications_seen += 1
        if notification_slot <= last_capture_slot:
            stale_notifications += 1
        else:
            refreshes_attempted += 1
            refreshed = capture_snapshot()
            refreshed_slot = _capture_slot(
                refreshed,
                pool_address=pool_address,
            )
            if refreshed_slot < notification_slot:
                raise ValueError(
                    "refreshed snapshot is behind account notification"
                )
            saved = persist_cache(
                cache_path,
                refreshed,
                observed_at=observed_at(),
            )
            rows_saved += int(saved)
            refreshes_saved += int(saved)
            last_capture_slot = refreshed_slot

        if (
            max_notifications > 0
            and notifications_seen >= max_notifications
        ):
            break

    return Phase2EventPrestateReport(
        pool_address=pool_address,
        baseline_slot=baseline_slot,
        notifications_seen=notifications_seen,
        stale_notifications=stale_notifications,
        refreshes_attempted=refreshes_attempted,
        refreshes_saved=refreshes_saved,
        cache_rows_saved=rows_saved,
        last_capture_slot=last_capture_slot,
        event_driven=True,
        polling_loop_used=False,
        detector_cursor_untouched=True,
        service_control_performed=False,
    )


def _notification_stream(
    process: subprocess.Popen[str],
) -> Iterator[dict[str, Any]]:
    if process.stdout is None:
        raise ValueError("watch process stdout is unavailable")
    for line in process.stdout:
        value = line.strip()
        if not value:
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "watch process returned invalid notification JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError("watch notification must be a JSON object")
        yield payload


def _start_watch_process(
    *,
    executor_path: str | Path,
    pool_address: str,
    max_notifications: int,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            str(executor_path),
            "watch-account-env",
            pool_address,
            str(max_notifications),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Maintain Phase-2 pool prestates from account-change events "
            "instead of continuous successful RPC polling."
        )
    )
    parser.add_argument(
        "--pool",
        default=os.getenv("PIO_PHASE2_POSITION_POOL"),
    )
    parser.add_argument(
        "--executor",
        default=(
            "/opt/pio-phase2-runtime/current/"
            "rust-executor/target/release/meteora-executor"
        ),
    )
    parser.add_argument(
        "--cache-database",
        default=os.getenv("PIO_PHASE2_PRESTATE_CACHE"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument(
        "--max-notifications",
        type=int,
        default=0,
        help="0 means run until the account stream ends",
    )
    args = parser.parse_args()

    if not args.pool:
        parser.error("--pool or PIO_PHASE2_POSITION_POOL is required")

    settings = Settings.from_env()
    cache_path = (
        Path(args.cache_database)
        if args.cache_database
        else settings.database_path.with_name(
            "phase2-prestate-cache.db"
        )
    )

    watcher = _start_watch_process(
        executor_path=args.executor,
        pool_address=args.pool,
        max_notifications=args.max_notifications,
    )
    try:
        report = run_event_prestate_session(
            cache_path=cache_path,
            pool_address=args.pool,
            notifications=_notification_stream(watcher),
            capture_snapshot=lambda: capture_pool_snapshot(
                executor_path=args.executor,
                pool_address=args.pool,
                timeout_seconds=args.timeout_seconds,
            ),
            max_notifications=args.max_notifications,
        )
    finally:
        if watcher.poll() is None:
            watcher.terminate()
            try:
                watcher.wait(timeout=5)
            except subprocess.TimeoutExpired:
                watcher.kill()
                watcher.wait(timeout=5)

    print(json.dumps(report.to_record(), indent=2))
    if args.max_notifications == 0:
        # An unbounded production stream is not expected to end. Exit nonzero
        # even after a clean websocket close so systemd reconnects.
        raise SystemExit(2)
    if watcher.returncode not in (0, None):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
