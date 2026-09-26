from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .phase9_capture_plan import Phase9ChainCaptureCriteria
from .phase9_chain_capture import (
    Phase9ChainCaptureBatchReport,
    run_phase9_chain_capture_batch,
)
from .storage import Storage


InspectPool = Callable[[str, int], dict[str, Any]]


@dataclass(frozen=True)
class MarketChainContextCandidate:
    pool_address: str
    first_api_observed_at: str
    latest_api_observed_at: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketChainContextQueue:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    discovered_pools: int
    chain_observed_pools: int
    missing_chain_pools: int
    batch_limit: int
    candidates: tuple[MarketChainContextCandidate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketChainContextCaptureReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    queue_before: MarketChainContextQueue
    capture: Phase9ChainCaptureBatchReport | None
    queue_after: MarketChainContextQueue

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_market_chain_context_queue(
    storage: Storage,
    *,
    batch_limit: int = 25,
) -> MarketChainContextQueue:
    """
    Queue discovered pools that have never received a chain snapshot.

    Ordering is based on collection fairness (oldest first discovery, then
    address), not TVL, volume, fees or a hand-written profitability score.
    """
    if batch_limit < 1:
        raise ValueError("batch_limit must be positive")

    with storage.connect() as conn:
        totals = conn.execute(
            """
            SELECT
                COUNT(DISTINCT address) AS discovered,
                (
                    SELECT COUNT(DISTINCT pool_address)
                    FROM chain_pool_snapshots
                    WHERE pool_address IS NOT NULL
                      AND TRIM(pool_address) != ''
                ) AS chain_observed
            FROM pool_snapshots
            WHERE address IS NOT NULL
              AND TRIM(address) != ''
            """
        ).fetchone()

        rows = conn.execute(
            """
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
                SELECT DISTINCT pool_address
                FROM chain_pool_snapshots
                WHERE pool_address IS NOT NULL
                  AND TRIM(pool_address) != ''
            )
            SELECT
                api.pool_address,
                api.first_api_observed_at,
                api.latest_api_observed_at
            FROM api
            LEFT JOIN chain
              ON chain.pool_address = api.pool_address
            WHERE chain.pool_address IS NULL
            ORDER BY
                julianday(api.first_api_observed_at) ASC,
                api.pool_address ASC
            LIMIT ?
            """,
            (batch_limit,),
        ).fetchall()

        missing_row = conn.execute(
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
            LEFT JOIN chain
              ON chain.pool_address = api.pool_address
            WHERE chain.pool_address IS NULL
            """
        ).fetchone()

    discovered = int(totals[0]) if totals is not None else 0
    chain_observed = int(totals[1]) if totals is not None else 0
    missing = int(missing_row[0]) if missing_row is not None else 0

    candidates = tuple(
        MarketChainContextCandidate(
            pool_address=str(row[0]),
            first_api_observed_at=str(row[1]),
            latest_api_observed_at=str(row[2]),
        )
        for row in rows
    )

    return MarketChainContextQueue(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        discovered_pools=discovered,
        chain_observed_pools=chain_observed,
        missing_chain_pools=missing,
        batch_limit=batch_limit,
        candidates=candidates,
    )


def run_market_chain_context_capture(
    storage: Storage,
    *,
    batch_limit: int = 25,
    bin_array_radius: int = 0,
    inspector: InspectPool | None = None,
    rust_manifest_path: str | None = None,
    rust_binary_path: str | None = None,
    timeout_seconds: int = 120,
    ingest_observed_at: str | None = None,
) -> MarketChainContextCaptureReport:
    """
    Capture one neutral batch of missing chain pool context.

    Reuses the existing read-only Phase 9 chain inspector/ingest path. The
    selected pool addresses come only from the neutral missing-context queue.
    """
    if bin_array_radius < 0:
        raise ValueError("bin_array_radius cannot be negative")

    before = build_market_chain_context_queue(
        storage,
        batch_limit=batch_limit,
    )
    if not before.candidates:
        after = build_market_chain_context_queue(
            storage,
            batch_limit=batch_limit,
        )
        return MarketChainContextCaptureReport(
            research_only=True,
            read_only_capture=True,
            policy_actionable=False,
            execution_wired=False,
            queue_before=before,
            capture=None,
            queue_after=after,
        )

    preferred = tuple(
        item.pool_address
        for item in before.candidates
    )
    capture = run_phase9_chain_capture_batch(
        storage,
        criteria=Phase9ChainCaptureCriteria(
            target_chain_pools=1,
            max_candidates=batch_limit,
            bin_array_radius=bin_array_radius,
        ),
        inspector=inspector,
        rust_manifest_path=rust_manifest_path,
        rust_binary_path=rust_binary_path,
        timeout_seconds=timeout_seconds,
        ingest_observed_at=ingest_observed_at,
        preferred_pool_addresses=preferred,
        max_preferred_candidates=batch_limit,
        api_ranking_as_of=None,
    )

    after = build_market_chain_context_queue(
        storage,
        batch_limit=batch_limit,
    )
    return MarketChainContextCaptureReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        queue_before=before,
        capture=capture,
        queue_after=after,
    )
