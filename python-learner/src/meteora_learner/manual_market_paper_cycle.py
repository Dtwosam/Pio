from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .market_paper_exploration import (
    MarketPaperExplorationReport,
    run_market_paper_exploration,
)
from .market_research_cycle import (
    MarketResearchCycleReport,
    run_market_research_cycle,
)
from .paper_scheduler import (
    ScheduledPaperTickReport,
    run_scheduled_paper_tick,
)
from .settings import Settings
from .storage import Storage, utc_now_iso


ResearchRunner = Callable[..., MarketResearchCycleReport]
SchedulerRunner = Callable[..., ScheduledPaperTickReport]
ExplorationRunner = Callable[..., MarketPaperExplorationReport]

BLOCKING_SCHEDULER_STATUSES = {
    "BUSY",
    "FAILED",
    "MARKET_REFRESH_FAILED",
    "PARTIAL",
}


@dataclass(frozen=True)
class ManualMarketPaperCycleReport:
    account_id: str
    run_id: str
    observed_at: str
    status: str
    manual_only: bool
    paper_only: bool
    policy_actionable: bool
    live_authorized: bool
    research: MarketResearchCycleReport | None
    scheduler: ScheduledPaperTickReport | None
    exploration: MarketPaperExplorationReport | None
    new_entries_skipped_reason: str | None
    new_positions_pending_next_tick: int

    def to_record(self) -> dict[str, Any]:
        research_record = None
        if self.research is not None:
            research_record = {
                "status": self.research.status,
                "pools_ready": (
                    self.research.intake.pools_ready
                    if self.research.intake is not None
                    else None
                ),
                "stages": [
                    item.to_record()
                    for item in self.research.stages
                ],
            }

        scheduler_record = None
        if self.scheduler is not None:
            scheduler_record = {
                "status": self.scheduler.status,
                "tick_id": self.scheduler.tick_id,
                "lease_acquired": self.scheduler.lease_acquired,
                "recovered_stale_lease": (
                    self.scheduler.recovered_stale_lease
                ),
            }

        exploration_record = None
        if self.exploration is not None:
            exploration_record = {
                "pools_ready": self.exploration.pools_ready,
                "pools_considered": self.exploration.pools_considered,
                "positions_opened": self.exploration.positions_opened,
                "max_new_positions": self.exploration.max_new_positions,
                "items": [
                    item.to_record()
                    for item in self.exploration.items
                ],
            }

        return {
            "account_id": self.account_id,
            "run_id": self.run_id,
            "observed_at": self.observed_at,
            "status": self.status,
            "manual_only": self.manual_only,
            "paper_only": self.paper_only,
            "policy_actionable": self.policy_actionable,
            "live_authorized": self.live_authorized,
            "research": research_record,
            "scheduler": scheduler_record,
            "exploration": exploration_record,
            "new_entries_skipped_reason": self.new_entries_skipped_reason,
            "new_positions_pending_next_tick": (
                self.new_positions_pending_next_tick
            ),
        }


def _check_research(report: MarketResearchCycleReport) -> None:
    if (
        not report.research_only
        or not report.paper_only
        or report.policy_actionable
        or report.execution_wired
    ):
        raise RuntimeError("market research crossed PAPER-only boundary")


def _check_exploration(report: MarketPaperExplorationReport) -> None:
    if (
        not report.paper_only
        or report.policy_actionable
        or report.live_authorized
    ):
        raise RuntimeError("market exploration crossed PAPER-only boundary")


def run_manual_market_paper_cycle(
    storage: Storage,
    *,
    account_id: str,
    run_id: str,
    per_position_capital_quote: float,
    network_cost_quote: float,
    max_new_positions: int = 1,
    minimum_chain_observations: int = 12,
    intake_max_pools: int = 500,
    discovery_page_size: int = 1000,
    discovery_max_pages: int = 100,
    discovery_sort_by: str | None = "tvl:desc",
    seed_batch_limit: int = 25,
    refresh_batch_limit: int = 10,
    bin_array_radius: int = 0,
    timeout_seconds: int = 120,
    quote_max_age_seconds: int = 300,
    max_share_bps: int = 500,
    scheduler_interval_seconds: int = 300,
    scheduler_lease_seconds: int = 900,
    scheduler_max_positions: int | None = None,
    observed_at: str | None = None,
    settings: Settings | None = None,
    research_runner: ResearchRunner = run_market_research_cycle,
    scheduler_runner: SchedulerRunner = run_scheduled_paper_tick,
    exploration_runner: ExplorationRunner = run_market_paper_exploration,
) -> ManualMarketPaperCycleReport:
    """
    Run one manual market-to-PAPER cycle.

    Existing PAPER positions are managed before any new virtual positions are
    opened. Newly opened positions are intentionally left for the next
    scheduler tick, avoiding a second synthetic tick in the same idempotency
    bucket.
    """
    if not account_id.strip():
        raise ValueError("account_id is required")
    if not run_id.strip():
        raise ValueError("run_id is required")

    timestamp = observed_at or utc_now_iso()
    cfg = settings or Settings.from_env()

    research = research_runner(
        storage,
        settings=cfg,
        discovery_page_size=discovery_page_size,
        discovery_max_pages=discovery_max_pages,
        discovery_sort_by=discovery_sort_by,
        seed_batch_limit=seed_batch_limit,
        refresh_batch_limit=refresh_batch_limit,
        bin_array_radius=bin_array_radius,
        timeout_seconds=timeout_seconds,
        minimum_chain_observations=minimum_chain_observations,
        intake_max_pools=intake_max_pools,
        observed_at=timestamp,
    )
    _check_research(research)

    scheduler = scheduler_runner(
        storage,
        account_id=account_id,
        interval_seconds=scheduler_interval_seconds,
        lease_seconds=scheduler_lease_seconds,
        as_of=timestamp,
        settings=cfg,
        quote_max_age_seconds=quote_max_age_seconds,
        max_positions=scheduler_max_positions,
        retry_failed=True,
        refresh_jupiter_quotes=True,
    )

    skip_reason = None
    exploration = None

    if scheduler.status in BLOCKING_SCHEDULER_STATUSES:
        skip_reason = f"SCHEDULER_{scheduler.status}"
    elif research.status == "FAILED":
        skip_reason = "MARKET_RESEARCH_FAILED"
    else:
        exploration = exploration_runner(
            storage,
            account_id=account_id,
            run_id=run_id,
            per_position_capital_quote=per_position_capital_quote,
            network_cost_quote=network_cost_quote,
            max_new_positions=max_new_positions,
            minimum_chain_observations=minimum_chain_observations,
            intake_max_pools=intake_max_pools,
            quote_max_age_seconds=quote_max_age_seconds,
            max_share_bps=max_share_bps,
        )
        _check_exploration(exploration)

    pending = (
        exploration.positions_opened
        if exploration is not None
        else 0
    )

    if scheduler.status == "BUSY":
        status = "BUSY"
    elif scheduler.status in BLOCKING_SCHEDULER_STATUSES:
        status = "PARTIAL" if research.status != "FAILED" else "FAILED"
    elif research.status == "FAILED":
        status = "PARTIAL"
    elif research.status == "PARTIAL":
        status = "PARTIAL"
    else:
        status = "COMPLETE"

    return ManualMarketPaperCycleReport(
        account_id=account_id,
        run_id=run_id,
        observed_at=timestamp,
        status=status,
        manual_only=True,
        paper_only=True,
        policy_actionable=False,
        live_authorized=False,
        research=research,
        scheduler=scheduler,
        exploration=exploration,
        new_entries_skipped_reason=skip_reason,
        new_positions_pending_next_tick=pending,
    )
