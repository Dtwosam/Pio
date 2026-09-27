from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .research_store import ResearchStore


@dataclass(frozen=True)
class MarketPaperIntakePool:
    pool_address: str
    market_observed_at: str
    chain_observations_checked: int
    latest_chain_observed_at: str | None
    ready_for_candidate_cycle: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketPaperIntakeReport:
    research_only: bool
    paper_only: bool
    policy_actionable: bool
    execution_wired: bool
    minimum_chain_observations: int
    max_pools: int
    pools_seen: int
    pools_considered: int
    pools_ready: int
    pools: tuple[MarketPaperIntakePool, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "pools": [item.to_record() for item in self.pools],
        }


def _build_from_store(
    store: ResearchStore,
    *,
    minimum_chain_observations: int,
    max_pools: int,
) -> MarketPaperIntakeReport:
    if minimum_chain_observations < 2:
        raise ValueError("minimum_chain_observations must be at least 2")
    if max_pools < 1:
        raise ValueError("max_pools must be positive")

    snapshots = store.latest_pool_snapshots()
    considered = snapshots[:max_pools]
    pools: list[MarketPaperIntakePool] = []

    for snapshot in considered:
        address = str(snapshot["address"])
        times = store.chain_observation_times(
            address,
            limit=minimum_chain_observations,
            ascending=False,
        )
        ready = len(times) >= minimum_chain_observations
        pools.append(
            MarketPaperIntakePool(
                pool_address=address,
                market_observed_at=str(snapshot["observed_at"]),
                chain_observations_checked=len(times),
                latest_chain_observed_at=(times[0] if times else None),
                ready_for_candidate_cycle=ready,
            )
        )

    return MarketPaperIntakeReport(
        research_only=True,
        paper_only=True,
        policy_actionable=False,
        execution_wired=False,
        minimum_chain_observations=minimum_chain_observations,
        max_pools=max_pools,
        pools_seen=len(snapshots),
        pools_considered=len(pools),
        pools_ready=sum(item.ready_for_candidate_cycle for item in pools),
        pools=tuple(pools),
    )


def build_market_paper_intake(
    database_path: str | Path,
    *,
    minimum_chain_observations: int = 12,
    max_pools: int = 500,
) -> MarketPaperIntakeReport:
    """
    Build a neutral intake queue from discovered market pools to PAPER research.

    Pools are kept in the stable address order returned by ResearchStore.
    Readiness means only that enough chain observation timestamps exist for the
    current PAPER candidate window. No pool score, winner, allocation, economic
    threshold, execution authorization, or capital action is produced.
    """
    store = ResearchStore(str(database_path))
    return _build_from_store(
        store,
        minimum_chain_observations=minimum_chain_observations,
        max_pools=max_pools,
    )
