from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .market_chain_context import (
    InspectPool,
    MarketChainContextCaptureReport,
    run_market_chain_context_capture,
)
from .market_chain_refresh import (
    MarketChainRefreshReport,
    run_market_chain_refresh,
)
from .market_mint_context import (
    InspectMint,
    MarketMintContextCaptureReport,
    run_market_mint_context_capture,
)
from .market_pool_universe import (
    PageFetcher,
    PoolUniverseDiscoveryReport,
    discover_pool_universe,
)
from .settings import Settings
from .storage import Storage


@dataclass(frozen=True)
class MarketContextCollectionCycleReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    status: str
    discovery: PoolUniverseDiscoveryReport | None
    chain_context: MarketChainContextCaptureReport | None
    chain_refresh: MarketChainRefreshReport | None
    mint_context: MarketMintContextCaptureReport | None
    pools_discovered: int
    chain_pools_captured: int
    chain_pools_failed: int
    chain_pools_refreshed: int
    chain_refresh_failed: int
    mints_captured: int
    mints_failed: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def run_market_context_collection_cycle(
    storage: Storage,
    *,
    capture_universe: bool = True,
    capture_chain_context: bool = True,
    refresh_chain_context: bool = False,
    capture_mint_context: bool = True,
    observed_at: str | None = None,
    settings: Settings | None = None,
    fetch_page: PageFetcher | None = None,
    pool_inspector: InspectPool | None = None,
    mint_inspector: InspectMint | None = None,
    page_size: int = 1000,
    max_pages: int = 100,
    chain_batch_limit: int = 10,
    chain_refresh_batch_limit: int = 10,
    mint_batch_limit: int = 20,
    bin_array_radius: int = 0,
    rust_manifest_path: str | None = None,
    rust_binary_path: str | None = None,
    timeout_seconds: int = 120,
) -> MarketContextCollectionCycleReport:
    """
    Run one read-only market context collection cycle.

    Ordering is intentional:
      1. discover/refresh the broad pool universe;
      2. capture a neutral batch of missing chain pool state;
      3. capture mints newly exposed by chain pool state.

    The cycle collects evidence only. It performs no pool ranking, allocation,
    transaction construction, signing or submission.
    """
    if not (
        capture_universe
        or capture_chain_context
        or capture_mint_context
    ):
        raise ValueError(
            "at least one context collection stage must be enabled"
        )
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    discovery: PoolUniverseDiscoveryReport | None = None
    chain_context: MarketChainContextCaptureReport | None = None
    chain_refresh: MarketChainRefreshReport | None = None
    mint_context: MarketMintContextCaptureReport | None = None

    if capture_universe:
        discovery = discover_pool_universe(
            storage,
            observed_at=observed_at,
            settings=settings,
            fetch_page=fetch_page,
            page_size=page_size,
            max_pages=max_pages,
        )

    if refresh_chain_context:
        chain_refresh = run_market_chain_refresh(
            storage,
            batch_limit=chain_refresh_batch_limit,
            bin_array_radius=bin_array_radius,
            inspector=pool_inspector,
            rust_manifest_path=rust_manifest_path,
            rust_binary_path=rust_binary_path,
            timeout_seconds=timeout_seconds,
            ingest_observed_at=observed_at,
        )

    if capture_chain_context:
        chain_context = run_market_chain_context_capture(
            storage,
            batch_limit=chain_batch_limit,
            bin_array_radius=bin_array_radius,
            inspector=pool_inspector,
            rust_manifest_path=rust_manifest_path,
            rust_binary_path=rust_binary_path,
            timeout_seconds=timeout_seconds,
            ingest_observed_at=observed_at,
        )

    if capture_mint_context:
        mint_context = run_market_mint_context_capture(
            storage,
            batch_limit=mint_batch_limit,
            inspector=mint_inspector,
            rust_manifest_path=rust_manifest_path,
            rust_binary_path=rust_binary_path,
            timeout_seconds=timeout_seconds,
            observed_at=observed_at,
        )

    pools_discovered = (
        discovery.unique_pools_seen
        if discovery is not None
        else 0
    )
    chain_captured = (
        chain_context.capture.pools_captured
        if (
            chain_context is not None
            and chain_context.capture is not None
        )
        else 0
    )
    chain_failed = (
        chain_context.capture.pools_failed
        if (
            chain_context is not None
            and chain_context.capture is not None
        )
        else 0
    )
    chain_refreshed = (
        chain_refresh.pools_refreshed
        if chain_refresh is not None
        else 0
    )
    chain_refresh_failed = (
        chain_refresh.pools_failed
        if chain_refresh is not None
        else 0
    )
    mints_captured = (
        mint_context.captured
        if mint_context is not None
        else 0
    )
    mints_failed = (
        mint_context.failed
        if mint_context is not None
        else 0
    )

    if chain_failed or chain_refresh_failed or mints_failed:
        status = "PARTIAL"
    elif chain_captured or chain_refreshed or mints_captured:
        status = "CAPTURED"
    elif discovery is not None and discovery.snapshots_saved:
        status = "DISCOVERED"
    else:
        status = "NO_CHANGE"

    return MarketContextCollectionCycleReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        status=status,
        discovery=discovery,
        chain_context=chain_context,
        chain_refresh=chain_refresh,
        mint_context=mint_context,
        pools_discovered=pools_discovered,
        chain_pools_captured=chain_captured,
        chain_pools_failed=chain_failed,
        chain_pools_refreshed=chain_refreshed,
        chain_refresh_failed=chain_refresh_failed,
        mints_captured=mints_captured,
        mints_failed=mints_failed,
    )
