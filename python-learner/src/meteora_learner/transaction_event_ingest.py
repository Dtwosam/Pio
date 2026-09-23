from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class TransactionEventIngestResult:
    signature: str
    events: int


def ingest_transaction_events(
    storage: Storage,
    payload: Any,
    *,
    observed_at: str | None = None,
) -> TransactionEventIngestResult:
    if not isinstance(payload, dict):
        raise ValueError("transaction event snapshot must be a JSON object")

    required = {"signature", "slot", "events"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"transaction event snapshot missing fields: {missing}")

    events = storage.save_chain_transaction_events(
        payload,
        observed_at=observed_at,
    )
    return TransactionEventIngestResult(
        signature=str(payload["signature"]),
        events=events,
    )
