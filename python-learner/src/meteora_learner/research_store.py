from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Sequence


class ResearchStore:
    """Read-only research queries over the collector SQLite database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def latest_pool_snapshot(self, address: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, address, name, tvl, volume_24h, fees_24h,
                       current_price, bin_step, active_bin_id, apr, apy,
                       token_x_symbol, token_y_symbol
                FROM pool_snapshots
                WHERE address = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (address,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def load_ohlcv(
        self,
        address: str,
        *,
        limit: int = 500,
        resolution: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")

        params: list[Any] = [address]
        resolution_clause = ""
        if resolution is not None:
            resolution_clause = "AND resolution = ?"
            params.append(resolution)
        params.append(limit)

        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM (
                    SELECT candle_time, resolution, open, high, low, close, volume
                    FROM ohlcv_candles
                    WHERE pool_address = ?
                    {resolution_clause}
                    ORDER BY candle_time DESC
                    LIMIT ?
                )
                ORDER BY candle_time ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()

        return [dict(row) for row in rows]

    def latest_chain_pool_snapshot(
        self,
        address: str,
        *,
        as_of: str | None = None,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            if as_of is None:
                row = conn.execute(
                    """
                    SELECT observed_at, pool_address, active_bin_id, bin_step,
                           token_x_mint, token_y_mint, token_x_program, token_y_program,
                           base_fee_rate, variable_fee_rate, total_fee_rate, deposit_total_fee_rate,
                           protocol_share_bps, collect_fee_mode, supports_limit_order,
                           reward_mint_0, reward_mint_1, reward_rate_0, reward_rate_1,
                           reward_duration_end_0, reward_duration_end_1,
                           reward_last_update_time_0, reward_last_update_time_1
                    FROM chain_pool_snapshots
                    WHERE pool_address = ?
                    ORDER BY julianday(observed_at) DESC, id DESC
                    LIMIT 1
                    """,
                    (address,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT observed_at, pool_address, active_bin_id, bin_step,
                           token_x_mint, token_y_mint, token_x_program, token_y_program,
                           base_fee_rate, variable_fee_rate, total_fee_rate, deposit_total_fee_rate,
                           protocol_share_bps, collect_fee_mode, supports_limit_order,
                           reward_mint_0, reward_mint_1, reward_rate_0, reward_rate_1,
                           reward_duration_end_0, reward_duration_end_1,
                           reward_last_update_time_0, reward_last_update_time_1
                    FROM chain_pool_snapshots
                    WHERE pool_address = ?
                      AND julianday(observed_at) <= julianday(?)
                    ORDER BY julianday(observed_at) DESC, id DESC
                    LIMIT 1
                    """,
                    (address, as_of),
                ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def chain_observation_times(
        self,
        address: str,
        *,
        limit: int | None = 2,
        ascending: bool = False,
    ) -> list[str]:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive when supplied")
        order = "ASC" if ascending else "DESC"
        conn = self._connect()
        try:
            if limit is None:
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT observed_at
                    FROM chain_pool_snapshots
                    WHERE pool_address = ?
                    ORDER BY observed_at {order}
                    """,
                    (address,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT observed_at
                    FROM chain_pool_snapshots
                    WHERE pool_address = ?
                    ORDER BY observed_at {order}
                    LIMIT ?
                    """,
                    (address, limit),
                ).fetchall()
        finally:
            conn.close()
        return [str(row["observed_at"]) for row in rows]

    def load_bin_liquidity(
        self,
        address: str,
        *,
        observed_at: str | None = None,
    ) -> list[dict[str, Any]]:
        if observed_at is None:
            times = self.chain_observation_times(address, limit=1)
            if not times:
                return []
            observed_at = times[0]

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT observed_at, pool_address, bin_array_index, bin_array_address, bin_id, price,
                       amount_x, amount_y, liquidity_supply,
                       fee_amount_x_per_token_stored,
                       fee_amount_y_per_token_stored,
                       reward_per_token_stored_0,
                       reward_per_token_stored_1
                FROM bin_liquidity_snapshots
                WHERE pool_address = ? AND observed_at = ?
                ORDER BY bin_id ASC
                """,
                (address, observed_at),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def latest_position_snapshot(self, position_address: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, position_address, pool_address, owner, fee_owner,
                       lower_bin_id, upper_bin_id, total_x_amount, total_y_amount,
                       fee_x, fee_y, reward_one, reward_two, last_updated_at,
                       total_claimed_fee_x_amount, total_claimed_fee_y_amount,
                       supports_limit_order, reward_mint_0, reward_mint_1
                FROM chain_position_snapshots
                WHERE position_address = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (position_address,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def load_position_bins(
        self,
        position_address: str,
        *,
        observed_at: str | None = None,
    ) -> list[dict[str, Any]]:
        if observed_at is None:
            snapshot = self.latest_position_snapshot(position_address)
            if snapshot is None:
                return []
            observed_at = str(snapshot["observed_at"])

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT observed_at, position_address, bin_id, price,
                       bin_x_amount, bin_y_amount, bin_liquidity,
                       bin_fee_x_per_token_stored, bin_fee_y_per_token_stored,
                       bin_reward_per_token_stored_0, bin_reward_per_token_stored_1,
                       reward_checkpoint_available, position_liquidity, position_x_amount, position_y_amount,
                       position_fee_x_amount, position_fee_y_amount,
                       reward_one, reward_two
                FROM position_bin_snapshots
                WHERE position_address = ? AND observed_at = ?
                ORDER BY bin_id ASC
                """,
                (position_address, observed_at),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def chain_pool_snapshot_at(
        self,
        address: str,
        observed_at: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, pool_address, active_bin_id, bin_step,
                       token_x_mint, token_y_mint, token_x_program, token_y_program,
                       base_fee_rate, variable_fee_rate, total_fee_rate, deposit_total_fee_rate,
                       protocol_share_bps, collect_fee_mode, supports_limit_order,
                       reward_mint_0, reward_mint_1, reward_rate_0, reward_rate_1,
                       reward_duration_end_0, reward_duration_end_1,
                       reward_last_update_time_0, reward_last_update_time_1
                FROM chain_pool_snapshots
                WHERE pool_address = ? AND observed_at = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (address, observed_at),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def position_observation_times(
        self,
        position_address: str,
        *,
        limit: int | None = 2,
        ascending: bool = False,
    ) -> list[str]:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive when supplied")
        order = "ASC" if ascending else "DESC"
        conn = self._connect()
        try:
            if limit is None:
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT observed_at
                    FROM chain_position_snapshots
                    WHERE position_address = ?
                    ORDER BY observed_at {order}
                    """,
                    (position_address,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT observed_at
                    FROM chain_position_snapshots
                    WHERE position_address = ?
                    ORDER BY observed_at {order}
                    LIMIT ?
                    """,
                    (position_address, limit),
                ).fetchall()
        finally:
            conn.close()
        return [str(row["observed_at"]) for row in rows]

    def position_snapshot_at(
        self,
        position_address: str,
        observed_at: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, position_address, pool_address, owner, fee_owner,
                       lower_bin_id, upper_bin_id, total_x_amount, total_y_amount,
                       fee_x, fee_y, reward_one, reward_two, last_updated_at,
                       total_claimed_fee_x_amount, total_claimed_fee_y_amount,
                       supports_limit_order, reward_mint_0, reward_mint_1
                FROM chain_position_snapshots
                WHERE position_address = ? AND observed_at = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (position_address, observed_at),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def position_addresses(self, *, limit: int | None = None) -> list[str]:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive when supplied")
        conn = self._connect()
        try:
            if limit is None:
                rows = conn.execute(
                    """
                    SELECT position_address, MAX(observed_at) AS latest_observed_at
                    FROM chain_position_snapshots
                    GROUP BY position_address
                    ORDER BY latest_observed_at DESC, position_address ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT position_address, MAX(observed_at) AS latest_observed_at
                    FROM chain_position_snapshots
                    GROUP BY position_address
                    ORDER BY latest_observed_at DESC, position_address ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        finally:
            conn.close()
        return [str(row["position_address"]) for row in rows]

    def load_position_history_events(
        self,
        position_address: str,
        *,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if event_type is None:
                rows = conn.execute(
                    """
                    SELECT observed_at, position_address, signature, ix_index,
                           event_type, block_time, slot, pool_address,
                           user_address, token_x, token_y, amount_x, amount_y,
                           amount_x_usd, amount_y_usd, total_usd, created_at
                    FROM position_event_history
                    WHERE position_address = ?
                    ORDER BY block_time ASC, ix_index ASC, id ASC
                    """,
                    (position_address,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT observed_at, position_address, signature, ix_index,
                           event_type, block_time, slot, pool_address,
                           user_address, token_x, token_y, amount_x, amount_y,
                           amount_x_usd, amount_y_usd, total_usd, created_at
                    FROM position_event_history
                    WHERE position_address = ? AND event_type = ?
                    ORDER BY block_time ASC, ix_index ASC, id ASC
                    """,
                    (position_address, event_type),
                ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def load_transaction_events(
        self,
        signature: str,
        *,
        parent_ix_index: int | None = None,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if parent_ix_index is None:
                rows = conn.execute(
                    """
                    SELECT observed_at, signature, event_index, parent_ix_index,
                           slot, block_time, event_type, lb_pair, from_address,
                           position_address, active_bin_id, bin_id, amount_x,
                           amount_y, token_x_fee_amount, token_y_fee_amount,
                           protocol_token_x_fee_amount,
                           protocol_token_y_fee_amount,
                           owner_address, x_withdrawn_amount, x_added_amount,
                           y_withdrawn_amount, y_added_amount,
                           x_fee_amount, y_fee_amount,
                           old_min_id, old_max_id, new_min_id, new_max_id,
                           reward_one, reward_two
                    FROM chain_transaction_events
                    WHERE signature = ?
                    ORDER BY event_index ASC
                    """,
                    (signature,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT observed_at, signature, event_index, parent_ix_index,
                           slot, block_time, event_type, lb_pair, from_address,
                           position_address, active_bin_id, bin_id, amount_x,
                           amount_y, token_x_fee_amount, token_y_fee_amount,
                           protocol_token_x_fee_amount,
                           protocol_token_y_fee_amount,
                           owner_address, x_withdrawn_amount, x_added_amount,
                           y_withdrawn_amount, y_added_amount,
                           x_fee_amount, y_fee_amount,
                           old_min_id, old_max_id, new_min_id, new_max_id,
                           reward_one, reward_two
                    FROM chain_transaction_events
                    WHERE signature = ? AND parent_ix_index = ?
                    ORDER BY event_index ASC
                    """,
                    (signature, parent_ix_index),
                ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def transaction_snapshot(self, signature: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, signature, slot, block_time,
                       network_fee_lamports, compute_units_consumed, succeeded
                FROM chain_transaction_snapshots
                WHERE signature = ?
                LIMIT 1
                """,
                (signature,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def add_liquidity_request(
        self,
        signature: str,
        instruction_index: int,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, signature, instruction_index,
                       instruction_type, requested_amount_x, requested_amount_y,
                       observed_active_id, max_active_bin_slippage,
                       min_bin_id, max_bin_id, strategy_variant, strategy_favor_x,
                       explicit_distribution_json, weighted_distribution_json
                FROM chain_add_liquidity_requests
                WHERE signature = ? AND instruction_index = ?
                LIMIT 1
                """,
                (signature, instruction_index),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def pool_capture_before_slot(
        self,
        pool_address: str,
        *,
        target_slot: int,
        active_bin_id: int,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT c.observed_at, c.pool_address,
                       c.capture_slot_start, c.capture_slot_end,
                       c.clock_unix_timestamp,
                       c.fee_base_factor, c.fee_filter_period,
                       c.fee_decay_period, c.fee_reduction_factor,
                       c.fee_variable_fee_control,
                       c.fee_max_volatility_accumulator,
                       c.fee_base_fee_power_factor,
                       c.fee_volatility_accumulator,
                       c.fee_volatility_reference,
                       c.fee_index_reference,
                       c.fee_last_update_timestamp,
                       p.active_bin_id, p.bin_step,
                       p.protocol_share_bps,
                       p.token_x_program, p.token_y_program
                FROM chain_pool_capture_state c
                JOIN chain_pool_snapshots p
                  ON p.pool_address = c.pool_address
                 AND p.observed_at = c.observed_at
                WHERE c.pool_address = ?
                  AND c.capture_slot_end IS NOT NULL
                  AND c.capture_slot_end < ?
                  AND p.active_bin_id = ?
                ORDER BY c.capture_slot_end DESC, c.id DESC
                LIMIT 1
                """,
                (pool_address, target_slot, active_bin_id),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def bin_liquidity_at(
        self,
        pool_address: str,
        *,
        observed_at: str,
        bin_id: int,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, pool_address, bin_array_index,
                       bin_array_address, bin_id, price, amount_x, amount_y,
                       liquidity_supply, fee_amount_x_per_token_stored,
                       fee_amount_y_per_token_stored,
                       reward_per_token_stored_0, reward_per_token_stored_1
                FROM bin_liquidity_snapshots
                WHERE pool_address = ? AND observed_at = ? AND bin_id = ?
                LIMIT 1
                """,
                (pool_address, observed_at, bin_id),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def prestate_verification(
        self,
        signature: str,
        *,
        snapshot_observed_at: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, signature, snapshot_observed_at,
                       pool_address, transaction_slot,
                       capture_slot_start, capture_slot_end,
                       eligible, reasons_json, account_checks_json
                FROM composition_prestate_verifications
                WHERE signature = ? AND snapshot_observed_at = ?
                LIMIT 1
                """,
                (signature, snapshot_observed_at),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def rebalance_request(
        self,
        signature: str,
        instruction_index: int,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, signature, instruction_index,
                       observed_active_id, max_active_bin_slippage,
                       should_claim_fee, should_claim_reward,
                       min_withdraw_x_amount, max_deposit_x_amount,
                       min_withdraw_y_amount, max_deposit_y_amount,
                       shrink_mode
                FROM chain_rebalance_requests
                WHERE signature = ? AND instruction_index = ?
                LIMIT 1
                """,
                (signature, instruction_index),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def load_position_transaction_events(
        self,
        position_address: str,
        *,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [position_address]
        event_clause = ""
        if event_type is not None:
            event_clause = "AND event_type = ?"
            params.append(event_type)

        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT observed_at, signature, event_index, parent_ix_index,
                       slot, block_time, event_type, lb_pair, from_address,
                       position_address, active_bin_id, bin_id, amount_x, amount_y,
                       token_x_fee_amount, token_y_fee_amount,
                       protocol_token_x_fee_amount, protocol_token_y_fee_amount,
                       owner_address, x_withdrawn_amount, x_added_amount,
                       y_withdrawn_amount, y_added_amount,
                       x_fee_amount, y_fee_amount,
                       old_min_id, old_max_id, new_min_id, new_max_id,
                       reward_one, reward_two
                FROM chain_transaction_events
                WHERE position_address = ?
                {event_clause}
                ORDER BY block_time ASC, slot ASC, event_index ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def position_history_addresses(
        self,
        *,
        event_type: str | None = None,
    ) -> list[str]:
        params: list[Any] = []
        clause = ""
        if event_type is not None:
            clause = "WHERE event_type = ?"
            params.append(event_type)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT position_address, MAX(block_time) AS latest_block_time
                FROM position_event_history
                {clause}
                GROUP BY position_address
                ORDER BY latest_block_time DESC, position_address ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        return [str(row["position_address"]) for row in rows]

    def transaction_event_position_addresses(
        self,
        *,
        event_type: str | None = None,
    ) -> list[str]:
        params: list[Any] = []
        clause = "WHERE position_address IS NOT NULL"
        if event_type is not None:
            clause += " AND event_type = ?"
            params.append(event_type)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT position_address, MAX(slot) AS latest_slot
                FROM chain_transaction_events
                {clause}
                GROUP BY position_address
                ORDER BY latest_slot DESC, position_address ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        return [str(row["position_address"]) for row in rows]

    def latest_pool_snapshots(
        self,
        *,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cutoff_sql = (
                ""
                if as_of is None
                else "WHERE julianday(observed_at) <= julianday(?)"
            )
            params = () if as_of is None else (as_of,)
            rows = conn.execute(
                f"""
                SELECT p.observed_at, p.address, p.name, p.tvl, p.volume_24h,
                       p.fees_24h, p.current_price, p.bin_step, p.active_bin_id,
                       p.apr, p.apy, p.token_x_symbol, p.token_y_symbol,
                       p.token_x_decimals, p.token_y_decimals,
                       p.dynamic_fee_pct, p.base_fee_pct, p.max_fee_pct,
                       p.protocol_fee_pct, p.collect_fee_mode,
                       p.is_blacklisted, p.pool_created_at
                FROM pool_snapshots p
                JOIN (
                    SELECT address, MAX(observed_at) AS max_observed_at
                    FROM pool_snapshots
                    {cutoff_sql}
                    GROUP BY address
                ) latest
                  ON latest.address = p.address
                 AND latest.max_observed_at = p.observed_at
                WHERE p.id = (
                    SELECT MAX(p2.id)
                    FROM pool_snapshots p2
                    WHERE p2.address = p.address
                      AND p2.observed_at = p.observed_at
                )
                ORDER BY p.address ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def pool_snapshot_history(
        self,
        *,
        pool_addresses: Sequence[str] | None = None,
        start_observed_at: str | None = None,
        end_observed_at: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if pool_addresses is not None:
            normalized = [str(value).strip() for value in pool_addresses]
            if any(not value for value in normalized):
                raise ValueError("pool_addresses cannot contain blank values")
            if not normalized:
                return []
            placeholders = ", ".join("?" for _ in normalized)
            clauses.append(f"p.address IN ({placeholders})")
            params.extend(normalized)

        if start_observed_at is not None:
            clauses.append(
                "julianday(p.observed_at) >= julianday(?)"
            )
            params.append(start_observed_at)
        if end_observed_at is not None:
            clauses.append(
                "julianday(p.observed_at) <= julianday(?)"
            )
            params.append(end_observed_at)

        where = ""
        if clauses:
            where = "AND " + " AND ".join(clauses)

        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT p.observed_at, p.address, p.name, p.tvl,
                       p.volume_24h, p.fees_24h, p.current_price,
                       p.bin_step, p.active_bin_id, p.apr, p.apy,
                       p.token_x_symbol, p.token_y_symbol,
                       p.token_x_decimals, p.token_y_decimals,
                       p.dynamic_fee_pct, p.base_fee_pct, p.max_fee_pct,
                       p.protocol_fee_pct, p.collect_fee_mode,
                       p.is_blacklisted, p.pool_created_at
                FROM pool_snapshots p
                WHERE p.id = (
                    SELECT MAX(p2.id)
                    FROM pool_snapshots p2
                    WHERE p2.address = p.address
                      AND p2.observed_at = p.observed_at
                )
                {where}
                ORDER BY p.address ASC,
                         julianday(p.observed_at) ASC,
                         p.id ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def chain_observation_count(
        self,
        pool_address: str,
        *,
        as_of: str | None = None,
    ) -> int:
        conn = self._connect()
        try:
            if as_of is None:
                row = conn.execute(
                    """
                    SELECT COUNT(DISTINCT observed_at) AS observation_count
                    FROM chain_pool_snapshots
                    WHERE pool_address = ?
                    """,
                    (pool_address,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT COUNT(DISTINCT observed_at) AS observation_count
                    FROM chain_pool_snapshots
                    WHERE pool_address = ?
                      AND julianday(observed_at) <= julianday(?)
                    """,
                    (pool_address, as_of),
                ).fetchone()
        finally:
            conn.close()
        return int(row["observation_count"]) if row is not None else 0

