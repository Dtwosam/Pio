from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .collector import extract_pool_rows, pool_address
from .meteora_api import MeteoraDataAPI
from .settings import Settings
from .storage import Storage, utc_now_iso


PageFetcher = Callable[[int, int], Any]


@dataclass(frozen=True)
class PoolUniverseDiscoveryReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    observed_at: str
    sort_by: str | None
    page_size: int
    max_pages: int
    pages_fetched: int
    raw_rows_seen: int
    unique_pools_seen: int
    snapshots_saved: int
    duplicate_rows: int
    invalid_rows: int
    stop_reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _discover_with_fetcher(
    storage: Storage,
    *,
    fetch_page: PageFetcher,
    observed_at: str,
    sort_by: str | None,
    page_size: int,
    max_pages: int,
) -> PoolUniverseDiscoveryReport:
    seen: set[str] = set()
    pages_fetched = 0
    raw_rows_seen = 0
    snapshots_saved = 0
    duplicate_rows = 0
    invalid_rows = 0
    stop_reason = "MAX_PAGES"

    for page in range(1, max_pages + 1):
        payload = fetch_page(page, page_size)
        pages_fetched += 1

        storage.save_raw(
            "/pools",
            payload,
            observed_at=observed_at,
            entity_key=f"universe:page:{page}",
        )

        rows = extract_pool_rows(payload)
        raw_rows_seen += len(rows)

        if not rows:
            stop_reason = "EMPTY_PAGE"
            break

        new_on_page = 0
        for row in rows:
            address = pool_address(row)
            if not address:
                invalid_rows += 1
                continue
            if address in seen:
                duplicate_rows += 1
                continue

            seen.add(address)
            new_on_page += 1
            if storage.save_pool_snapshot(
                row,
                observed_at=observed_at,
            ):
                snapshots_saved += 1
            else:
                invalid_rows += 1

        if len(rows) < page_size:
            stop_reason = "SHORT_PAGE"
            break
        if new_on_page == 0:
            stop_reason = "NO_NEW_POOLS"
            break

    return PoolUniverseDiscoveryReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        observed_at=observed_at,
        sort_by=sort_by,
        page_size=page_size,
        max_pages=max_pages,
        pages_fetched=pages_fetched,
        raw_rows_seen=raw_rows_seen,
        unique_pools_seen=len(seen),
        snapshots_saved=snapshots_saved,
        duplicate_rows=duplicate_rows,
        invalid_rows=invalid_rows,
        stop_reason=stop_reason,
    )


def discover_pool_universe(
    storage: Storage,
    *,
    observed_at: str | None = None,
    settings: Settings | None = None,
    fetch_page: PageFetcher | None = None,
    page_size: int = 1000,
    max_pages: int = 100,
    sort_by: str | None = "tvl:desc",
) -> PoolUniverseDiscoveryReport:
    """
    Capture the broad Meteora pool universe for research.

    Pagination continues until the source is exhausted, a page repeats no new
    pool addresses, or max_pages is reached. The routine only captures and
    normalizes observations. It deliberately does not apply economic filters,
    risk cutoffs, ranking weights, capital sizing, or execution decisions.
    """
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size must be between 1 and 1000")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    if sort_by is not None and ":" not in sort_by:
        raise ValueError(
            "sort_by must use Meteora format '<field>:<asc|desc>'"
        )

    timestamp = observed_at or utc_now_iso()

    if fetch_page is not None:
        return _discover_with_fetcher(
            storage,
            fetch_page=fetch_page,
            observed_at=timestamp,
            sort_by=sort_by,
            page_size=page_size,
            max_pages=max_pages,
        )

    cfg = settings or Settings.from_env()
    with MeteoraDataAPI(
        base_url=cfg.meteora_data_api,
        requests_per_second=cfg.requests_per_second,
    ) as api:
        def api_fetch(page: int, size: int) -> Any:
            return api.pools(
                page=page,
                page_size=size,
                sort_by=sort_by,
            )

        return _discover_with_fetcher(
            storage,
            fetch_page=api_fetch,
            observed_at=timestamp,
            sort_by=sort_by,
            page_size=page_size,
            max_pages=max_pages,
        )
