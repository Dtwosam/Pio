#!/opt/pio/python-learner/.venv/bin/python

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = "/opt/pio"
RUST = f"{ROOT}/rust-executor/target/release/meteora-executor"

CACHE_DB = Path(f"{ROOT}/data/phase2-prestate-cache.db")

RPC = os.environ["SOLANA_RPC_URL"]

FAST_POOL = "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ"
SLOW_POOL = "DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf"

DQ9_POLL_SECONDS = 60.0
CACHE_RETENTION_HOURS = 24

# These are not throughput caps. They only suppress requests that cannot add
# evidence (same slot/bin already cached) or that the RPC provider is actively
# rejecting. Healthy, evidence-producing captures remain unthrottled.
DUPLICATE_SLOT_RECHECK_SECONDS = float(
    os.getenv("PIO_PHASE2_DUPLICATE_RECHECK_SECONDS", "0.25")
)
RATE_LIMIT_BACKOFF_SECONDS = (
    5.0,
    10.0,
    20.0,
    40.0,
    80.0,
    160.0,
    300.0,
    600.0,
)

STOP = threading.Event()
PRINT_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(kind: str, **fields) -> None:
    with PRINT_LOCK:
        print(
            json.dumps(
                {
                    "time": now(),
                    "kind": kind,
                    **fields,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )


def is_rate_limited_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return True

    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "429",
            "too many requests",
            "rate limit",
            "ratelimit",
        )
    )


def rate_limit_backoff_seconds(consecutive_rate_limits: int) -> float:
    if consecutive_rate_limits <= 0:
        return 0.0
    index = min(
        consecutive_rate_limits - 1,
        len(RATE_LIMIT_BACKOFF_SECONDS) - 1,
    )
    return RATE_LIMIT_BACKOFF_SECONDS[index]


def error_category(exc: BaseException) -> str:
    if is_rate_limited_error(exc):
        return "RPC_RATE_LIMITED"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "EXECUTOR_TIMEOUT"
    return type(exc).__name__.upper()


def post_capture_delay(*, stored: bool) -> float:
    # The cache key is (pool, capture_slot_end, active_bin_id). If a successful
    # capture was not stored, an immediate retry would be discarded by the same
    # uniqueness rule and therefore cannot add evidence.
    return 0.0 if stored else max(0.0, DUPLICATE_SLOT_RECHECK_SECONDS)


def connect_cache():
    conn = sqlite3.connect(
        CACHE_DB,
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def initialize_cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)

    with connect_cache() as conn:
        conn.executescript("""
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
        """)


def prune_cache():
    with connect_cache() as conn:
        cursor = conn.execute(
            """
            DELETE FROM prestate_snapshots
            WHERE julianday(observed_at)
                < julianday('now', ?)
            """,
            (f"-{CACHE_RETENTION_HOURS} hours",),
        )

        deleted = cursor.rowcount

    if deleted:
        log(
            "CACHE_PRUNE",
            deleted=deleted,
            retention_hours=CACHE_RETENTION_HOURS,
        )


def trim_to_active_bin(snapshot: dict) -> tuple[dict, str]:
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
        raise RuntimeError(
            f"active bin {active_bin_id} missing from inspect-pool snapshot"
        )

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


def persist_cache(snapshot: dict) -> bool:
    trimmed, array_address = trim_to_active_bin(snapshot)

    observed_at = now()

    with connect_cache() as conn:
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
                json.dumps(
                    trimmed,
                    separators=(",", ":"),
                ),
            ),
        )

        return cursor.rowcount == 1


def rpc_signatures(pool: str, limit: int = 20) -> list[dict]:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [
            pool,
            {
                "limit": limit,
                "commitment": "confirmed",
            },
        ],
    }

    request = urllib.request.Request(
        RPC,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        reply = json.load(response)

    if reply.get("error"):
        raise RuntimeError(reply["error"])

    return reply.get("result") or []


def capture_pool(pool: str, *, mode: str):
    started = time.monotonic()

    proc = subprocess.run(
        [
            RUST,
            "inspect-pool-env",
            pool,
            "0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"inspect-pool-env failed: "
            f"{proc.stderr.strip()[:1000]}"
        )

    snapshot = json.loads(proc.stdout)

    stored = persist_cache(snapshot)

    elapsed = time.monotonic() - started

    log(
        "SNAPSHOT",
        pool=pool,
        mode=mode,
        capture_slot_start=snapshot.get("capture_slot_start"),
        capture_slot_end=snapshot.get("capture_slot_end"),
        slot_width=(
            int(snapshot["capture_slot_end"])
            - int(snapshot["capture_slot_start"])
        ),
        active_bin_id=snapshot.get("active_bin_id"),
        cached=stored,
        retained_bins=1,
        cycle_ms=round(elapsed * 1000, 1),
    )

    return stored


def continuous_worker():
    log(
        "WORKER_START",
        pool=FAST_POOL,
        mode="continuous",
    )

    failures = 0
    consecutive_rate_limits = 0
    captures = 0

    while not STOP.is_set():
        try:
            stored = capture_pool(
                FAST_POOL,
                mode="continuous",
            )

            failures = 0
            consecutive_rate_limits = 0
            captures += 1

            if captures % 250 == 0:
                prune_cache()

            delay = post_capture_delay(stored=stored)
            if delay:
                STOP.wait(delay)

        except Exception as exc:
            failures += 1
            category = error_category(exc)

            if category == "RPC_RATE_LIMITED":
                consecutive_rate_limits += 1
                delay = rate_limit_backoff_seconds(
                    consecutive_rate_limits
                )
                log(
                    "RPC_BACKOFF",
                    pool=FAST_POOL,
                    mode="continuous",
                    consecutive_failures=failures,
                    consecutive_rate_limits=consecutive_rate_limits,
                    sleep_seconds=delay,
                    category=category,
                )
            else:
                consecutive_rate_limits = 0
                delay = min(
                    5.0,
                    max(0.5, failures * 0.5),
                )
                log(
                    "ERROR",
                    pool=FAST_POOL,
                    mode="continuous",
                    consecutive_failures=failures,
                    category=category,
                )

            STOP.wait(delay)


def dq9_worker():
    log(
        "WORKER_START",
        pool=SLOW_POOL,
        mode="activity_triggered",
        poll_seconds=DQ9_POLL_SECONDS,
    )

    cursor = None

    try:
        rows = rpc_signatures(SLOW_POOL)
        cursor = rows[0]["signature"] if rows else None

        capture_pool(
            SLOW_POOL,
            mode="baseline",
        )

        log(
            "BASELINE",
            pool=SLOW_POOL,
            cursor=cursor,
        )

    except Exception as exc:
        log(
            "ERROR",
            pool=SLOW_POOL,
            mode="baseline",
            category=error_category(exc),
        )

    while not STOP.wait(DQ9_POLL_SECONDS):
        try:
            rows = rpc_signatures(SLOW_POOL)

            if not rows:
                continue

            newest = rows[0]["signature"]

            if cursor is None:
                cursor = newest

                capture_pool(
                    SLOW_POOL,
                    mode="activity_triggered",
                )
                continue

            if newest == cursor:
                continue

            signatures = [
                row["signature"]
                for row in rows
            ]

            cursor_missed = cursor not in signatures

            if cursor_missed:
                new_count = len(rows)
            else:
                new_count = signatures.index(cursor)

            log(
                "ACTIVITY",
                pool=SLOW_POOL,
                previous_cursor=cursor,
                newest_signature=newest,
                new_signatures=new_count,
                cursor_missed=cursor_missed,
            )

            capture_pool(
                SLOW_POOL,
                mode="activity_triggered",
            )

            cursor = newest

        except Exception as exc:
            log(
                "ERROR",
                pool=SLOW_POOL,
                mode="activity_triggered",
                category=error_category(exc),
            )


def request_stop(signum, frame):
    log(
        "STOP_REQUESTED",
        signal=signum,
    )
    STOP.set()


initialize_cache()
prune_cache()

signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)

log(
    "START",
    cache_db=str(CACHE_DB),
    cache_retention_hours=CACHE_RETENTION_HOURS,
    fast_pool=FAST_POOL,
    fast_mode="continuous",
    slow_pool=SLOW_POOL,
)

threads = [
    threading.Thread(
        target=continuous_worker,
        name="phase2-54vp-continuous",
        daemon=True,
    ),
    threading.Thread(
        target=dq9_worker,
        name="phase2-dq9-triggered",
        daemon=True,
    ),
]

for thread in threads:
    thread.start()

while not STOP.is_set():
    for thread in threads:
        if not thread.is_alive():
            log(
                "WORKER_DIED",
                worker=thread.name,
            )
            STOP.set()
            break

    STOP.wait(1.0)

for thread in threads:
    thread.join(timeout=5)

log("STOPPED")
