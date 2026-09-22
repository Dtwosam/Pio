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


def normalize_position_history(
    payload: Any,
    *,
    observed_at: str,
) -> list[dict[str, Any]]:
    """
    Normalize Meteora GetPositionHistoricalEventsResponse.

    API token amount fields remain strings because the public schema does not
    guarantee atomic-vs-UI units. Transaction signature + instruction index are
    preserved for later on-chain event reconciliation.
    """
    if not isinstance(payload, dict):
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []

    required = (
        "signature",
        "ixIndex",
        "eventType",
        "positionAddress",
        "blockTime",
        "slot",
        "poolAddress",
        "userAddress",
        "tokenX",
        "tokenY",
        "amountX",
        "amountY",
        "amountXUsd",
        "amountYUsd",
        "totalUsd",
        "createdAt",
    )

    out: list[dict[str, Any]] = []
    for row in events:
        if not isinstance(row, dict):
            continue
        if any(row.get(key) is None for key in required):
            continue

        try:
            ix_index = int(row["ixIndex"])
            block_time = int(row["blockTime"])
            slot = int(row["slot"])
        except (TypeError, ValueError):
            continue

        out.append(
            {
                "observed_at": observed_at,
                "position_address": str(row["positionAddress"]),
                "signature": str(row["signature"]),
                "ix_index": ix_index,
                "event_type": str(row["eventType"]),
                "block_time": block_time,
                "slot": slot,
                "pool_address": str(row["poolAddress"]),
                "user_address": str(row["userAddress"]),
                "token_x": str(row["tokenX"]),
                "token_y": str(row["tokenY"]),
                "amount_x": str(row["amountX"]),
                "amount_y": str(row["amountY"]),
                "amount_x_usd": str(row["amountXUsd"]),
                "amount_y_usd": str(row["amountYUsd"]),
                "total_usd": str(row["totalUsd"]),
                "created_at": str(row["createdAt"]),
                "raw": row,
            }
        )

    return out

