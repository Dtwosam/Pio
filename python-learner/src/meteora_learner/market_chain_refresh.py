from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .chain_ingest import ingest_chain_snapshot
from .paper_chain_refresh import inspect_pool_with_rust
from .storage import Storage


InspectPool = Callable[[str, int], dict[str, Any]]


@dataclass(frozen=True)
class MarketChainRefreshCandidate:
    pool_address: str
    first_api_observed_at: str
    latest_api_observed_at: str
    chain_observations: int
    last_chain_observed_at: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketChainRefreshQueue:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    discovered_pools: int
    chain_observed_pools: int
    refreshable_pools: int
    batch_limit: int
    candidates: tuple[MarketChainRefreshCandidate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketChainRefreshItem:
    pool_address: str
    previous_chain_observed_at: str
    status: str
    new_observed_at: str | None
    bin_arrays: int | None
    bins: int | None
    error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketChainRefreshReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    queue_before: MarketChainRefreshQueue
    pools_attempted: int
    pools_refreshed: int
    pools_failed: int
    items: tuple[MarketChainRefreshItem, ...]
    queue_after: MarketChainRefreshQueue

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_market_chain_refresh_queue(
    storage: Storage,
    *,
    batch_limit: int = 10,
    exclude_last_observed_at: str | None = None,
) -> MarketChainRefreshQueue:
    """
    Queue already-observed discovered pools for neutral longitudinal refresh.

    Ordering is oldest persisted chain observation first. Economic fields such
    as TVL, volume, fees, APR or model predictions are deliberately absent from
    the ordering.
    """
    if batch_limit < 1:
        raise ValueError("batch_limit must be positive")

    with storage.connect() as conn:
        totals = conn.execute(
            """
            WITH api AS (
                SELECT DISTINCT address
                FROM pool_snapshots
                WHERE address IS NOT NULL
                  AND TRIM(address) != ''
            ),
            chain AS (
                SELECT DISTINCT pool_address
                FROM chain_pool_snapshots
                WHERE pool_address IS NOT NULL
                  AND TRIM(pool_address) != ''
            )
            SELECT
                (SELECT COUNT(*) FROM api),
                (
                    SELECT COUNT(*)
                    FROM api
                    JOIN chain ON chain.pool_address = api.address
                )
            """
        ).fetchone()

        params: list[Any] = []
        exclusion = ""
        if exclude_last_observed_at is not None:
            exclusion = (
                "AND julianday(chain.last_chain_observed_at) "
                "< julianday(?)"
            )
            params.append(exclude_last_observed_at)
        params.append(batch_limit)

        rows = conn.execute(
            f"""
            WITH api AS (
                SELECT
                    address AS pool_address,
                    MIN(observed_at) AS first_api_observed_at,
                    MAX(observed_at) AS latest_api_observed_at
                FROM pool_snapshots
                WHERE address IS NOT NULL
                  AND TRIM(address) != ''
                GROUP BY address
            ),
            chain AS (
                SELECT
                    pool_address,
                    COUNT(*) AS chain_observations,
                    MAX(observed_at) AS last_chain_observed_at
                FROM chain_pool_snapshots
                WHERE pool_address IS NOT NULL
                  AND TRIM(pool_address) != ''
                GROUP BY pool_address
            )
            SELECT
                api.pool_address,
                api.first_api_observed_at,
                api.latest_api_observed_at,
                chain.chain_observations,
                chain.last_chain_observed_at
            FROM api
            JOIN chain
              ON chain.pool_address = api.pool_address
            WHERE 1 = 1
              {exclusion}
            ORDER BY
                julianday(chain.last_chain_observed_at) ASC,
                julianday(api.first_api_observed_at) ASC,
                api.pool_address ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

        refreshable = conn.execute(
            """
            WITH api AS (
                SELECT DISTINCT address AS pool_address
                FROM pool_snapshots
                WHERE address IS NOT NULL
                  AND TRIM(address) != ''
            ),
            chain AS (
                SELECT DISTINCT pool_address
                FROM chain_pool_snapshots
                WHERE pool_address IS NOT NULL
                  AND TRIM(pool_address) != ''
            )
            SELECT COUNT(*)
            FROM api
            JOIN chain
              ON chain.pool_address = api.pool_address
            """
        ).fetchone()

    discovered = int(totals[0]) if totals is not None else 0
    chain_observed = int(totals[1]) if totals is not None else 0
    candidates = tuple(
        MarketChainRefreshCandidate(
            pool_address=str(row[0]),
            first_api_observed_at=str(row[1]),
            latest_api_observed_at=str(row[2]),
            chain_observations=int(row[3]),
            last_chain_observed_at=str(row[4]),
        )
        for row in rows
    )
    return MarketChainRefreshQueue(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        discovered_pools=discovered,
        chain_observed_pools=chain_observed,
        refreshable_pools=(
            int(refreshable[0]) if refreshable is not None else 0
        ),
        batch_limit=batch_limit,
        candidates=candidates,
    )


def _latest_chain_observed_at(
    storage: Storage,
    pool_address: str,
) -> str:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT observed_at
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (pool_address,),
        ).fetchone()
    if row is None:
        raise ValueError(
            "refreshed pool snapshot was not persisted"
        )
    return str(row[0])


def run_market_chain_refresh(
    storage: Storage,
    *,
    batch_limit: int = 10,
    bin_array_radius: int = 0,
    inspector: InspectPool | None = None,
    rust_manifest_path: str | None = None,
    rust_binary_path: str | None = None,
    timeout_seconds: int = 120,
    ingest_observed_at: str | None = None,
) -> MarketChainRefreshReport:
    """
    Refresh one neutral batch of already-observed market pools.

    This performs RPC reads plus local evidence ingestion only. It does not
    construct, sign or submit Solana transactions.
    """
    if bin_array_radius < 0:
        raise ValueError("bin_array_radius cannot be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    before = build_market_chain_refresh_queue(
        storage,
        batch_limit=batch_limit,
        exclude_last_observed_at=ingest_observed_at,
    )

    if inspector is None:
        def inspect(pool: str, radius: int) -> dict[str, Any]:
            return inspect_pool_with_rust(
                pool,
                radius,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
            )
    else:
        inspect = inspector

    items: list[MarketChainRefreshItem] = []
    refreshed = 0
    failed = 0
    for candidate in before.candidates:
        try:
            payload = inspect(
                candidate.pool_address,
                bin_array_radius,
            )
            if str(payload.get("pool_address", "")).strip() != (
                candidate.pool_address
            ):
                raise ValueError(
                    "Rust inspector returned a different pool_address"
                )
            result = ingest_chain_snapshot(
                storage,
                payload,
                observed_at=ingest_observed_at,
            )
            refreshed += 1
            items.append(
                MarketChainRefreshItem(
                    pool_address=candidate.pool_address,
                    previous_chain_observed_at=(
                        candidate.last_chain_observed_at
                    ),
                    status="REFRESHED",
                    new_observed_at=_latest_chain_observed_at(
                        storage,
                        candidate.pool_address,
                    ),
                    bin_arrays=result.bin_arrays,
                    bins=result.bins,
                    error=None,
                )
            )
        except Exception as exc:
            failed += 1
            items.append(
                MarketChainRefreshItem(
                    pool_address=candidate.pool_address,
                    previous_chain_observed_at=(
                        candidate.last_chain_observed_at
                    ),
                    status="FAILED",
                    new_observed_at=None,
                    bin_arrays=None,
                    bins=None,
                    error=str(exc)[:2000],
                )
            )

    after = build_market_chain_refresh_queue(
        storage,
        batch_limit=batch_limit,
        exclude_last_observed_at=ingest_observed_at,
    )
    return MarketChainRefreshReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        queue_before=before,
        pools_attempted=len(before.candidates),
        pools_refreshed=refreshed,
        pools_failed=failed,
        items=tuple(items),
        queue_after=after,
    )
