from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS raw_api_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    entity_key TEXT,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_endpoint_time
ON raw_api_observations(endpoint, observed_at);

CREATE INDEX IF NOT EXISTS idx_raw_entity_time
ON raw_api_observations(entity_key, observed_at);

CREATE TABLE IF NOT EXISTS pool_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    address TEXT NOT NULL,
    name TEXT,
    tvl REAL,
    volume_24h REAL,
    fees_24h REAL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pool_snapshots_address_time
ON pool_snapshots(address, observed_at);

CREATE TABLE IF NOT EXISTS collector_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    pools_seen INTEGER NOT NULL DEFAULT 0,
    history_pools_seen INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def save_raw(
        self,
        endpoint: str,
        payload: Any,
        *,
        observed_at: str | None = None,
        entity_key: str | None = None,
    ) -> None:
        observed_at = observed_at or utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO raw_api_observations(observed_at, endpoint, entity_key, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (observed_at, endpoint, entity_key, json.dumps(payload, separators=(",", ":"))),
            )

    def save_pool_snapshot(self, pool: dict[str, Any], *, observed_at: str | None = None) -> bool:
        address = _first(pool, "address", "pool_address", "lb_pair")
        if not address:
            return False

        observed_at = observed_at or utc_now_iso()
        name = _first(pool, "name", "pool_name", "pair_name")
        tvl = _number(_first(pool, "tvl", "liquidity", "total_liquidity"))
        volume_24h = _number(_first(pool, "volume_24h", "trade_volume_24h", "volume24h"))
        fees_24h = _number(_first(pool, "fees_24h", "fee_24h", "fees24h"))

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pool_snapshots(
                    observed_at, address, name, tvl, volume_24h, fees_24h, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at,
                    str(address),
                    str(name) if name is not None else None,
                    tvl,
                    volume_24h,
                    fees_24h,
                    json.dumps(pool, separators=(",", ":")),
                ),
            )
        return True

    def start_run(self, run_id: str, started_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO collector_runs(run_id, started_at, status)
                VALUES (?, ?, 'RUNNING')
                """,
                (run_id, started_at),
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        pools_seen: int,
        history_pools_seen: int,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE collector_runs
                SET finished_at = ?, status = ?, pools_seen = ?, history_pools_seen = ?, error = ?
                WHERE run_id = ?
                """,
                (utc_now_iso(), status, pools_seen, history_pools_seen, error, run_id),
            )
