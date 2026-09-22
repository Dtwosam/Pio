from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any


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

    def latest_chain_pool_snapshot(self, address: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT observed_at, pool_address, active_bin_id, bin_step,
                       token_x_mint, token_y_mint
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (address,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def chain_observation_times(self, address: str, *, limit: int = 2) -> list[str]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT observed_at
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                ORDER BY observed_at DESC
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
                SELECT observed_at, pool_address, bin_array_index, bin_id,
                       amount_x, amount_y, liquidity_supply,
                       fee_amount_x_per_token_stored,
                       fee_amount_y_per_token_stored
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
                       total_claimed_fee_x_amount, total_claimed_fee_y_amount
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
                       position_liquidity, position_x_amount, position_y_amount,
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

