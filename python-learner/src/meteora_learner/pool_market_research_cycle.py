from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .market_pool_universe import (
    PageFetcher,
    PoolUniverseDiscoveryReport,
    discover_pool_universe,
)
from .pool_market_data_quality import (
    PoolMarketCoverageReport,
    build_pool_market_coverage_report,
)
from .pool_market_learning import (
    PoolMarketLearningDataset,
    build_pool_market_learning_dataset_from_store,
)
from .pool_market_walk_forward import (
    PoolMarketWalkForwardReport,
    evaluate_pool_market_walk_forward,
)
from .settings import Settings
from .storage import Storage


@dataclass(frozen=True)
class PoolMarketResearchCycleReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    status: str
    reason: str | None
    discovery: PoolUniverseDiscoveryReport | None
    coverage: PoolMarketCoverageReport
    dataset: PoolMarketLearningDataset | None
    walk_forward: PoolMarketWalkForwardReport | None

    def to_record(self) -> dict[str, Any]:
        dataset_summary = None
        if self.dataset is not None:
            dataset_summary = {
                "rows_seen": self.dataset.rows_seen,
                "pools_seen": self.dataset.pools_seen,
                "examples_built": self.dataset.examples_built,
                "rows_dropped": self.dataset.rows_dropped,
                "drop_reasons": list(self.dataset.drop_reasons),
            }

        return {
            "research_only": self.research_only,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
            "status": self.status,
            "reason": self.reason,
            "discovery": (
                self.discovery.to_record()
                if self.discovery is not None
                else None
            ),
            "coverage": self.coverage.to_record(),
            "dataset": dataset_summary,
            "walk_forward": (
                self.walk_forward.to_record()
                if self.walk_forward is not None
                else None
            ),
        }


def run_pool_market_research_cycle(
    storage: Storage,
    *,
    capture_universe: bool = True,
    observed_at: str | None = None,
    settings: Settings | None = None,
    fetch_page: PageFetcher | None = None,
    page_size: int = 1000,
    max_pages: int = 100,
    horizon_rows: int = 6,
    volatility_window: int = 6,
    drawdown_window: int = 12,
    activity_window: int = 6,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> PoolMarketResearchCycleReport:
    """
    Run one research-only market learning cycle.

    The cycle can capture the current Meteora pool universe, rebuild the
    no-lookahead dataset from stored history, and evaluate continuous outcome
    prediction with purged walk-forward folds.

    Insufficient history is a normal collection state, not a reason to invent
    labels, thresholds, synthetic outcomes, or an execution decision.
    """
    discovery: PoolUniverseDiscoveryReport | None = None

    if capture_universe:
        discovery = discover_pool_universe(
            storage,
            observed_at=observed_at,
            settings=settings,
            fetch_page=fetch_page,
            page_size=page_size,
            max_pages=max_pages,
        )

    coverage = build_pool_market_coverage_report(
        str(storage.path)
    )

    try:
        dataset = build_pool_market_learning_dataset_from_store(
            str(storage.path),
            horizon_rows=horizon_rows,
            volatility_window=volatility_window,
            drawdown_window=drawdown_window,
            activity_window=activity_window,
        )
    except ValueError as exc:
        return PoolMarketResearchCycleReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="COLLECTING_HISTORY",
            reason=str(exc),
            discovery=discovery,
            coverage=coverage,
            dataset=None,
            walk_forward=None,
        )

    try:
        walk_forward = evaluate_pool_market_walk_forward(
            dataset.to_frame(),
            min_train_decision_times=min_train_decision_times,
            validation_decision_times=validation_decision_times,
            step_decision_times=step_decision_times,
            min_train_rows=min_train_rows,
        )
    except ValueError as exc:
        return PoolMarketResearchCycleReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            status="COLLECTING_HISTORY",
            reason=str(exc),
            discovery=discovery,
            coverage=coverage,
            dataset=dataset,
            walk_forward=None,
        )

    return PoolMarketResearchCycleReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        status="EVALUATED",
        reason=None,
        discovery=discovery,
        coverage=coverage,
        dataset=dataset,
        walk_forward=walk_forward,
    )
