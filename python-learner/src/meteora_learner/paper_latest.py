from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Any, Sequence

from .paper_account import paper_position_snapshot
from .paper_live import (
    LivePaperChainBatchItem,
    run_live_chain_paper_batch,
)
from .paper_runner import PaperBatchRunReport
from .pool_safety import PoolSafetyConfig
from .position_policy import PositionManagementConfig
from .research_store import ResearchStore
from .storage import Storage


@dataclass(frozen=True)
class LatestPaperCycleItem:
    position_id: str
    token_y_quote_per_atomic: float
    quote_max_age_seconds: int = 300
    emergency_exit: bool = False
    estimated_exit_cost_quote: float = 0.0
    rebalance_cost_quote: float | None = None

    def __post_init__(self) -> None:
        if not self.position_id.strip():
            raise ValueError("position_id is required")
        if self.token_y_quote_per_atomic <= 0:
            raise ValueError("token_y_quote_per_atomic must be positive")
        if self.quote_max_age_seconds < 0:
            raise ValueError("quote_max_age_seconds cannot be negative")
        if self.estimated_exit_cost_quote < 0:
            raise ValueError("estimated_exit_cost_quote cannot be negative")
        if self.rebalance_cost_quote is not None and self.rebalance_cost_quote < 0:
            raise ValueError("rebalance_cost_quote cannot be negative")


@dataclass(frozen=True)
class LatestPaperCycleGroup:
    observed_at: str
    run_id: str
    positions: tuple[str, ...]
    report: PaperBatchRunReport


@dataclass(frozen=True)
class LatestPaperCycleReport:
    cycle_id: str
    positions_requested: int
    groups: int
    applied: int
    failed: int
    details: tuple[LatestPaperCycleGroup, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _latest_observed_at(storage: Storage, pool_address: str) -> str:
    times = ResearchStore(storage.path).chain_observation_times(
        pool_address,
        limit=1,
        ascending=False,
    )
    if not times:
        raise ValueError(f"pool {pool_address} has no chain observations")
    return times[0]


def _group_run_id(cycle_id: str, observed_at: str) -> str:
    digest = hashlib.sha256(observed_at.encode("utf-8")).hexdigest()[:12]
    return f"{cycle_id}:{digest}"


def run_latest_live_paper_cycle(
    storage: Storage,
    *,
    cycle_id: str,
    items: Sequence[LatestPaperCycleItem],
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
    management_config: PositionManagementConfig = PositionManagementConfig(),
    retry_failed: bool = False,
) -> LatestPaperCycleReport:
    """
    Run all requested open paper positions at each pool's latest local chain state.

    Positions are grouped by observed_at so the existing idempotent batch runner
    remains the only mutation path. Reusing cycle_id with unchanged latest chain
    states is idempotent.
    """
    if not cycle_id.strip():
        raise ValueError("cycle_id is required")
    if not items:
        raise ValueError("at least one latest paper cycle item is required")

    ordered = tuple(sorted(items, key=lambda item: item.position_id))
    if len({item.position_id for item in ordered}) != len(ordered):
        raise ValueError("latest paper cycle contains duplicate position_id values")

    grouped: dict[str, list[LatestPaperCycleItem]] = {}
    for item in ordered:
        position = paper_position_snapshot(storage, position_id=item.position_id)
        if position.status != "OPEN":
            raise ValueError(f"paper position {item.position_id} is not open")
        observed_at = _latest_observed_at(storage, position.pool_address)
        grouped.setdefault(observed_at, []).append(item)

    details: list[LatestPaperCycleGroup] = []
    for observed_at in sorted(grouped):
        group_items = grouped[observed_at]
        run_id = _group_run_id(cycle_id, observed_at)
        report = run_live_chain_paper_batch(
            storage,
            run_id=run_id,
            observed_at=observed_at,
            items=tuple(
                LivePaperChainBatchItem(
                    position_id=item.position_id,
                    token_y_quote_per_atomic=item.token_y_quote_per_atomic,
                    quote_max_age_seconds=item.quote_max_age_seconds,
                    emergency_exit=item.emergency_exit,
                    estimated_exit_cost_quote=item.estimated_exit_cost_quote,
                    rebalance_cost_quote=item.rebalance_cost_quote,
                )
                for item in group_items
            ),
            safety_config=safety_config,
            management_config=management_config,
            retry_failed=retry_failed,
        )
        details.append(
            LatestPaperCycleGroup(
                observed_at=observed_at,
                run_id=run_id,
                positions=tuple(item.position_id for item in group_items),
                report=report,
            )
        )

    return LatestPaperCycleReport(
        cycle_id=cycle_id,
        positions_requested=len(ordered),
        groups=len(details),
        applied=sum(item.report.items_applied for item in details),
        failed=sum(item.report.items_failed for item in details),
        details=tuple(details),
    )
