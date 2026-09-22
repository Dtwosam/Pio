from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    meteora_data_api: str = "https://dlmm.datapi.meteora.ag"
    database_path: Path = Path("data/pio.db")
    pool_page_size: int = 250
    pool_pages_per_run: int = 1
    top_pool_history_count: int = 25
    requests_per_second: float = 20.0
    ohlcv_timeframe: str = "5m"
    history_lookback_seconds: int = 86_400
    market_data_stale_seconds: int = 900
    history_gap_multiplier: float = 3.0

    @classmethod
    def from_env(cls) -> "Settings":
        timeframe = os.getenv("PIO_OHLCV_TIMEFRAME", cls.ohlcv_timeframe).strip()
        return cls(
            meteora_data_api=os.getenv("METEORA_DATA_API", cls.meteora_data_api),
            database_path=Path(os.getenv("PIO_DATABASE_PATH", str(cls.database_path))),
            pool_page_size=int(os.getenv("PIO_POOL_PAGE_SIZE", str(cls.pool_page_size))),
            pool_pages_per_run=int(os.getenv("PIO_POOL_PAGES_PER_RUN", str(cls.pool_pages_per_run))),
            top_pool_history_count=int(
                os.getenv("PIO_TOP_POOL_HISTORY_COUNT", str(cls.top_pool_history_count))
            ),
            requests_per_second=float(
                os.getenv("PIO_REQUESTS_PER_SECOND", str(cls.requests_per_second))
            ),
            ohlcv_timeframe=timeframe,
            history_lookback_seconds=int(
                os.getenv("PIO_HISTORY_LOOKBACK_SECONDS", str(cls.history_lookback_seconds))
            ),
            market_data_stale_seconds=int(
                os.getenv("PIO_MARKET_DATA_STALE_SECONDS", str(cls.market_data_stale_seconds))
            ),
            history_gap_multiplier=float(
                os.getenv("PIO_HISTORY_GAP_MULTIPLIER", str(cls.history_gap_multiplier))
            ),
        )
