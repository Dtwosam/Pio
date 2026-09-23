from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class MintIngestResult:
    mint_address: str
    snapshot_id: int


def ingest_mint_snapshot(
    storage: Storage,
    payload: Any,
    *,
    observed_at: str | None = None,
) -> MintIngestResult:
    if not isinstance(payload, dict):
        raise ValueError("mint snapshot must be a JSON object")
    snapshot_id = storage.save_token_mint_snapshot(
        payload,
        observed_at=observed_at,
    )
    return MintIngestResult(
        mint_address=str(payload["mint_address"]),
        snapshot_id=snapshot_id,
    )
