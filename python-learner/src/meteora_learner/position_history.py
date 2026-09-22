from __future__ import annotations

from dataclasses import dataclass

from .meteora_api import MeteoraDataAPI
from .position_normalization import normalize_position_history
from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class PositionHistoryCollectionResult:
    position_address: str
    events: int
    observed_at: str


def collect_position_history(
    storage: Storage,
    api: MeteoraDataAPI,
    position_address: str,
) -> PositionHistoryCollectionResult:
    if not position_address:
        raise ValueError("position_address is required")

    observed_at = utc_now_iso()
    payload = api.position_history(
        position_address,
        order_direction="asc",
    )
    storage.save_raw(
        f"/positions/{position_address}/historical",
        payload,
        observed_at=observed_at,
        entity_key=position_address,
    )
    events = normalize_position_history(
        payload,
        observed_at=observed_at,
    )
    if any(item["position_address"] != position_address for item in events):
        raise ValueError("position history response contains a different position address")
    saved = storage.save_position_events(events)
    return PositionHistoryCollectionResult(
        position_address=position_address,
        events=saved,
        observed_at=observed_at,
    )
