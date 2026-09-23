from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .meteora_api import MeteoraDataAPI
from .settings import Settings
from .storage import Storage, utc_now_iso


PoolFetcher = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class PaperMarketRefreshItem:
    pool_address: str
    status: str
    error: str | None


@dataclass(frozen=True)
class PaperMarketRefreshReport:
    account_id: str | None
    observed_at: str
    pools_requested: int
    pools_refreshed: int
    pools_failed: int
    items: tuple[PaperMarketRefreshItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _open_paper_pools(
    storage: Storage,
    *,
    account_id: str | None,
) -> tuple[str, ...]:
    params: tuple[Any, ...] = ()
    clause = ""
    if account_id is not None:
        if not account_id.strip():
            raise ValueError("account_id cannot be blank")
        clause = "AND account_id = ?"
        params = (account_id,)
    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT pool_address
            FROM paper_positions
            WHERE status = 'OPEN'
            {clause}
            ORDER BY pool_address ASC
            """,
            params,
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def refresh_paper_market_state(
    storage: Storage,
    *,
    account_id: str | None = None,
    observed_at: str | None = None,
    settings: Settings | None = None,
    fetch_pool: PoolFetcher | None = None,
) -> PaperMarketRefreshReport:
    """
    Refresh normalized Meteora Data API metadata for pools behind open paper positions.

    Failures are isolated per pool. No chain state or quote is synthesized here.
    """
    timestamp = observed_at or utc_now_iso()
    pools = _open_paper_pools(storage, account_id=account_id)
    items: list[PaperMarketRefreshItem] = []
    refreshed = 0
    failed = 0

    if fetch_pool is not None:
        def fetch(address: str) -> dict[str, Any]:
            return fetch_pool(address)

        for pool in pools:
            try:
                payload = fetch(pool)
                if not isinstance(payload, dict):
                    raise ValueError("pool detail must be a JSON object")
                storage.save_raw(
                    f"/pools/{pool}",
                    payload,
                    observed_at=timestamp,
                    entity_key=pool,
                )
                storage.save_pool_snapshot(payload, observed_at=timestamp)
                refreshed += 1
                items.append(PaperMarketRefreshItem(pool, "REFRESHED", None))
            except Exception as exc:
                failed += 1
                items.append(
                    PaperMarketRefreshItem(
                        pool,
                        "FAILED",
                        str(exc)[:2000],
                    )
                )
    elif pools:
        cfg = settings or Settings.from_env()
        with MeteoraDataAPI(
            base_url=cfg.meteora_data_api,
            requests_per_second=cfg.requests_per_second,
        ) as api:
            for pool in pools:
                try:
                    payload = api.pool(pool)
                    if not isinstance(payload, dict):
                        raise ValueError("pool detail must be a JSON object")
                    storage.save_raw(
                        f"/pools/{pool}",
                        payload,
                        observed_at=timestamp,
                        entity_key=pool,
                    )
                    storage.save_pool_snapshot(payload, observed_at=timestamp)
                    refreshed += 1
                    items.append(PaperMarketRefreshItem(pool, "REFRESHED", None))
                except Exception as exc:
                    failed += 1
                    items.append(
                        PaperMarketRefreshItem(
                            pool,
                            "FAILED",
                            str(exc)[:2000],
                        )
                    )

    return PaperMarketRefreshReport(
        account_id=account_id,
        observed_at=timestamp,
        pools_requested=len(pools),
        pools_refreshed=refreshed,
        pools_failed=failed,
        items=tuple(items),
    )
