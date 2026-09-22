from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .dlmm_math import price_to_bin_id


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

CREATE TABLE IF NOT EXISTS ohlcv_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_address TEXT NOT NULL,
    source TEXT NOT NULL,
    candle_time TEXT NOT NULL,
    resolution TEXT NOT NULL DEFAULT '',
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    observed_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(pool_address, source, candle_time, resolution)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_pool_time
ON ohlcv_candles(pool_address, candle_time);

CREATE TABLE IF NOT EXISTS volume_buckets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_address TEXT NOT NULL,
    source TEXT NOT NULL,
    bucket_time TEXT NOT NULL,
    volume REAL,
    fees REAL,
    protocol_fees REAL,
    observed_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(pool_address, source, bucket_time)
);

CREATE INDEX IF NOT EXISTS idx_volume_pool_time
ON volume_buckets(pool_address, bucket_time);

CREATE TABLE IF NOT EXISTS chain_pool_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    active_bin_id INTEGER NOT NULL,
    bin_step INTEGER NOT NULL,
    token_x_mint TEXT NOT NULL,
    token_y_mint TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chain_pool_time
ON chain_pool_snapshots(pool_address, observed_at);

CREATE TABLE IF NOT EXISTS bin_liquidity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    bin_array_index INTEGER NOT NULL,
    bin_id INTEGER NOT NULL,
    amount_x TEXT NOT NULL,
    amount_y TEXT NOT NULL,
    liquidity_supply TEXT NOT NULL,
    fee_amount_x_per_token_stored TEXT NOT NULL,
    fee_amount_y_per_token_stored TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bin_liquidity_pool_time
ON bin_liquidity_snapshots(pool_address, observed_at, bin_id);

CREATE TABLE IF NOT EXISTS data_quality_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    value REAL,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_quality_entity_time
ON data_quality_checks(entity_type, entity_key, checked_at);

CREATE TABLE IF NOT EXISTS collection_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    entity_key TEXT,
    error TEXT NOT NULL
);

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

VOLUME_BUCKET_EXTRA_COLUMNS = {
    "protocol_fees": "REAL",
}

POOL_SNAPSHOT_EXTRA_COLUMNS = {
    "current_price": "REAL",
    "bin_step": "INTEGER",
    "active_bin_id": "INTEGER",
    "apr": "REAL",
    "apy": "REAL",
    "token_x_symbol": "TEXT",
    "token_y_symbol": "TEXT",
    "token_x_decimals": "INTEGER",
    "token_y_decimals": "INTEGER",
    "dynamic_fee_pct": "REAL",
    "base_fee_pct": "REAL",
    "max_fee_pct": "REAL",
    "protocol_fee_pct": "REAL",
    "collect_fee_mode": "INTEGER",
    "is_blacklisted": "INTEGER",
    "pool_created_at": "INTEGER",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _nested_mapping(pool: dict[str, Any], *keys: str) -> dict[str, Any]:
    value = _first(pool, *keys)
    return value if isinstance(value, dict) else {}


def _token_field(pool: dict[str, Any], token_keys: tuple[str, ...], field: str) -> Any:
    token = _nested_mapping(pool, *token_keys)
    return token.get(field)


def _time_window_metric(pool: dict[str, Any], key: str, window: str = "24h") -> Any:
    value = pool.get(key)
    if isinstance(value, dict):
        return value.get(window)
    return None


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")


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
            _ensure_columns(conn, "pool_snapshots", POOL_SNAPSHOT_EXTRA_COLUMNS)
            _ensure_columns(conn, "volume_buckets", VOLUME_BUCKET_EXTRA_COLUMNS)

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
        config = _nested_mapping(pool, "pool_config", "poolConfig")

        name = _first(pool, "name", "pool_name", "pair_name")
        tvl = _number(_first(pool, "tvl", "liquidity", "total_liquidity"))

        volume_24h = _number(_first(pool, "volume_24h", "trade_volume_24h", "volume24h"))
        if volume_24h is None:
            volume_24h = _number(_time_window_metric(pool, "volume"))

        fees_24h = _number(_first(pool, "fees_24h", "fee_24h", "fees24h"))
        if fees_24h is None:
            fees_24h = _number(_time_window_metric(pool, "fees"))

        current_price = _number(_first(pool, "current_price", "currentPrice", "price"))

        bin_step = _integer(_first(pool, "bin_step", "binStep"))
        if bin_step is None:
            bin_step = _integer(_first(config, "bin_step", "binStep"))

        token_x_symbol = _token_field(pool, ("token_x", "tokenX"), "symbol")
        token_y_symbol = _token_field(pool, ("token_y", "tokenY"), "symbol")
        token_x_decimals = _integer(_token_field(pool, ("token_x", "tokenX"), "decimals"))
        token_y_decimals = _integer(_token_field(pool, ("token_y", "tokenY"), "decimals"))

        active_bin_id = _integer(_first(pool, "active_bin_id", "active_id", "activeId"))
        if (
            active_bin_id is None
            and current_price is not None
            and bin_step is not None
            and token_x_decimals is not None
            and token_y_decimals is not None
        ):
            try:
                active_bin_id = price_to_bin_id(
                    current_price,
                    bin_step,
                    round_down=True,
                    token_x_decimals=token_x_decimals,
                    token_y_decimals=token_y_decimals,
                )
            except ValueError:
                active_bin_id = None

        apr = _number(_first(pool, "apr", "apr_24h"))
        apy = _number(_first(pool, "apy", "apy_24h"))
        dynamic_fee_pct = _number(_first(pool, "dynamic_fee_pct", "dynamicFeePct"))
        base_fee_pct = _number(_first(config, "base_fee_pct", "baseFeePct"))
        max_fee_pct = _number(_first(config, "max_fee_pct", "maxFeePct"))
        protocol_fee_pct = _number(_first(config, "protocol_fee_pct", "protocolFeePct"))
        collect_fee_mode = _integer(_first(config, "collect_fee_mode", "collectFeeMode"))
        is_blacklisted_raw = _first(pool, "is_blacklisted", "isBlacklisted")
        is_blacklisted = int(bool(is_blacklisted_raw)) if is_blacklisted_raw is not None else None
        pool_created_at = _integer(_first(pool, "created_at", "createdAt"))

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pool_snapshots(
                    observed_at, address, name, tvl, volume_24h, fees_24h,
                    current_price, bin_step, active_bin_id, apr, apy,
                    token_x_symbol, token_y_symbol, token_x_decimals, token_y_decimals,
                    dynamic_fee_pct, base_fee_pct, max_fee_pct, protocol_fee_pct,
                    collect_fee_mode, is_blacklisted, pool_created_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at,
                    str(address),
                    str(name) if name is not None else None,
                    tvl,
                    volume_24h,
                    fees_24h,
                    current_price,
                    bin_step,
                    active_bin_id,
                    apr,
                    apy,
                    str(token_x_symbol) if token_x_symbol is not None else None,
                    str(token_y_symbol) if token_y_symbol is not None else None,
                    token_x_decimals,
                    token_y_decimals,
                    dynamic_fee_pct,
                    base_fee_pct,
                    max_fee_pct,
                    protocol_fee_pct,
                    collect_fee_mode,
                    is_blacklisted,
                    pool_created_at,
                    json.dumps(pool, separators=(",", ":")),
                ),
            )
        return True

    def save_ohlcv_candles(self, candles: list[dict[str, Any]]) -> int:
        if not candles:
            return 0
        rows = [
            (
                item["pool_address"],
                item["source"],
                item["candle_time"],
                item.get("resolution") or "",
                item.get("open"),
                item.get("high"),
                item.get("low"),
                item.get("close"),
                item.get("volume"),
                item["observed_at"],
                json.dumps(item.get("raw", {}), separators=(",", ":")),
            )
            for item in candles
        ]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO ohlcv_candles(
                    pool_address, source, candle_time, resolution,
                    open, high, low, close, volume, observed_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pool_address, source, candle_time, resolution) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    observed_at=excluded.observed_at,
                    raw_json=excluded.raw_json
                """,
                rows,
            )
        return len(rows)

    def save_volume_buckets(self, buckets: list[dict[str, Any]]) -> int:
        if not buckets:
            return 0
        rows = [
            (
                item["pool_address"],
                item["source"],
                item["bucket_time"],
                item.get("volume"),
                item.get("fees"),
                item.get("protocol_fees"),
                item["observed_at"],
                json.dumps(item.get("raw", {}), separators=(",", ":")),
            )
            for item in buckets
        ]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO volume_buckets(
                    pool_address, source, bucket_time, volume, fees, protocol_fees, observed_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pool_address, source, bucket_time) DO UPDATE SET
                    volume=excluded.volume,
                    fees=excluded.fees,
                    protocol_fees=excluded.protocol_fees,
                    observed_at=excluded.observed_at,
                    raw_json=excluded.raw_json
                """,
                rows,
            )
        return len(rows)

    def save_chain_pool_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        observed_at: str | None = None,
    ) -> tuple[int, int]:
        observed_at = observed_at or utc_now_iso()
        pool_address = str(snapshot["pool_address"])
        active_bin_id = int(snapshot["active_bin_id"])
        bin_step = int(snapshot["bin_step"])
        token_x_mint = str(snapshot["token_x_mint"])
        token_y_mint = str(snapshot["token_y_mint"])
        bin_arrays = snapshot.get("bin_arrays")
        if not isinstance(bin_arrays, list):
            raise ValueError("bin_arrays must be a list")

        bin_rows: list[tuple[Any, ...]] = []
        arrays_seen = 0
        for array in bin_arrays:
            if not isinstance(array, dict):
                raise ValueError("each bin array must be an object")
            index = int(array["index"])
            bins = array.get("bins")
            if not isinstance(bins, list):
                raise ValueError("bin array bins must be a list")
            arrays_seen += 1
            for bin_row in bins:
                if not isinstance(bin_row, dict):
                    raise ValueError("each bin must be an object")
                bin_rows.append(
                    (
                        observed_at,
                        pool_address,
                        index,
                        int(bin_row["bin_id"]),
                        str(bin_row["amount_x"]),
                        str(bin_row["amount_y"]),
                        str(bin_row["liquidity_supply"]),
                        str(bin_row["fee_amount_x_per_token_stored"]),
                        str(bin_row["fee_amount_y_per_token_stored"]),
                    )
                )

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chain_pool_snapshots(
                    observed_at, pool_address, active_bin_id, bin_step,
                    token_x_mint, token_y_mint, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at,
                    pool_address,
                    active_bin_id,
                    bin_step,
                    token_x_mint,
                    token_y_mint,
                    json.dumps(snapshot, separators=(",", ":")),
                ),
            )
            if bin_rows:
                conn.executemany(
                    """
                    INSERT INTO bin_liquidity_snapshots(
                        observed_at, pool_address, bin_array_index, bin_id,
                        amount_x, amount_y, liquidity_supply,
                        fee_amount_x_per_token_stored, fee_amount_y_per_token_stored
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    bin_rows,
                )

        return arrays_seen, len(bin_rows)

    def save_quality_checks(
        self,
        entity_type: str,
        entity_key: str,
        checks: list[dict[str, Any]],
        *,
        checked_at: str | None = None,
    ) -> int:
        if not checks:
            return 0
        checked_at = checked_at or utc_now_iso()
        rows = [
            (
                checked_at,
                entity_type,
                entity_key,
                item["check_name"],
                item["status"],
                item.get("value"),
                item.get("detail"),
            )
            for item in checks
        ]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO data_quality_checks(
                    checked_at, entity_type, entity_key, check_name, status, value, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def save_collection_error(
        self,
        run_id: str,
        endpoint: str,
        error: Exception | str,
        *,
        entity_key: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO collection_errors(run_id, occurred_at, endpoint, entity_key, error)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, utc_now_iso(), endpoint, entity_key, str(error)[:2000]),
            )

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

    def data_status(self) -> dict[str, Any]:
        with self.connect() as conn:
            candles = conn.execute(
                "SELECT COUNT(*), MAX(candle_time), COUNT(DISTINCT pool_address) FROM ohlcv_candles"
            ).fetchone()
            volume = conn.execute(
                "SELECT COUNT(*), MAX(bucket_time), COUNT(DISTINCT pool_address) FROM volume_buckets"
            ).fetchone()
            pools = conn.execute(
                "SELECT COUNT(*), MAX(observed_at), COUNT(DISTINCT address) FROM pool_snapshots"
            ).fetchone()
            chain = conn.execute(
                "SELECT COUNT(*), MAX(observed_at), COUNT(DISTINCT pool_address) FROM chain_pool_snapshots"
            ).fetchone()
            bins = conn.execute(
                "SELECT COUNT(*) FROM bin_liquidity_snapshots"
            ).fetchone()[0]
            failures = conn.execute(
                "SELECT COUNT(*) FROM data_quality_checks WHERE status = 'FAIL'"
            ).fetchone()[0]
            errors = conn.execute("SELECT COUNT(*) FROM collection_errors").fetchone()[0]

        return {
            "pool_snapshots": pools[0],
            "pool_count": pools[2],
            "latest_pool_snapshot": pools[1],
            "ohlcv_candles": candles[0],
            "ohlcv_pool_count": candles[2],
            "latest_candle": candles[1],
            "volume_buckets": volume[0],
            "volume_pool_count": volume[2],
            "latest_volume_bucket": volume[1],
            "chain_pool_snapshots": chain[0],
            "chain_pool_count": chain[2],
            "latest_chain_snapshot": chain[1],
            "bin_liquidity_snapshots": bins,
            "quality_failures": failures,
            "collection_errors": errors,
        }
