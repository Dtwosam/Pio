from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


def extract_rows(payload: Any, keys: Iterable[str] = ("data", "items", "results", "candles", "history")) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = extract_rows(value, keys)
            if nested:
                return nested
    return []


def first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_timestamp(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None:
        return normalize_timestamp(numeric)

    candidate = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def normalize_ohlcv(
    pool_address: str,
    payload: Any,
    *,
    observed_at: str,
    source: str = "meteora",
    resolution: str | None = None,
) -> list[dict[str, Any]]:
    rows = extract_rows(payload)
    out: list[dict[str, Any]] = []

    for row in rows:
        candle_time = normalize_timestamp(
            first(row, "time", "timestamp", "ts", "date", "bucket_start", "start_time")
        )
        if candle_time is None:
            continue

        out.append(
            {
                "pool_address": pool_address,
                "source": source,
                "candle_time": candle_time,
                "resolution": str(first(row, "resolution", "interval", "timeframe") or resolution or ""),
                "open": number(first(row, "open", "o")),
                "high": number(first(row, "high", "h")),
                "low": number(first(row, "low", "l")),
                "close": number(first(row, "close", "c", "price")),
                "volume": number(first(row, "volume", "v", "volume_usd", "trade_volume")),
                "observed_at": observed_at,
                "raw": row,
            }
        )

    return out


def normalize_volume_history(
    pool_address: str,
    payload: Any,
    *,
    observed_at: str,
    source: str = "meteora",
) -> list[dict[str, Any]]:
    rows = extract_rows(payload)
    out: list[dict[str, Any]] = []

    for row in rows:
        bucket_time = normalize_timestamp(
            first(row, "time", "timestamp", "ts", "date", "bucket_start", "start_time")
        )
        if bucket_time is None:
            continue

        out.append(
            {
                "pool_address": pool_address,
                "source": source,
                "bucket_time": bucket_time,
                "volume": number(first(row, "volume", "volume_usd", "trade_volume", "amount")),
                "fees": number(first(row, "fees", "fee", "fees_usd", "fee_usd")),
                "observed_at": observed_at,
                "raw": row,
            }
        )

    return out
