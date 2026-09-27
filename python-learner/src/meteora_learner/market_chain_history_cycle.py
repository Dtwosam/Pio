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


def _safe_attr(value: Any, name: str) -> Any:
    return getattr(value, name, None) if value is not None else None


def _context_queue_record(queue: Any) -> dict[str, Any] | None:
    if queue is None:
        return None
    return {
        "discovered_pools": _safe_attr(queue, "discovered_pools"),
        "chain_observed_pools": _safe_attr(queue, "chain_observed_pools"),
        "missing_chain_pools": _safe_attr(queue, "missing_chain_pools"),
        "batch_limit": _safe_attr(queue, "batch_limit"),
    }


def _refresh_queue_record(queue: Any) -> dict[str, Any] | None:
    if queue is None:
        return None
    return {
        "discovered_pools": _safe_attr(queue, "discovered_pools"),
        "chain_observed_pools": _safe_attr(queue, "chain_observed_pools"),
        "refreshable_pools": _safe_attr(queue, "refreshable_pools"),
        "batch_limit": _safe_attr(queue, "batch_limit"),
    }


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
        context = None
        if self.context is not None:
            capture = _safe_attr(self.context, "capture")
            context = {
                "queue_before": _context_queue_record(
                    _safe_attr(self.context, "queue_before")
                ),
                "capture": (
                    None
                    if capture is None
                    else {
                        "pools_attempted": _safe_attr(capture, "pools_attempted"),
                        "pools_captured": _safe_attr(capture, "pools_captured"),
                        "pools_failed": _safe_attr(capture, "pools_failed"),
                        "target_met": _safe_attr(capture, "target_met"),
                        "preferred_target_met": _safe_attr(
                            capture,
                            "preferred_target_met",
                        ),
                    }
                ),
                "queue_after": _context_queue_record(
                    _safe_attr(self.context, "queue_after")
                ),
            }

        refresh = None
        if self.refresh is not None:
            refresh = {
                "queue_before": _refresh_queue_record(
                    _safe_attr(self.refresh, "queue_before")
                ),
                "pools_attempted": _safe_attr(
                    self.refresh,
                    "pools_attempted",
                ),
                "pools_refreshed": _safe_attr(
                    self.refresh,
                    "pools_refreshed",
                ),
                "pools_failed": _safe_attr(self.refresh, "pools_failed"),
                "queue_after": _refresh_queue_record(
                    _safe_attr(self.refresh, "queue_after")
                ),
            }

        return {
            "research_only": self.research_only,
            "read_only_capture": self.read_only_capture,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
            "status": self.status,
            "context": context,
            "refresh": refresh,
            "stages": [item.to_record() for item in self.stages],
        }


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


def _context_stage(report: MarketChainContextCaptureReport) -> MarketChainHistoryStage:
    capture = report.capture
    if capture is not None and int(capture.pools_failed) > 0:
        return MarketChainHistoryStage(
            stage="SEED_MISSING_CHAIN_CONTEXT",
            status="PARTIAL",
            error_category="CHAIN_CONTEXT_PARTIAL_FAILURE",
        )
    return MarketChainHistoryStage(
        stage="SEED_MISSING_CHAIN_CONTEXT",
        status="SUCCESS",
        error_category=None,
    )


def _refresh_stage(report: MarketChainRefreshReport) -> MarketChainHistoryStage:
    if int(report.pools_failed) > 0:
        return MarketChainHistoryStage(
            stage="REFRESH_LONGITUDINAL_CHAIN_CONTEXT",
            status="PARTIAL",
            error_category="CHAIN_REFRESH_PARTIAL_FAILURE",
        )
    return MarketChainHistoryStage(
        stage="REFRESH_LONGITUDINAL_CHAIN_CONTEXT",
        status="SUCCESS",
        error_category=None,
    )


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
        stages.append(_context_stage(context))
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
        stages.append(_refresh_stage(refresh))
    except Exception:
        stages.append(
            MarketChainHistoryStage(
                stage="REFRESH_LONGITUDINAL_CHAIN_CONTEXT",
                status="FAILED",
                error_category="CHAIN_REFRESH_FAILED",
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
