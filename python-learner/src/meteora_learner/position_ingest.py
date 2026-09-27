from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class PositionIngestResult:
    position_address: str
    pool_address: str
    bins: int


def ingest_position_snapshot(
    storage: Storage,
    payload: Any,
    *,
    observed_at: str | None = None,
) -> PositionIngestResult:
    if not isinstance(payload, dict):
        raise ValueError("position snapshot must be a JSON object")

    required = {
        "position_address",
        "pool_address",
        "owner",
        "fee_owner",
        "lower_bin_id",
        "upper_bin_id",
        "total_x_amount",
        "total_y_amount",
        "fee_x",
        "fee_y",
        "reward_one",
        "reward_two",
        "last_updated_at",
        "total_claimed_fee_x_amount",
        "total_claimed_fee_y_amount",
        "bins",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"position snapshot missing fields: {missing}")

    if int(payload["lower_bin_id"]) > int(payload["upper_bin_id"]):
        raise ValueError("position lower_bin_id cannot exceed upper_bin_id")

    capture_start = payload.get("capture_slot_start")
    capture_end = payload.get("capture_slot_end")
    if (capture_start is None) != (capture_end is None):
        raise ValueError(
            "position capture slots must be supplied together"
        )
    if capture_start is not None:
        start = int(capture_start)
        end = int(capture_end)
        if start < 0 or end < 0:
            raise ValueError("position capture slots cannot be negative")
        if start > end:
            raise ValueError(
                "position capture_slot_start cannot exceed capture_slot_end"
            )

    bins = storage.save_chain_position_snapshot(payload, observed_at=observed_at)
    return PositionIngestResult(
        position_address=str(payload["position_address"]),
        pool_address=str(payload["pool_address"]),
        bins=bins,
    )
