from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .market_chain_context import (
    MarketChainContextCaptureReport,
    run_market_chain_context_capture,
)
from .market_chain_refresh import (
    MarketChainRefreshReport,
    run_market_chain_refresh,
)
from .storage import Storage


ContextRunner = Callable[..., MarketChainContextCaptureReport]
RefreshRunner = Callable[..., MarketChainRefreshReport]


@dataclass(frozen=True)
class MarketChainHistoryStage:
    stage: str
    status: str
    error_category: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketChainHistoryCycleReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    status: str
    context: MarketChainContextCaptureReport | None
    refresh: MarketChainRefreshReport | None
    stages: tuple[MarketChainHistoryStage, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _assert_context_boundary(report: MarketChainContextCaptureReport) -> None:
    if (
        not report.research_only
        or not report.read_only_capture
        or report.policy_actionable
        or report.execution_wired
    ):
        raise RuntimeError("market chain context crossed research-only boundary")


def _assert_refresh_boundary(report: MarketChainRefreshReport) -> None:
    if (
        not report.research_only
        or not report.read_only_capture
        or report.policy_actionable
        or report.execution_wired
    ):
        raise RuntimeError("market chain refresh crossed research-only boundary")


def run_market_chain_history_cycle(
    storage: Storage,
    *,
    seed_batch_limit: int = 25,
    refresh_batch_limit: int = 10,
    bin_array_radius: int = 0,
    timeout_seconds: int = 120,
    observed_at: str | None = None,
    context_runner: ContextRunner = run_market_chain_context_capture,
    refresh_runner: RefreshRunner = run_market_chain_refresh,
) -> MarketChainHistoryCycleReport:
    """Run bounded read-only seed and longitudinal market-chain collection."""
    if seed_batch_limit < 1:
        raise ValueError("seed_batch_limit must be positive")
    if refresh_batch_limit < 1:
        raise ValueError("refresh_batch_limit must be positive")
    if bin_array_radius < 0:
        raise ValueError("bin_array_radius cannot be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    context = None
    refresh = None
    stages: list[MarketChainHistoryStage] = []

    try:
        context = context_runner(
            storage,
            batch_limit=seed_batch_limit,
            bin_array_radius=bin_array_radius,
            timeout_seconds=timeout_seconds,
            ingest_observed_at=observed_at,
        )
        _assert_context_boundary(context)
        stages.append(
            MarketChainHistoryStage(
                stage="SEED_MISSING_CHAIN_CONTEXT",
                status="SUCCESS",
                error_category=None,
            )
        )
    except Exception:
        stages.append(
            MarketChainHistoryStage(
                stage="SEED_MISSING_CHAIN_CONTEXT",
                status="FAILED",
                error_category="CHAIN_CONTEXT_CAPTURE_FAILED",
            )
        )

    try:
        refresh = refresh_runner(
            storage,
            batch_limit=refresh_batch_limit,
            bin_array_radius=bin_array_radius,
            timeout_seconds=timeout_seconds,
            ingest_observed_at=observed_at,
        )
        _assert_refresh_boundary(refresh)
        stages.append(
            MarketChainHistoryStage(
                stage="REFRESH_LONGITUDINAL_CHAIN_CONTEXT",
                status="SUCCESS",
                error_category=None,
            )
        )
    except Exception:
        stages.append(
            MarketChainHistoryStage(
                stage="REFRESH_LONGITUDINAL_CHAIN_CONTEXT",
                status="FAILED",
                error_category="CHAIN_REFRESH_FAILED",
            )
        )

    successes = sum(item.status == "SUCCESS" for item in stages)
    status = (
        "COMPLETE"
        if successes == len(stages)
        else "PARTIAL"
        if successes
        else "FAILED"
    )
    return MarketChainHistoryCycleReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        status=status,
        context=context,
        refresh=refresh,
        stages=tuple(stages),
    )
