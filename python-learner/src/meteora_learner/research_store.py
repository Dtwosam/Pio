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
