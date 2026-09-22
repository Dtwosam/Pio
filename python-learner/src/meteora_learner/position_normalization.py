from __future__ import annotations

from typing import Any


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pair_total_usd(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    total = value.get("total")
    if not isinstance(total, dict):
        return None
    return _float(total.get("usd"))


def normalize_position_pnl(payload: Any) -> list[dict[str, Any]]:
    """
    Normalize Meteora GetPoolPositionPnLResponse into validation-friendly rows.
    """
    if not isinstance(payload, dict):
        return []
    positions = payload.get("positions")
    if not isinstance(positions, list):
        return []

    out: list[dict[str, Any]] = []
    for row in positions:
        if not isinstance(row, dict) or not row.get("positionAddress"):
            continue

        out.append(
            {
                "position_address": str(row["positionAddress"]),
                "lower_bin_id": row.get("lowerBinId"),
                "upper_bin_id": row.get("upperBinId"),
                "min_price": _float(row.get("minPrice")),
                "max_price": _float(row.get("maxPrice")),
                "created_at": row.get("createdAt"),
                "closed_at": row.get("closedAt"),
                "is_closed": bool(row.get("isClosed")),
                "is_out_of_range": row.get("isOutOfRange"),
                "pnl_usd": _float(row.get("pnlUsd")),
                "pnl_pct_change": _float(row.get("pnlPctChange")),
                "fee_per_tvl_24h": _float(row.get("feePerTvl24h")),
                "deposits_usd": _pair_total_usd(row.get("allTimeDeposits")),
                "withdrawals_usd": _pair_total_usd(row.get("allTimeWithdrawals")),
                "fees_usd": _pair_total_usd(row.get("allTimeFees")),
                "pool_active_bin_id": row.get("poolActiveBinId"),
                "pool_active_price": _float(row.get("poolActivePrice")),
            }
        )
    return out
