from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .market_chain_history_cycle import (
    MarketChainHistoryCycleReport,
    run_market_chain_history_cycle,
)
from .market_paper_intake import (
    MarketPaperIntakeReport,
    build_market_paper_intake,
)
from .market_pool_universe import (
    PoolUniverseDiscoveryReport,
    discover_pool_universe,
)
from .settings import Settings
from .storage import Storage


DiscoveryRunner = Callable[..., PoolUniverseDiscoveryReport]
HistoryRunner = Callable[..., MarketChainHistoryCycleReport]
IntakeRunner = Callable[..., MarketPaperIntakeReport]


@dataclass(frozen=True)
class MarketResearchStage:
    stage: str
    status: str
    error_category: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketResearchCycleReport:
    research_only: bool
    paper_only: bool
    policy_actionable: bool
    execution_wired: bool
    status: str
    discovery: PoolUniverseDiscoveryReport | None
    chain_history: MarketChainHistoryCycleReport | None
    intake: MarketPaperIntakeReport | None
    stages: tuple[MarketResearchStage, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _check_discovery(report: PoolUniverseDiscoveryReport) -> None:
    if (
        not report.research_only
        or report.policy_actionable
        or report.execution_wired
    ):
        raise RuntimeError("market discovery crossed research-only boundary")


def _check_history(report: MarketChainHistoryCycleReport) -> None:
    if (
        not report.research_only
        or not report.read_only_capture
        or report.policy_actionable
        or report.execution_wired
    ):
        raise RuntimeError("market chain history crossed research-only boundary")


def _history_stage(report: MarketChainHistoryCycleReport) -> MarketResearchStage:
    if report.status == "COMPLETE":
        return MarketResearchStage("COLLECT_CHAIN_HISTORY", "SUCCESS", None)
    if report.status == "PARTIAL":
        return MarketResearchStage(
            "COLLECT_CHAIN_HISTORY",
            "PARTIAL",
            "MARKET_CHAIN_HISTORY_PARTIAL",
        )
    if report.status == "FAILED":
        return MarketResearchStage(
            "COLLECT_CHAIN_HISTORY",
            "FAILED",
            "MARKET_CHAIN_HISTORY_FAILED",
        )
    raise ValueError(f"unsupported market chain history status: {report.status}")


def _check_intake(report: MarketPaperIntakeReport) -> None:
    if (
        not report.research_only
        or not report.paper_only
        or report.policy_actionable
        or report.execution_wired
    ):
        raise RuntimeError("market PAPER intake crossed research-only boundary")


def run_market_research_cycle(
    storage: Storage,
    *,
    settings: Settings | None = None,
    discovery_page_size: int = 1000,
    discovery_max_pages: int = 100,
    discovery_sort_by: str | None = "tvl:desc",
    seed_batch_limit: int = 25,
    refresh_batch_limit: int = 10,
    bin_array_radius: int = 0,
    timeout_seconds: int = 120,
    minimum_chain_observations: int = 12,
    intake_max_pools: int = 500,
    observed_at: str | None = None,
    discovery_runner: DiscoveryRunner = discover_pool_universe,
    history_runner: HistoryRunner = run_market_chain_history_cycle,
    intake_runner: IntakeRunner = build_market_paper_intake,
) -> MarketResearchCycleReport:
    """Run one bounded research-only market collection and PAPER-readiness cycle."""
    cfg = settings or Settings.from_env()
    discovery = None
    history = None
    intake = None
    stages: list[MarketResearchStage] = []

    try:
        discovery = discovery_runner(
            storage,
            observed_at=observed_at,
            settings=cfg,
            page_size=discovery_page_size,
            max_pages=discovery_max_pages,
            sort_by=discovery_sort_by,
        )
        _check_discovery(discovery)
        stages.append(MarketResearchStage("DISCOVER_POOL_UNIVERSE", "SUCCESS", None))
    except Exception:
        stages.append(
            MarketResearchStage(
                "DISCOVER_POOL_UNIVERSE",
                "FAILED",
                "POOL_UNIVERSE_DISCOVERY_FAILED",
            )
        )

    try:
        history = history_runner(
            storage,
            seed_batch_limit=seed_batch_limit,
            refresh_batch_limit=refresh_batch_limit,
            bin_array_radius=bin_array_radius,
            timeout_seconds=timeout_seconds,
            observed_at=observed_at,
        )
        _check_history(history)
        stages.append(_history_stage(history))
    except Exception:
        stages.append(
            MarketResearchStage(
                "COLLECT_CHAIN_HISTORY",
                "FAILED",
                "MARKET_CHAIN_HISTORY_FAILED",
            )
        )

    try:
        intake = intake_runner(
            storage.path,
            minimum_chain_observations=minimum_chain_observations,
            max_pools=intake_max_pools,
        )
        _check_intake(intake)
        stages.append(MarketResearchStage("BUILD_PAPER_INTAKE", "SUCCESS", None))
    except Exception:
        stages.append(
            MarketResearchStage(
                "BUILD_PAPER_INTAKE",
                "FAILED",
                "PAPER_INTAKE_FAILED",
            )
        )

    statuses = tuple(item.status for item in stages)
    status = (
        "COMPLETE"
        if all(value == "SUCCESS" for value in statuses)
        else "PARTIAL"
        if any(value in {"SUCCESS", "PARTIAL"} for value in statuses)
        else "FAILED"
    )
    return MarketResearchCycleReport(
        research_only=True,
        paper_only=True,
        policy_actionable=False,
        execution_wired=False,
        status=status,
        discovery=discovery,
        chain_history=history,
        intake=intake,
        stages=tuple(stages),
    )
