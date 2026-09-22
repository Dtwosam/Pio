from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .meteora_api import MeteoraDataAPI
from .normalization import normalize_ohlcv, normalize_volume_history
from .quality import assess_candles
from .settings import Settings
from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class CollectorResult:
    run_id: str
    pools_seen: int
    history_pools_seen: int
    candles_saved: int
    volume_buckets_saved: int
    quality_failures: int
    collection_errors: int


def extract_pool_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("data", "pools", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def pool_address(pool: dict[str, Any]) -> str | None:
    for key in ("address", "pool_address", "lb_pair"):
        value = pool.get(key)
        if value:
            return str(value)
    return None


def collect_once(settings: Settings | None = None) -> CollectorResult:
    settings = settings or Settings.from_env()
    storage = Storage(settings.database_path)
    run_id = str(uuid4())
    started_at = utc_now_iso()
    storage.start_run(run_id, started_at)

    now_epoch = int(datetime.now(timezone.utc).timestamp())
    history_start = max(0, now_epoch - settings.history_lookback_seconds)

    pools_seen = 0
    history_pools_seen = 0
    candles_saved = 0
    volume_buckets_saved = 0
    quality_failures = 0
    collection_errors = 0
    pool_rows: list[dict[str, Any]] = []

    try:
        with MeteoraDataAPI(
            base_url=settings.meteora_data_api,
            requests_per_second=settings.requests_per_second,
        ) as api:
            metrics = api.protocol_metrics()
            storage.save_raw("/stats/protocol_metrics", metrics, observed_at=started_at)

            for page in range(1, settings.pool_pages_per_run + 1):
                payload = api.pools(page=page, page_size=settings.pool_page_size, sort_by="tvl:desc")
                storage.save_raw("/pools", payload, observed_at=started_at, entity_key=f"page:{page}")

                rows = extract_pool_rows(payload)
                for row in rows:
                    storage.save_pool_snapshot(row, observed_at=started_at)
                pool_rows.extend(rows)
                pools_seen += len(rows)

            addresses: list[str] = []
            for row in pool_rows:
                address = pool_address(row)
                if address and address not in addresses:
                    addresses.append(address)
                if len(addresses) >= settings.top_pool_history_count:
                    break

            for address in addresses:
                history_success = False

                try:
                    detail = api.pool(address)
                    storage.save_raw(
                        f"/pools/{address}",
                        detail,
                        observed_at=started_at,
                        entity_key=address,
                    )
                    if isinstance(detail, dict):
                        storage.save_pool_snapshot(detail, observed_at=started_at)
                except Exception as exc:
                    storage.save_collection_error(run_id, f"/pools/{address}", exc, entity_key=address)
                    collection_errors += 1

                try:
                    ohlcv = api.ohlcv(
                        address,
                        timeframe=settings.ohlcv_timeframe,
                        start_time=history_start,
                        end_time=now_epoch,
                    )
                    storage.save_raw(
                        f"/pools/{address}/ohlcv",
                        ohlcv,
                        observed_at=started_at,
                        entity_key=address,
                    )
                    candles = normalize_ohlcv(
                        address,
                        ohlcv,
                        observed_at=started_at,
                        resolution=settings.ohlcv_timeframe,
                    )
                    candles_saved += storage.save_ohlcv_candles(candles)
                    checks = assess_candles(
                        candles,
                        checked_at=started_at,
                        max_age_seconds=settings.market_data_stale_seconds,
                        gap_multiplier=settings.history_gap_multiplier,
                    )
                    storage.save_quality_checks("ohlcv", address, checks, checked_at=started_at)
                    quality_failures += sum(1 for item in checks if item["status"] == "FAIL")
                    history_success = True
                except Exception as exc:
                    storage.save_collection_error(
                        run_id,
                        f"/pools/{address}/ohlcv",
                        exc,
                        entity_key=address,
                    )
                    collection_errors += 1

                try:
                    volume = api.volume_history(
                        address,
                        timeframe=settings.ohlcv_timeframe,
                        start_time=history_start,
                        end_time=now_epoch,
                    )
                    storage.save_raw(
                        f"/pools/{address}/volume/history",
                        volume,
                        observed_at=started_at,
                        entity_key=address,
                    )
                    buckets = normalize_volume_history(address, volume, observed_at=started_at)
                    volume_buckets_saved += storage.save_volume_buckets(buckets)
                    history_success = True
                except Exception as exc:
                    storage.save_collection_error(
                        run_id,
                        f"/pools/{address}/volume/history",
                        exc,
                        entity_key=address,
                    )
                    collection_errors += 1

                if history_success:
                    history_pools_seen += 1

        status = "PARTIAL" if collection_errors else "SUCCESS"
        storage.finish_run(
            run_id,
            status=status,
            pools_seen=pools_seen,
            history_pools_seen=history_pools_seen,
        )
        return CollectorResult(
            run_id=run_id,
            pools_seen=pools_seen,
            history_pools_seen=history_pools_seen,
            candles_saved=candles_saved,
            volume_buckets_saved=volume_buckets_saved,
            quality_failures=quality_failures,
            collection_errors=collection_errors,
        )
    except Exception as exc:
        storage.finish_run(
            run_id,
            status="FAILED",
            pools_seen=pools_seen,
            history_pools_seen=history_pools_seen,
            error=str(exc)[:2000],
        )
        raise
