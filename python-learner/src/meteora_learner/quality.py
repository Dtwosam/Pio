from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def assess_candles(
    candles: list[dict[str, Any]],
    *,
    checked_at: str,
    max_age_seconds: int,
    gap_multiplier: float = 3.0,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    if not candles:
        return [
            {
                "check_name": "non_empty",
                "status": "FAIL",
                "value": 0.0,
                "detail": "No normalized OHLCV candles were returned.",
            }
        ]

    checks.append(
        {
            "check_name": "non_empty",
            "status": "PASS",
            "value": float(len(candles)),
            "detail": f"{len(candles)} normalized candles.",
        }
    )

    times = [_parse_iso(str(row["candle_time"])) for row in candles]
    sorted_times = sorted(times)
    duplicate_count = len(times) - len(set(times))
    checks.append(
        {
            "check_name": "duplicate_timestamps",
            "status": "PASS" if duplicate_count == 0 else "FAIL",
            "value": float(duplicate_count),
            "detail": f"{duplicate_count} duplicate candle timestamps.",
        }
    )

    checked = _parse_iso(checked_at)
    age_seconds = max(0.0, (checked - sorted_times[-1]).total_seconds())
    checks.append(
        {
            "check_name": "freshness",
            "status": "PASS" if age_seconds <= max_age_seconds else "FAIL",
            "value": age_seconds,
            "detail": f"Latest candle is {age_seconds:.0f}s old; limit is {max_age_seconds}s.",
        }
    )

    if len(sorted_times) >= 3:
        intervals = [
            (right - left).total_seconds()
            for left, right in zip(sorted_times, sorted_times[1:])
            if right > left
        ]
        if intervals:
            typical = median(intervals)
            max_gap = max(intervals)
            gap_ok = typical <= 0 or max_gap <= typical * gap_multiplier
            checks.append(
                {
                    "check_name": "time_gaps",
                    "status": "PASS" if gap_ok else "FAIL",
                    "value": max_gap,
                    "detail": f"Median interval {typical:.0f}s; max gap {max_gap:.0f}s.",
                }
            )

    invalid_ohlc = 0
    for row in candles:
        o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
        if None in (o, h, l, c):
            invalid_ohlc += 1
            continue
        if h < max(o, c) or l > min(o, c) or h < l:
            invalid_ohlc += 1

    checks.append(
        {
            "check_name": "ohlc_consistency",
            "status": "PASS" if invalid_ohlc == 0 else "FAIL",
            "value": float(invalid_ohlc),
            "detail": f"{invalid_ohlc} candles have missing or inconsistent OHLC values.",
        }
    )
    return checks
