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
    token_x_program TEXT,
    token_y_program TEXT,
    base_fee_rate TEXT,
    variable_fee_rate TEXT,
    total_fee_rate TEXT,
    deposit_total_fee_rate TEXT,
    protocol_share_bps INTEGER,
    collect_fee_mode INTEGER,
    supports_limit_order INTEGER,
    reward_mint_0 TEXT,
    reward_mint_1 TEXT,
    reward_rate_0 TEXT,
    reward_rate_1 TEXT,
    reward_duration_end_0 INTEGER,
    reward_duration_end_1 INTEGER,
    reward_last_update_time_0 INTEGER,
    reward_last_update_time_1 INTEGER,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chain_pool_time
ON chain_pool_snapshots(pool_address, observed_at);

CREATE TABLE IF NOT EXISTS chain_pool_capture_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    capture_slot_start INTEGER,
    capture_slot_end INTEGER,
    clock_unix_timestamp INTEGER,
    fee_base_factor INTEGER,
    fee_filter_period INTEGER,
    fee_decay_period INTEGER,
    fee_reduction_factor INTEGER,
    fee_variable_fee_control INTEGER,
    fee_max_volatility_accumulator INTEGER,
    fee_base_fee_power_factor INTEGER,
    fee_volatility_accumulator INTEGER,
    fee_volatility_reference INTEGER,
    fee_index_reference INTEGER,
    fee_last_update_timestamp INTEGER,
    raw_json TEXT NOT NULL,
    UNIQUE(pool_address, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_chain_pool_capture_slot
ON chain_pool_capture_state(pool_address, capture_slot_end);

CREATE TABLE IF NOT EXISTS bin_liquidity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    bin_array_index INTEGER NOT NULL,
    bin_array_address TEXT,
    bin_id INTEGER NOT NULL,
    price TEXT NOT NULL DEFAULT '0',
    amount_x TEXT NOT NULL,
    amount_y TEXT NOT NULL,
    liquidity_supply TEXT NOT NULL,
    fee_amount_x_per_token_stored TEXT NOT NULL,
    fee_amount_y_per_token_stored TEXT NOT NULL,
    reward_per_token_stored_0 TEXT NOT NULL DEFAULT '0',
    reward_per_token_stored_1 TEXT NOT NULL DEFAULT '0'
);

CREATE INDEX IF NOT EXISTS idx_bin_liquidity_pool_time
ON bin_liquidity_snapshots(pool_address, observed_at, bin_id);

CREATE TABLE IF NOT EXISTS chain_position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    position_address TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    owner TEXT NOT NULL,
    fee_owner TEXT NOT NULL,
    lower_bin_id INTEGER NOT NULL,
    upper_bin_id INTEGER NOT NULL,
    total_x_amount TEXT NOT NULL,
    total_y_amount TEXT NOT NULL,
    fee_x TEXT NOT NULL,
    fee_y TEXT NOT NULL,
    reward_one TEXT NOT NULL,
    reward_two TEXT NOT NULL,
    last_updated_at INTEGER NOT NULL,
    total_claimed_fee_x_amount TEXT NOT NULL,
    total_claimed_fee_y_amount TEXT NOT NULL,
    supports_limit_order INTEGER,
    reward_mint_0 TEXT,
    reward_mint_1 TEXT,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chain_position_time
ON chain_position_snapshots(position_address, observed_at);

CREATE TABLE IF NOT EXISTS position_bin_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    position_address TEXT NOT NULL,
    bin_id INTEGER NOT NULL,
    price TEXT NOT NULL,
    bin_x_amount TEXT NOT NULL,
    bin_y_amount TEXT NOT NULL,
    bin_liquidity TEXT NOT NULL,
    bin_fee_x_per_token_stored TEXT NOT NULL DEFAULT '0',
    bin_fee_y_per_token_stored TEXT NOT NULL DEFAULT '0',
    bin_reward_per_token_stored_0 TEXT NOT NULL DEFAULT '0',
    bin_reward_per_token_stored_1 TEXT NOT NULL DEFAULT '0',
    reward_checkpoint_available INTEGER NOT NULL DEFAULT 0,
    position_liquidity TEXT NOT NULL,
    position_x_amount TEXT NOT NULL,
    position_y_amount TEXT NOT NULL,
    position_fee_x_amount TEXT NOT NULL,
    position_fee_y_amount TEXT NOT NULL,
    reward_one TEXT NOT NULL,
    reward_two TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_position_bin_time
ON position_bin_snapshots(position_address, observed_at, bin_id);

CREATE TABLE IF NOT EXISTS position_event_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    position_address TEXT NOT NULL,
    signature TEXT NOT NULL,
    ix_index INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    block_time INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    pool_address TEXT NOT NULL,
    user_address TEXT NOT NULL,
    token_x TEXT NOT NULL,
    token_y TEXT NOT NULL,
    amount_x TEXT NOT NULL,
    amount_y TEXT NOT NULL,
    amount_x_usd TEXT NOT NULL,
    amount_y_usd TEXT NOT NULL,
    total_usd TEXT NOT NULL,
    created_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(position_address, signature, ix_index, event_type)
);

CREATE INDEX IF NOT EXISTS idx_position_event_history_position_time
ON position_event_history(position_address, block_time, ix_index);

CREATE INDEX IF NOT EXISTS idx_position_event_history_signature
ON position_event_history(signature, ix_index);

CREATE TABLE IF NOT EXISTS chain_transaction_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    signature TEXT NOT NULL UNIQUE,
    slot INTEGER NOT NULL,
    block_time INTEGER,
    network_fee_lamports INTEGER,
    compute_units_consumed INTEGER,
    succeeded INTEGER,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chain_tx_snapshot_time
ON chain_transaction_snapshots(block_time, slot);

CREATE TABLE IF NOT EXISTS chain_add_liquidity_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    signature TEXT NOT NULL,
    instruction_index INTEGER NOT NULL,
    instruction_type TEXT NOT NULL,
    requested_amount_x TEXT NOT NULL,
    requested_amount_y TEXT NOT NULL,
    observed_active_id INTEGER,
    max_active_bin_slippage INTEGER,
    min_bin_id INTEGER,
    max_bin_id INTEGER,
    strategy_variant INTEGER,
    strategy_favor_x INTEGER,
    explicit_distribution_json TEXT,
    weighted_distribution_json TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(signature, instruction_index)
);

CREATE INDEX IF NOT EXISTS idx_chain_add_request_signature
ON chain_add_liquidity_requests(signature, instruction_index);

CREATE TABLE IF NOT EXISTS chain_rebalance_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    signature TEXT NOT NULL,
    instruction_index INTEGER NOT NULL,
    observed_active_id INTEGER NOT NULL,
    max_active_bin_slippage INTEGER NOT NULL,
    should_claim_fee INTEGER NOT NULL,
    should_claim_reward INTEGER NOT NULL,
    min_withdraw_x_amount TEXT NOT NULL,
    max_deposit_x_amount TEXT NOT NULL,
    min_withdraw_y_amount TEXT NOT NULL,
    max_deposit_y_amount TEXT NOT NULL,
    shrink_mode INTEGER NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(signature, instruction_index)
);

CREATE INDEX IF NOT EXISTS idx_chain_rebalance_request_signature
ON chain_rebalance_requests(signature, instruction_index);

CREATE TABLE IF NOT EXISTS composition_prestate_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    signature TEXT NOT NULL,
    snapshot_observed_at TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    transaction_slot INTEGER NOT NULL,
    capture_slot_start INTEGER NOT NULL,
    capture_slot_end INTEGER NOT NULL,
    eligible INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    account_checks_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(signature, snapshot_observed_at)
);

CREATE INDEX IF NOT EXISTS idx_composition_prestate_signature
ON composition_prestate_verifications(signature, snapshot_observed_at);

CREATE TABLE IF NOT EXISTS chain_transaction_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    signature TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    parent_ix_index INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    block_time INTEGER,
    event_type TEXT NOT NULL,
    lb_pair TEXT,
    from_address TEXT,
    position_address TEXT,
    active_bin_id INTEGER,
    bin_id INTEGER,
    amount_x TEXT,
    amount_y TEXT,
    token_x_fee_amount TEXT,
    token_y_fee_amount TEXT,
    protocol_token_x_fee_amount TEXT,
    protocol_token_y_fee_amount TEXT,
    owner_address TEXT,
    x_withdrawn_amount TEXT,
    x_added_amount TEXT,
    y_withdrawn_amount TEXT,
    y_added_amount TEXT,
    x_fee_amount TEXT,
    y_fee_amount TEXT,
    old_min_id INTEGER,
    old_max_id INTEGER,
    new_min_id INTEGER,
    new_max_id INTEGER,
    reward_one TEXT,
    reward_two TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(signature, event_index)
);

CREATE INDEX IF NOT EXISTS idx_chain_tx_event_signature_parent
ON chain_transaction_events(signature, parent_ix_index);

CREATE INDEX IF NOT EXISTS idx_chain_tx_event_position
ON chain_transaction_events(position_address, signature);

CREATE TABLE IF NOT EXISTS paper_accounts (
    account_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    starting_equity_quote TEXT NOT NULL,
    cash_quote TEXT NOT NULL,
    high_water_equity_quote TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    position_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('OPEN', 'CLOSED')),
    policy_source TEXT NOT NULL,
    model_id TEXT,
    strategy TEXT NOT NULL,
    min_bin_id INTEGER NOT NULL,
    max_bin_id INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    entry_capital_quote TEXT NOT NULL,
    entry_cost_quote TEXT NOT NULL,
    current_mark_quote TEXT NOT NULL,
    fee_income_quote TEXT NOT NULL,
    reward_income_quote TEXT NOT NULL,
    rebalance_cost_quote TEXT NOT NULL,
    exit_cost_quote TEXT NOT NULL,
    realized_pnl_quote TEXT,
    rebalances INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_account_status
ON paper_positions(account_id, status);

CREATE TABLE IF NOT EXISTS paper_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    position_id TEXT,
    event_time TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(
        event_type IN ('ENTER', 'MARK', 'REBALANCE', 'EXIT')
    ),
    cash_delta_quote TEXT NOT NULL,
    position_mark_quote TEXT,
    fee_delta_quote TEXT NOT NULL DEFAULT '0',
    reward_delta_quote TEXT NOT NULL DEFAULT '0',
    cost_quote TEXT NOT NULL DEFAULT '0',
    realized_pnl_quote TEXT,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_events_account_time
ON paper_events(account_id, event_time, id);

CREATE TABLE IF NOT EXISTS paper_counterfactual_positions (
    position_id TEXT PRIMARY KEY,
    pool_address TEXT NOT NULL,
    entry_observed_at TEXT NOT NULL,
    amount_x_atomic TEXT NOT NULL,
    amount_y_atomic TEXT NOT NULL,
    idle_x_atomic TEXT NOT NULL,
    idle_y_atomic TEXT NOT NULL,
    entry_price_q64 TEXT NOT NULL,
    entry_value_y_atomic TEXT NOT NULL,
    capital_quote TEXT NOT NULL,
    max_share_bps INTEGER NOT NULL,
    favor_x_active INTEGER NOT NULL,
    token_x_mint TEXT NOT NULL,
    token_y_mint TEXT NOT NULL,
    reward_mint_0 TEXT,
    reward_mint_1 TEXT,
    initial_state_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_chain_valuations (
    position_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    event_key_prefix TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('PREPARED', 'APPLIED')),
    active_bin_id INTEGER NOT NULL,
    mark_quote TEXT NOT NULL,
    fee_delta_quote TEXT NOT NULL,
    reward_delta_quote TEXT NOT NULL,
    inventory_x_atomic TEXT NOT NULL,
    inventory_y_atomic TEXT NOT NULL,
    fee_x_atomic TEXT NOT NULL,
    fee_y_atomic TEXT NOT NULL,
    reward_one_atomic TEXT NOT NULL,
    reward_two_atomic TEXT NOT NULL,
    next_state_json TEXT NOT NULL,
    valuation_json TEXT NOT NULL,
    PRIMARY KEY(position_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_paper_chain_valuations_status
ON paper_chain_valuations(position_id, status, observed_at);

CREATE TABLE IF NOT EXISTS paper_runs (
    run_id TEXT PRIMARY KEY,
    observed_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('RUNNING', 'COMPLETE', 'FAILED')),
    items_total INTEGER NOT NULL DEFAULT 0,
    items_applied INTEGER NOT NULL DEFAULT 0,
    items_skipped INTEGER NOT NULL DEFAULT 0,
    items_failed INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS paper_run_items (
    run_id TEXT NOT NULL,
    position_id TEXT NOT NULL,
    event_key_prefix TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'APPLIED', 'SKIPPED', 'FAILED')),
    input_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    PRIMARY KEY(run_id, position_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_run_items_status
ON paper_run_items(run_id, status);

CREATE TABLE IF NOT EXISTS model_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    model_family TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN (
            'OFFLINE_CANDIDATE',
            'OFFLINE_QUALIFIED',
            'PAPER_CHALLENGER',
            'CHAMPION',
            'REJECTED',
            'ROLLED_BACK'
        )
    ),
    artifact_uri TEXT,
    train_start TEXT,
    train_end TEXT,
    validation_start TEXT,
    validation_end TEXT,
    train_rows INTEGER,
    validation_rows INTEGER,
    metrics_json TEXT NOT NULL,
    notes TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_registry_one_champion
ON model_registry(status)
WHERE status = 'CHAMPION';

CREATE TABLE IF NOT EXISTS phase_promotion_evidence (
    phase_name TEXT PRIMARY KEY,
    promoted_at TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    qualified INTEGER NOT NULL,
    evidence_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_offline_evidence (
    model_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    qualified INTEGER NOT NULL,
    evidence_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_promotion_evidence (
    model_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    qualified INTEGER NOT NULL,
    evidence_json TEXT NOT NULL
);

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

BIN_LIQUIDITY_EXTRA_COLUMNS = {
    "price": "TEXT NOT NULL DEFAULT '0'",
    "bin_array_address": "TEXT",
    "reward_per_token_stored_0": "TEXT NOT NULL DEFAULT '0'",
    "reward_per_token_stored_1": "TEXT NOT NULL DEFAULT '0'",
}

POSITION_BIN_EXTRA_COLUMNS = {
    "bin_fee_x_per_token_stored": "TEXT NOT NULL DEFAULT '0'",
    "bin_fee_y_per_token_stored": "TEXT NOT NULL DEFAULT '0'",
    "bin_reward_per_token_stored_0": "TEXT NOT NULL DEFAULT '0'",
    "bin_reward_per_token_stored_1": "TEXT NOT NULL DEFAULT '0'",
    "reward_checkpoint_available": "INTEGER NOT NULL DEFAULT 0",
}

CHAIN_ADD_REQUEST_EXTRA_COLUMNS = {
    "min_bin_id": "INTEGER",
    "max_bin_id": "INTEGER",
    "strategy_variant": "INTEGER",
    "strategy_favor_x": "INTEGER",
    "explicit_distribution_json": "TEXT",
    "weighted_distribution_json": "TEXT",
}

CHAIN_TX_EVENT_EXTRA_COLUMNS = {
    "owner_address": "TEXT",
    "x_withdrawn_amount": "TEXT",
    "x_added_amount": "TEXT",
    "y_withdrawn_amount": "TEXT",
    "y_added_amount": "TEXT",
    "x_fee_amount": "TEXT",
    "y_fee_amount": "TEXT",
    "old_min_id": "INTEGER",
    "old_max_id": "INTEGER",
    "new_min_id": "INTEGER",
    "new_max_id": "INTEGER",
    "reward_one": "TEXT",
    "reward_two": "TEXT",
}

CHAIN_POSITION_EXTRA_COLUMNS = {
    "supports_limit_order": "INTEGER",
    "reward_mint_0": "TEXT",
    "reward_mint_1": "TEXT",
}

CHAIN_POOL_EXTRA_COLUMNS = {
    "token_x_program": "TEXT",
    "token_y_program": "TEXT",
    "base_fee_rate": "TEXT",
    "variable_fee_rate": "TEXT",
    "total_fee_rate": "TEXT",
    "deposit_total_fee_rate": "TEXT",
    "protocol_share_bps": "INTEGER",
    "collect_fee_mode": "INTEGER",
    "supports_limit_order": "INTEGER",
    "reward_mint_0": "TEXT",
    "reward_mint_1": "TEXT",
    "reward_rate_0": "TEXT",
    "reward_rate_1": "TEXT",
    "reward_duration_end_0": "INTEGER",
    "reward_duration_end_1": "INTEGER",
    "reward_last_update_time_0": "INTEGER",
    "reward_last_update_time_1": "INTEGER",
}

MODEL_STATUS_TRANSITIONS = {
    "OFFLINE_CANDIDATE": {"REJECTED"},
    "OFFLINE_QUALIFIED": {"REJECTED"},
    "PAPER_CHALLENGER": {"REJECTED"},
    "CHAMPION": {"ROLLED_BACK"},
    "REJECTED": set(),
    "ROLLED_BACK": set(),
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
            _ensure_columns(conn, "bin_liquidity_snapshots", BIN_LIQUIDITY_EXTRA_COLUMNS)
            _ensure_columns(conn, "chain_pool_snapshots", CHAIN_POOL_EXTRA_COLUMNS)
            _ensure_columns(conn, "position_bin_snapshots", POSITION_BIN_EXTRA_COLUMNS)
            _ensure_columns(conn, "chain_position_snapshots", CHAIN_POSITION_EXTRA_COLUMNS)
            _ensure_columns(conn, "chain_transaction_events", CHAIN_TX_EVENT_EXTRA_COLUMNS)
            _ensure_columns(conn, "chain_add_liquidity_requests", CHAIN_ADD_REQUEST_EXTRA_COLUMNS)

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
        token_x_program = snapshot.get("token_x_program")
        token_y_program = snapshot.get("token_y_program")
        base_fee_rate = snapshot.get("base_fee_rate")
        variable_fee_rate = snapshot.get("variable_fee_rate")
        total_fee_rate = snapshot.get("total_fee_rate")
        deposit_total_fee_rate = snapshot.get("deposit_total_fee_rate")
        protocol_share_bps = snapshot.get("protocol_share_bps")
        collect_fee_mode = snapshot.get("collect_fee_mode")
        supports_limit_order = snapshot.get("supports_limit_order")
        reward_mints = snapshot.get("reward_mints") or [None, None]
        reward_rates = snapshot.get("reward_rates") or [None, None]
        reward_duration_ends = snapshot.get("reward_duration_ends") or [None, None]
        reward_last_update_times = snapshot.get("reward_last_update_times") or [None, None]
        if not all(
            isinstance(values, list) and len(values) == 2
            for values in (
                reward_mints,
                reward_rates,
                reward_duration_ends,
                reward_last_update_times,
            )
        ):
            raise ValueError("reward metadata arrays must contain exactly two values")
        capture_slot_start = snapshot.get("capture_slot_start")
        capture_slot_end = snapshot.get("capture_slot_end")
        clock_unix_timestamp = snapshot.get("clock_unix_timestamp")
        fee_state = snapshot.get("fee_state")
        if fee_state is not None and not isinstance(fee_state, dict):
            raise ValueError("fee_state must be an object when supplied")
        bin_arrays = snapshot.get("bin_arrays")
        if not isinstance(bin_arrays, list):
            raise ValueError("bin_arrays must be a list")

        bin_rows: list[tuple[Any, ...]] = []
        arrays_seen = 0
        for array in bin_arrays:
            if not isinstance(array, dict):
                raise ValueError("each bin array must be an object")
            index = int(array["index"])
            array_address = str(array.get("address")) if array.get("address") is not None else None
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
                        array_address,
                        int(bin_row["bin_id"]),
                        str(bin_row["price"]),
                        str(bin_row["amount_x"]),
                        str(bin_row["amount_y"]),
                        str(bin_row["liquidity_supply"]),
                        str(bin_row["fee_amount_x_per_token_stored"]),
                        str(bin_row["fee_amount_y_per_token_stored"]),
                        str((bin_row.get("reward_per_token_stored") or ["0", "0"])[0]),
                        str((bin_row.get("reward_per_token_stored") or ["0", "0"])[1]),
                    )
                )

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chain_pool_snapshots(
                    observed_at, pool_address, active_bin_id, bin_step,
                    token_x_mint, token_y_mint, token_x_program, token_y_program,
                    base_fee_rate, variable_fee_rate, total_fee_rate,
                    deposit_total_fee_rate, protocol_share_bps, collect_fee_mode,
                    supports_limit_order, reward_mint_0, reward_mint_1,
                    reward_rate_0, reward_rate_1,
                    reward_duration_end_0, reward_duration_end_1,
                    reward_last_update_time_0, reward_last_update_time_1,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at,
                    pool_address,
                    active_bin_id,
                    bin_step,
                    token_x_mint,
                    token_y_mint,
                    str(token_x_program) if token_x_program is not None else None,
                    str(token_y_program) if token_y_program is not None else None,
                    str(base_fee_rate) if base_fee_rate is not None else None,
                    str(variable_fee_rate) if variable_fee_rate is not None else None,
                    str(total_fee_rate) if total_fee_rate is not None else None,
                    str(deposit_total_fee_rate) if deposit_total_fee_rate is not None else None,
                    int(protocol_share_bps) if protocol_share_bps is not None else None,
                    int(collect_fee_mode) if collect_fee_mode is not None else None,
                    int(bool(supports_limit_order)) if supports_limit_order is not None else None,
                    str(reward_mints[0]) if reward_mints[0] is not None else None,
                    str(reward_mints[1]) if reward_mints[1] is not None else None,
                    str(reward_rates[0]) if reward_rates[0] is not None else None,
                    str(reward_rates[1]) if reward_rates[1] is not None else None,
                    int(reward_duration_ends[0]) if reward_duration_ends[0] is not None else None,
                    int(reward_duration_ends[1]) if reward_duration_ends[1] is not None else None,
                    int(reward_last_update_times[0]) if reward_last_update_times[0] is not None else None,
                    int(reward_last_update_times[1]) if reward_last_update_times[1] is not None else None,
                    json.dumps(snapshot, separators=(",", ":")),
                ),
            )
            if (
                capture_slot_start is not None
                or capture_slot_end is not None
                or clock_unix_timestamp is not None
                or fee_state is not None
            ):
                fee_state = fee_state or {}
                conn.execute(
                    """
                    INSERT INTO chain_pool_capture_state(
                        observed_at, pool_address,
                        capture_slot_start, capture_slot_end, clock_unix_timestamp,
                        fee_base_factor, fee_filter_period, fee_decay_period,
                        fee_reduction_factor, fee_variable_fee_control,
                        fee_max_volatility_accumulator, fee_base_fee_power_factor,
                        fee_volatility_accumulator, fee_volatility_reference,
                        fee_index_reference, fee_last_update_timestamp, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pool_address, observed_at) DO UPDATE SET
                        capture_slot_start=excluded.capture_slot_start,
                        capture_slot_end=excluded.capture_slot_end,
                        clock_unix_timestamp=excluded.clock_unix_timestamp,
                        fee_base_factor=excluded.fee_base_factor,
                        fee_filter_period=excluded.fee_filter_period,
                        fee_decay_period=excluded.fee_decay_period,
                        fee_reduction_factor=excluded.fee_reduction_factor,
                        fee_variable_fee_control=excluded.fee_variable_fee_control,
                        fee_max_volatility_accumulator=excluded.fee_max_volatility_accumulator,
                        fee_base_fee_power_factor=excluded.fee_base_fee_power_factor,
                        fee_volatility_accumulator=excluded.fee_volatility_accumulator,
                        fee_volatility_reference=excluded.fee_volatility_reference,
                        fee_index_reference=excluded.fee_index_reference,
                        fee_last_update_timestamp=excluded.fee_last_update_timestamp,
                        raw_json=excluded.raw_json
                    """,
                    (
                        observed_at,
                        pool_address,
                        int(capture_slot_start) if capture_slot_start is not None else None,
                        int(capture_slot_end) if capture_slot_end is not None else None,
                        int(clock_unix_timestamp) if clock_unix_timestamp is not None else None,
                        int(fee_state["base_factor"]) if fee_state.get("base_factor") is not None else None,
                        int(fee_state["filter_period"]) if fee_state.get("filter_period") is not None else None,
                        int(fee_state["decay_period"]) if fee_state.get("decay_period") is not None else None,
                        int(fee_state["reduction_factor"]) if fee_state.get("reduction_factor") is not None else None,
                        int(fee_state["variable_fee_control"]) if fee_state.get("variable_fee_control") is not None else None,
                        int(fee_state["max_volatility_accumulator"]) if fee_state.get("max_volatility_accumulator") is not None else None,
                        int(fee_state["base_fee_power_factor"]) if fee_state.get("base_fee_power_factor") is not None else None,
                        int(fee_state["volatility_accumulator"]) if fee_state.get("volatility_accumulator") is not None else None,
                        int(fee_state["volatility_reference"]) if fee_state.get("volatility_reference") is not None else None,
                        int(fee_state["index_reference"]) if fee_state.get("index_reference") is not None else None,
                        int(fee_state["last_update_timestamp"]) if fee_state.get("last_update_timestamp") is not None else None,
                        json.dumps(
                            {
                                "capture_slot_start": capture_slot_start,
                                "capture_slot_end": capture_slot_end,
                                "clock_unix_timestamp": clock_unix_timestamp,
                                "fee_state": fee_state,
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
            if bin_rows:
                conn.executemany(
                    """
                    INSERT INTO bin_liquidity_snapshots(
                        observed_at, pool_address, bin_array_index, bin_array_address,
                        bin_id, price, amount_x, amount_y, liquidity_supply,
                        fee_amount_x_per_token_stored, fee_amount_y_per_token_stored,
                        reward_per_token_stored_0, reward_per_token_stored_1
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    bin_rows,
                )

        return arrays_seen, len(bin_rows)

    def save_chain_position_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        observed_at: str | None = None,
    ) -> int:
        observed_at = observed_at or utc_now_iso()
        position_address = str(snapshot["position_address"])
        bins = snapshot.get("bins")
        if not isinstance(bins, list):
            raise ValueError("position bins must be a list")

        bin_rows: list[tuple[Any, ...]] = []
        for row in bins:
            if not isinstance(row, dict):
                raise ValueError("each position bin must be an object")
            rewards = row.get("position_reward_amounts")
            if not isinstance(rewards, list) or len(rewards) != 2:
                raise ValueError("position_reward_amounts must contain two values")
            reward_checkpoints = row.get("bin_reward_per_token_stored")
            reward_checkpoint_available = (
                isinstance(reward_checkpoints, list) and len(reward_checkpoints) == 2
            )
            if reward_checkpoint_available:
                reward_checkpoint_values = reward_checkpoints
            else:
                reward_checkpoint_values = ["0", "0"]
            bin_rows.append(
                (
                    observed_at,
                    position_address,
                    int(row["bin_id"]),
                    str(row["price"]),
                    str(row["bin_x_amount"]),
                    str(row["bin_y_amount"]),
                    str(row["bin_liquidity"]),
                    str(row.get("bin_fee_x_per_token_stored", "0")),
                    str(row.get("bin_fee_y_per_token_stored", "0")),
                    str(reward_checkpoint_values[0]),
                    str(reward_checkpoint_values[1]),
                    int(reward_checkpoint_available),
                    str(row["position_liquidity"]),
                    str(row["position_x_amount"]),
                    str(row["position_y_amount"]),
                    str(row["position_fee_x_amount"]),
                    str(row["position_fee_y_amount"]),
                    str(rewards[0]),
                    str(rewards[1]),
                )
            )

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chain_position_snapshots(
                    observed_at, position_address, pool_address, owner, fee_owner,
                    lower_bin_id, upper_bin_id, total_x_amount, total_y_amount,
                    fee_x, fee_y, reward_one, reward_two, last_updated_at,
                    total_claimed_fee_x_amount, total_claimed_fee_y_amount,
                    supports_limit_order, reward_mint_0, reward_mint_1, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at,
                    position_address,
                    str(snapshot["pool_address"]),
                    str(snapshot["owner"]),
                    str(snapshot["fee_owner"]),
                    int(snapshot["lower_bin_id"]),
                    int(snapshot["upper_bin_id"]),
                    str(snapshot["total_x_amount"]),
                    str(snapshot["total_y_amount"]),
                    str(snapshot["fee_x"]),
                    str(snapshot["fee_y"]),
                    str(snapshot["reward_one"]),
                    str(snapshot["reward_two"]),
                    int(snapshot["last_updated_at"]),
                    str(snapshot["total_claimed_fee_x_amount"]),
                    str(snapshot["total_claimed_fee_y_amount"]),
                    (
                        int(bool(snapshot["supports_limit_order"]))
                        if snapshot.get("supports_limit_order") is not None
                        else None
                    ),
                    (
                        str(snapshot["reward_mints"][0])
                        if isinstance(snapshot.get("reward_mints"), list)
                        and len(snapshot["reward_mints"]) == 2
                        else None
                    ),
                    (
                        str(snapshot["reward_mints"][1])
                        if isinstance(snapshot.get("reward_mints"), list)
                        and len(snapshot["reward_mints"]) == 2
                        else None
                    ),
                    json.dumps(snapshot, separators=(",", ":")),
                ),
            )
            if bin_rows:
                conn.executemany(
                    """
                    INSERT INTO position_bin_snapshots(
                        observed_at, position_address, bin_id, price,
                        bin_x_amount, bin_y_amount, bin_liquidity,
                        bin_fee_x_per_token_stored, bin_fee_y_per_token_stored,
                        bin_reward_per_token_stored_0, bin_reward_per_token_stored_1,
                        reward_checkpoint_available,
                        position_liquidity, position_x_amount, position_y_amount,
                        position_fee_x_amount, position_fee_y_amount,
                        reward_one, reward_two
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    bin_rows,
                )
        return len(bin_rows)

    def save_position_events(self, events: list[dict[str, Any]]) -> int:
        if not events:
            return 0
        rows = [
            (
                item["observed_at"],
                item["position_address"],
                item["signature"],
                int(item["ix_index"]),
                item["event_type"],
                int(item["block_time"]),
                int(item["slot"]),
                item["pool_address"],
                item["user_address"],
                item["token_x"],
                item["token_y"],
                str(item["amount_x"]),
                str(item["amount_y"]),
                str(item["amount_x_usd"]),
                str(item["amount_y_usd"]),
                str(item["total_usd"]),
                item["created_at"],
                json.dumps(item.get("raw", {}), separators=(",", ":")),
            )
            for item in events
        ]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO position_event_history(
                    observed_at, position_address, signature, ix_index, event_type,
                    block_time, slot, pool_address, user_address, token_x, token_y,
                    amount_x, amount_y, amount_x_usd, amount_y_usd, total_usd,
                    created_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_address, signature, ix_index, event_type) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    block_time=excluded.block_time,
                    slot=excluded.slot,
                    pool_address=excluded.pool_address,
                    user_address=excluded.user_address,
                    token_x=excluded.token_x,
                    token_y=excluded.token_y,
                    amount_x=excluded.amount_x,
                    amount_y=excluded.amount_y,
                    amount_x_usd=excluded.amount_x_usd,
                    amount_y_usd=excluded.amount_y_usd,
                    total_usd=excluded.total_usd,
                    created_at=excluded.created_at,
                    raw_json=excluded.raw_json
                """,
                rows,
            )
        return len(rows)

    def save_chain_transaction_events(
        self,
        snapshot: dict[str, Any],
        *,
        observed_at: str | None = None,
    ) -> int:
        observed_at = observed_at or utc_now_iso()
        signature = str(snapshot["signature"])
        slot = int(snapshot["slot"])
        block_time_raw = snapshot.get("block_time")
        block_time = int(block_time_raw) if block_time_raw is not None else None
        network_fee_raw = snapshot.get("network_fee_lamports")
        compute_units_raw = snapshot.get("compute_units_consumed")
        succeeded_raw = snapshot.get("succeeded")
        add_requests = snapshot.get("add_requests") or []
        if not isinstance(add_requests, list):
            raise ValueError("add_requests must be a list")
        rebalance_requests = snapshot.get("rebalance_requests") or []
        if not isinstance(rebalance_requests, list):
            raise ValueError("rebalance_requests must be a list")
        events = snapshot.get("events")
        if not isinstance(events, list):
            raise ValueError("transaction events must be a list")

        rows: list[tuple[Any, ...]] = []
        for item in events:
            if not isinstance(item, dict):
                raise ValueError("transaction event entry must be an object")
            event_index = int(item["event_index"])
            parent_ix_index = int(item["parent_ix_index"])
            event = item.get("event")
            if not isinstance(event, dict):
                raise ValueError("transaction event payload must be an object")
            event_type = str(event["event_type"])
            payload = event.get("event")
            if not isinstance(payload, dict):
                raise ValueError("decoded transaction event body must be an object")

            common = {
                "lb_pair": None,
                "from_address": payload.get("from"),
                "position_address": None,
                "active_bin_id": None,
                "bin_id": None,
                "amount_x": None,
                "amount_y": None,
                "token_x_fee_amount": None,
                "token_y_fee_amount": None,
                "protocol_token_x_fee_amount": None,
                "protocol_token_y_fee_amount": None,
                "owner_address": None,
                "x_withdrawn_amount": None,
                "x_added_amount": None,
                "y_withdrawn_amount": None,
                "y_added_amount": None,
                "x_fee_amount": None,
                "y_fee_amount": None,
                "old_min_id": None,
                "old_max_id": None,
                "new_min_id": None,
                "new_max_id": None,
                "reward_one": None,
                "reward_two": None,
            }

            if event_type == "AddLiquidity":
                common.update(
                    {
                        "lb_pair": payload.get("lb_pair"),
                        "position_address": payload.get("position"),
                        "active_bin_id": int(payload["active_bin_id"]),
                        "amount_x": str(payload["amount_x"]),
                        "amount_y": str(payload["amount_y"]),
                    }
                )
            elif event_type == "CompositionFee":
                common.update(
                    {
                        "bin_id": int(payload["bin_id"]),
                        "token_x_fee_amount": str(payload["token_x_fee_amount"]),
                        "token_y_fee_amount": str(payload["token_y_fee_amount"]),
                        "protocol_token_x_fee_amount": str(
                            payload["protocol_token_x_fee_amount"]
                        ),
                        "protocol_token_y_fee_amount": str(
                            payload["protocol_token_y_fee_amount"]
                        ),
                    }
                )
            elif event_type == "RemoveLiquidity":
                common.update(
                    {
                        "lb_pair": payload.get("lb_pair"),
                        "position_address": payload.get("position"),
                        "active_bin_id": int(payload["active_bin_id"]),
                        "amount_x": str(payload["amount_x"]),
                        "amount_y": str(payload["amount_y"]),
                    }
                )
            elif event_type == "Rebalancing":
                common.update(
                    {
                        "lb_pair": payload.get("lb_pair"),
                        "position_address": payload.get("position"),
                        "owner_address": payload.get("owner"),
                        "active_bin_id": int(payload["active_bin_id"]),
                        "x_withdrawn_amount": str(payload["x_withdrawn_amount"]),
                        "x_added_amount": str(payload["x_added_amount"]),
                        "y_withdrawn_amount": str(payload["y_withdrawn_amount"]),
                        "y_added_amount": str(payload["y_added_amount"]),
                        "x_fee_amount": str(payload["x_fee_amount"]),
                        "y_fee_amount": str(payload["y_fee_amount"]),
                        "old_min_id": int(payload["old_min_id"]),
                        "old_max_id": int(payload["old_max_id"]),
                        "new_min_id": int(payload["new_min_id"]),
                        "new_max_id": int(payload["new_max_id"]),
                        "reward_one": str(payload["reward_one"]),
                        "reward_two": str(payload["reward_two"]),
                    }
                )
            else:
                raise ValueError(f"unsupported decoded event type: {event_type}")

            rows.append(
                (
                    observed_at,
                    signature,
                    event_index,
                    parent_ix_index,
                    slot,
                    block_time,
                    event_type,
                    common["lb_pair"],
                    common["from_address"],
                    common["position_address"],
                    common["active_bin_id"],
                    common["bin_id"],
                    common["amount_x"],
                    common["amount_y"],
                    common["token_x_fee_amount"],
                    common["token_y_fee_amount"],
                    common["protocol_token_x_fee_amount"],
                    common["protocol_token_y_fee_amount"],
                    common["owner_address"],
                    common["x_withdrawn_amount"],
                    common["x_added_amount"],
                    common["y_withdrawn_amount"],
                    common["y_added_amount"],
                    common["x_fee_amount"],
                    common["y_fee_amount"],
                    common["old_min_id"],
                    common["old_max_id"],
                    common["new_min_id"],
                    common["new_max_id"],
                    common["reward_one"],
                    common["reward_two"],
                    json.dumps(item, separators=(",", ":")),
                )
            )

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chain_transaction_snapshots(
                    observed_at, signature, slot, block_time,
                    network_fee_lamports, compute_units_consumed, succeeded,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signature) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    slot=excluded.slot,
                    block_time=excluded.block_time,
                    network_fee_lamports=excluded.network_fee_lamports,
                    compute_units_consumed=excluded.compute_units_consumed,
                    succeeded=excluded.succeeded,
                    raw_json=excluded.raw_json
                """,
                (
                    observed_at,
                    signature,
                    slot,
                    block_time,
                    int(network_fee_raw) if network_fee_raw is not None else None,
                    int(compute_units_raw) if compute_units_raw is not None else None,
                    int(bool(succeeded_raw)) if succeeded_raw is not None else None,
                    json.dumps(snapshot, separators=(",", ":")),
                ),
            )
            request_rows = []
            for request in add_requests:
                if not isinstance(request, dict):
                    raise ValueError("add request entry must be an object")
                request_rows.append(
                    (
                        observed_at,
                        signature,
                        int(request["instruction_index"]),
                        str(request["instruction_type"]),
                        str(request["requested_amount_x"]),
                        str(request["requested_amount_y"]),
                        (
                            int(request["observed_active_id"])
                            if request.get("observed_active_id") is not None
                            else None
                        ),
                        (
                            int(request["max_active_bin_slippage"])
                            if request.get("max_active_bin_slippage") is not None
                            else None
                        ),
                        (
                            int(request["min_bin_id"])
                            if request.get("min_bin_id") is not None
                            else None
                        ),
                        (
                            int(request["max_bin_id"])
                            if request.get("max_bin_id") is not None
                            else None
                        ),
                        (
                            int(request["strategy_variant"])
                            if request.get("strategy_variant") is not None
                            else None
                        ),
                        (
                            int(bool(request["strategy_favor_x"]))
                            if request.get("strategy_favor_x") is not None
                            else None
                        ),
                        json.dumps(
                            request.get("explicit_distribution") or [],
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            request.get("weighted_distribution") or [],
                            separators=(",", ":"),
                        ),
                        json.dumps(request, separators=(",", ":")),
                    )
                )
            if request_rows:
                conn.executemany(
                    """
                    INSERT INTO chain_add_liquidity_requests(
                        observed_at, signature, instruction_index,
                        instruction_type, requested_amount_x, requested_amount_y,
                        observed_active_id, max_active_bin_slippage,
                        min_bin_id, max_bin_id, strategy_variant, strategy_favor_x,
                        explicit_distribution_json, weighted_distribution_json,
                        raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(signature, instruction_index) DO UPDATE SET
                        observed_at=excluded.observed_at,
                        instruction_type=excluded.instruction_type,
                        requested_amount_x=excluded.requested_amount_x,
                        requested_amount_y=excluded.requested_amount_y,
                        observed_active_id=excluded.observed_active_id,
                        max_active_bin_slippage=excluded.max_active_bin_slippage,
                        min_bin_id=excluded.min_bin_id,
                        max_bin_id=excluded.max_bin_id,
                        strategy_variant=excluded.strategy_variant,
                        strategy_favor_x=excluded.strategy_favor_x,
                        explicit_distribution_json=excluded.explicit_distribution_json,
                        weighted_distribution_json=excluded.weighted_distribution_json,
                        raw_json=excluded.raw_json
                    """,
                    request_rows,
                )

            rebalance_rows = []
            for request in rebalance_requests:
                if not isinstance(request, dict):
                    raise ValueError("rebalance request entry must be an object")
                rebalance_rows.append(
                    (
                        observed_at,
                        signature,
                        int(request["instruction_index"]),
                        int(request["observed_active_id"]),
                        int(request["max_active_bin_slippage"]),
                        int(bool(request["should_claim_fee"])),
                        int(bool(request["should_claim_reward"])),
                        str(request["min_withdraw_x_amount"]),
                        str(request["max_deposit_x_amount"]),
                        str(request["min_withdraw_y_amount"]),
                        str(request["max_deposit_y_amount"]),
                        int(request["shrink_mode"]),
                        json.dumps(request, separators=(",", ":")),
                    )
                )
            if rebalance_rows:
                conn.executemany(
                    """
                    INSERT INTO chain_rebalance_requests(
                        observed_at, signature, instruction_index,
                        observed_active_id, max_active_bin_slippage,
                        should_claim_fee, should_claim_reward,
                        min_withdraw_x_amount, max_deposit_x_amount,
                        min_withdraw_y_amount, max_deposit_y_amount,
                        shrink_mode, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(signature, instruction_index) DO UPDATE SET
                        observed_at=excluded.observed_at,
                        observed_active_id=excluded.observed_active_id,
                        max_active_bin_slippage=excluded.max_active_bin_slippage,
                        should_claim_fee=excluded.should_claim_fee,
                        should_claim_reward=excluded.should_claim_reward,
                        min_withdraw_x_amount=excluded.min_withdraw_x_amount,
                        max_deposit_x_amount=excluded.max_deposit_x_amount,
                        min_withdraw_y_amount=excluded.min_withdraw_y_amount,
                        max_deposit_y_amount=excluded.max_deposit_y_amount,
                        shrink_mode=excluded.shrink_mode,
                        raw_json=excluded.raw_json
                    """,
                    rebalance_rows,
                )

            conn.executemany(
                """
                INSERT INTO chain_transaction_events(
                    observed_at, signature, event_index, parent_ix_index,
                    slot, block_time, event_type, lb_pair, from_address,
                    position_address, active_bin_id, bin_id, amount_x, amount_y,
                    token_x_fee_amount, token_y_fee_amount,
                    protocol_token_x_fee_amount, protocol_token_y_fee_amount,
                    owner_address, x_withdrawn_amount, x_added_amount,
                    y_withdrawn_amount, y_added_amount, x_fee_amount, y_fee_amount,
                    old_min_id, old_max_id, new_min_id, new_max_id,
                    reward_one, reward_two, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signature, event_index) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    parent_ix_index=excluded.parent_ix_index,
                    slot=excluded.slot,
                    block_time=excluded.block_time,
                    event_type=excluded.event_type,
                    lb_pair=excluded.lb_pair,
                    from_address=excluded.from_address,
                    position_address=excluded.position_address,
                    active_bin_id=excluded.active_bin_id,
                    bin_id=excluded.bin_id,
                    amount_x=excluded.amount_x,
                    amount_y=excluded.amount_y,
                    token_x_fee_amount=excluded.token_x_fee_amount,
                    token_y_fee_amount=excluded.token_y_fee_amount,
                    protocol_token_x_fee_amount=excluded.protocol_token_x_fee_amount,
                    protocol_token_y_fee_amount=excluded.protocol_token_y_fee_amount,
                    owner_address=excluded.owner_address,
                    x_withdrawn_amount=excluded.x_withdrawn_amount,
                    x_added_amount=excluded.x_added_amount,
                    y_withdrawn_amount=excluded.y_withdrawn_amount,
                    y_added_amount=excluded.y_added_amount,
                    x_fee_amount=excluded.x_fee_amount,
                    y_fee_amount=excluded.y_fee_amount,
                    old_min_id=excluded.old_min_id,
                    old_max_id=excluded.old_max_id,
                    new_min_id=excluded.new_min_id,
                    new_max_id=excluded.new_max_id,
                    reward_one=excluded.reward_one,
                    reward_two=excluded.reward_two,
                    raw_json=excluded.raw_json
                """,
                rows,
            )
        return len(rows)

    def save_composition_prestate_verification(
        self,
        payload: dict[str, Any],
        *,
        snapshot_observed_at: str,
        pool_address: str,
        observed_at: str | None = None,
    ) -> int:
        observed_at = observed_at or utc_now_iso()
        reasons = payload.get("reasons") or []
        account_checks = payload.get("account_checks") or []
        if not isinstance(reasons, list):
            raise ValueError("prestate verification reasons must be a list")
        if not isinstance(account_checks, list):
            raise ValueError("prestate verification account_checks must be a list")

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO composition_prestate_verifications(
                    observed_at, signature, snapshot_observed_at, pool_address,
                    transaction_slot, capture_slot_start, capture_slot_end,
                    eligible, reasons_json, account_checks_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signature, snapshot_observed_at) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    pool_address=excluded.pool_address,
                    transaction_slot=excluded.transaction_slot,
                    capture_slot_start=excluded.capture_slot_start,
                    capture_slot_end=excluded.capture_slot_end,
                    eligible=excluded.eligible,
                    reasons_json=excluded.reasons_json,
                    account_checks_json=excluded.account_checks_json,
                    raw_json=excluded.raw_json
                """,
                (
                    observed_at,
                    str(payload["signature"]),
                    snapshot_observed_at,
                    pool_address,
                    int(payload["transaction_slot"]),
                    int(payload["capture_slot_start"]),
                    int(payload["capture_slot_end"]),
                    int(bool(payload["eligible"])),
                    json.dumps(reasons, separators=(",", ":")),
                    json.dumps(account_checks, separators=(",", ":")),
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
        return 1

    def register_model(
        self,
        *,
        model_id: str,
        model_family: str,
        feature_version: str,
        dataset_version: str,
        metrics: dict[str, Any],
        artifact_uri: str | None = None,
        train_start: str | None = None,
        train_end: str | None = None,
        validation_start: str | None = None,
        validation_end: str | None = None,
        train_rows: int | None = None,
        validation_rows: int | None = None,
        notes: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO model_registry(
                    model_id, created_at, updated_at, model_family,
                    feature_version, dataset_version, status, artifact_uri,
                    train_start, train_end, validation_start, validation_end,
                    train_rows, validation_rows, metrics_json, notes
                ) VALUES (?, ?, ?, ?, ?, ?, 'OFFLINE_CANDIDATE', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    now,
                    now,
                    model_family,
                    feature_version,
                    dataset_version,
                    artifact_uri,
                    train_start,
                    train_end,
                    validation_start,
                    validation_end,
                    train_rows,
                    validation_rows,
                    json.dumps(metrics, separators=(",", ":")),
                    notes,
                ),
            )

    def model_registry_entry(self, model_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT model_id, created_at, updated_at, model_family,
                       feature_version, dataset_version, status, artifact_uri,
                       train_start, train_end, validation_start, validation_end,
                       train_rows, validation_rows, metrics_json, notes
                FROM model_registry
                WHERE model_id = ?
                LIMIT 1
                """,
                (model_id,),
            ).fetchone()
        if row is None:
            return None
        columns = (
            "model_id", "created_at", "updated_at", "model_family",
            "feature_version", "dataset_version", "status", "artifact_uri",
            "train_start", "train_end", "validation_start", "validation_end",
            "train_rows", "validation_rows", "metrics_json", "notes",
        )
        return dict(zip(columns, row))

    def current_model_champion(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT model_id, created_at, updated_at, model_family,
                       feature_version, dataset_version, status, artifact_uri,
                       train_start, train_end, validation_start, validation_end,
                       train_rows, validation_rows, metrics_json, notes
                FROM model_registry
                WHERE status = 'CHAMPION'
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        columns = (
            "model_id", "created_at", "updated_at", "model_family",
            "feature_version", "dataset_version", "status", "artifact_uri",
            "train_start", "train_end", "validation_start", "validation_end",
            "train_rows", "validation_rows", "metrics_json", "notes",
        )
        return dict(zip(columns, row))

    def update_model_status(
        self,
        model_id: str,
        *,
        expected_status: str,
        new_status: str,
        notes: str | None = None,
    ) -> None:
        allowed = MODEL_STATUS_TRANSITIONS.get(expected_status)
        if allowed is None or new_status not in allowed:
            raise ValueError(
                f"invalid model transition: {expected_status} -> {new_status}"
            )
        now = utc_now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE model_registry
                SET status = ?, updated_at = ?,
                    notes = COALESCE(?, notes)
                WHERE model_id = ? AND status = ?
                """,
                (new_status, now, notes, model_id, expected_status),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"model {model_id} is not in expected status {expected_status}"
                )

    def save_phase_promotion_evidence(
        self,
        *,
        phase_name: str,
        evidence_type: str,
        qualified: bool,
        evidence: dict[str, Any],
    ) -> None:
        if not phase_name.strip():
            raise ValueError("phase_name is required")
        if not evidence_type.strip():
            raise ValueError("evidence_type is required")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO phase_promotion_evidence(
                    phase_name, promoted_at, evidence_type,
                    qualified, evidence_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(phase_name) DO UPDATE SET
                    promoted_at=excluded.promoted_at,
                    evidence_type=excluded.evidence_type,
                    qualified=excluded.qualified,
                    evidence_json=excluded.evidence_json
                """,
                (
                    phase_name,
                    utc_now_iso(),
                    evidence_type,
                    int(bool(qualified)),
                    json.dumps(evidence, separators=(",", ":")),
                ),
            )

    def phase_is_promoted(
        self,
        phase_name: str,
        *,
        evidence_type: str | None = None,
    ) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT evidence_type, qualified
                FROM phase_promotion_evidence
                WHERE phase_name = ?
                LIMIT 1
                """,
                (phase_name,),
            ).fetchone()
        if row is None or not bool(row[1]):
            return False
        if evidence_type is not None and str(row[0]) != evidence_type:
            return False
        return True

    def save_model_offline_evidence(
        self,
        *,
        model_id: str,
        evidence_type: str,
        qualified: bool,
        evidence: dict[str, Any],
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id is required")
        if not evidence_type.strip():
            raise ValueError("evidence_type is required")
        with self.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM model_registry WHERE model_id = ?",
                (model_id,),
            ).fetchone() is None:
                raise ValueError(f"unknown model_id: {model_id}")
            conn.execute(
                """
                INSERT INTO model_offline_evidence(
                    model_id, created_at, evidence_type, qualified, evidence_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    evidence_type=excluded.evidence_type,
                    qualified=excluded.qualified,
                    evidence_json=excluded.evidence_json
                """,
                (
                    model_id,
                    utc_now_iso(),
                    evidence_type,
                    int(bool(qualified)),
                    json.dumps(evidence, separators=(",", ":")),
                ),
            )

    def qualify_model_offline(
        self,
        model_id: str,
        *,
        notes: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT evidence_type, qualified
                FROM model_offline_evidence
                WHERE model_id = ?
                LIMIT 1
                """,
                (model_id,),
            ).fetchone()
            if row is None:
                raise ValueError("offline challenger evidence is missing")
            if str(row[0]) != "OFFLINE_CHALLENGER_V1" or not bool(row[1]):
                raise ValueError("offline challenger evidence is not qualified")
            cursor = conn.execute(
                """
                UPDATE model_registry
                SET status = 'OFFLINE_QUALIFIED', updated_at = ?,
                    notes = COALESCE(?, notes)
                WHERE model_id = ? AND status = 'OFFLINE_CANDIDATE'
                """,
                (now, notes, model_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"model {model_id} is not in OFFLINE_CANDIDATE status"
                )

    def start_model_paper_challenger(
        self,
        model_id: str,
        *,
        notes: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE model_registry
                SET status = 'PAPER_CHALLENGER', updated_at = ?,
                    notes = COALESCE(?, notes)
                WHERE model_id = ? AND status = 'OFFLINE_QUALIFIED'
                """,
                (now, notes, model_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"model {model_id} is not in OFFLINE_QUALIFIED status"
                )

    def save_model_promotion_evidence(
        self,
        *,
        model_id: str,
        evidence_type: str,
        qualified: bool,
        evidence: dict[str, Any],
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id is required")
        if not evidence_type.strip():
            raise ValueError("evidence_type is required")
        with self.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM model_registry WHERE model_id = ?",
                (model_id,),
            ).fetchone() is None:
                raise ValueError(f"unknown model_id: {model_id}")
            conn.execute(
                """
                INSERT INTO model_promotion_evidence(
                    model_id, created_at, evidence_type, qualified, evidence_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    evidence_type=excluded.evidence_type,
                    qualified=excluded.qualified,
                    evidence_json=excluded.evidence_json
                """,
                (
                    model_id,
                    utc_now_iso(),
                    evidence_type,
                    int(bool(qualified)),
                    json.dumps(evidence, separators=(",", ":")),
                ),
            )

    def promote_model_to_champion(
        self,
        model_id: str,
        *,
        notes: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT evidence_type, qualified
                FROM model_promotion_evidence
                WHERE model_id = ?
                LIMIT 1
                """,
                (model_id,),
            ).fetchone()
            if row is None:
                raise ValueError("paper promotion evidence is missing")
            if str(row[0]) != "PAPER_VALIDATION_V1" or not bool(row[1]):
                raise ValueError("paper promotion evidence is not qualified")
            champion = conn.execute(
                """
                SELECT model_id
                FROM model_registry
                WHERE status = 'CHAMPION'
                LIMIT 1
                """
            ).fetchone()
            if champion is not None and str(champion[0]) != model_id:
                raise ValueError(
                    f"champion already exists: {champion[0]}; "
                    "roll it back before promoting another model"
                )
            cursor = conn.execute(
                """
                UPDATE model_registry
                SET status = 'CHAMPION', updated_at = ?,
                    notes = COALESCE(?, notes)
                WHERE model_id = ? AND status = 'PAPER_CHALLENGER'
                """,
                (now, notes, model_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"model {model_id} is not in PAPER_CHALLENGER status"
                )

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
            positions = conn.execute(
                "SELECT COUNT(*), MAX(observed_at), COUNT(DISTINCT position_address) FROM chain_position_snapshots"
            ).fetchone()
            position_bins = conn.execute(
                "SELECT COUNT(*) FROM position_bin_snapshots"
            ).fetchone()[0]
            position_events = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT position_address) FROM position_event_history"
            ).fetchone()
            chain_tx_events = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT signature) FROM chain_transaction_events"
            ).fetchone()
            chain_tx_snapshots = conn.execute(
                """
                SELECT COUNT(*), SUM(CASE WHEN network_fee_lamports IS NOT NULL THEN 1 ELSE 0 END)
                FROM chain_transaction_snapshots
                """
            ).fetchone()
            add_requests = conn.execute(
                "SELECT COUNT(*) FROM chain_add_liquidity_requests"
            ).fetchone()[0]
            rebalance_requests = conn.execute(
                "SELECT COUNT(*) FROM chain_rebalance_requests"
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
            "chain_position_snapshots": positions[0],
            "chain_position_count": positions[2],
            "latest_chain_position_snapshot": positions[1],
            "position_bin_snapshots": position_bins,
            "position_event_history": position_events[0],
            "position_event_position_count": position_events[1],
            "chain_transaction_events": chain_tx_events[0],
            "chain_transaction_count": chain_tx_events[1],
            "chain_transaction_snapshots": chain_tx_snapshots[0],
            "transaction_fee_samples": chain_tx_snapshots[1] or 0,
            "chain_add_liquidity_requests": add_requests,
            "chain_rebalance_requests": rebalance_requests,
            "quality_failures": failures,
            "collection_errors": errors,
        }
