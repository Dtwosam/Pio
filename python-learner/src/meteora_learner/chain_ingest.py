from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class ChainIngestResult:
    pool_address: str
    bin_arrays: int
    bins: int


def ingest_chain_snapshot(
    storage: Storage,
    payload: Any,
    *,
    observed_at: str | None = None,
) -> ChainIngestResult:
    if not isinstance(payload, dict):
        raise ValueError("chain snapshot must be a JSON object")

    required = {
        "pool_address",
        "active_bin_id",
        "bin_step",
        "token_x_mint",
        "token_y_mint",
        "bin_arrays",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"chain snapshot missing fields: {missing}")

    if int(payload["bin_step"]) <= 0:
        raise ValueError("bin_step must be positive")

    arrays, bins = storage.save_chain_pool_snapshot(payload, observed_at=observed_at)
    return ChainIngestResult(
        pool_address=str(payload["pool_address"]),
        bin_arrays=arrays,
        bins=bins,
    )
