from __future__ import annotations

from types import SimpleNamespace

from meteora_learner.market_chain_history_cycle import (
    MarketChainHistoryCycleReport,
    MarketChainHistoryStage,
)
from meteora_learner.market_research_cycle import (
    MarketResearchCycleReport,
    MarketResearchStage,
)


def _queue_context():
    return SimpleNamespace(
        discovered_pools=10,
        chain_observed_pools=4,
        missing_chain_pools=6,
        batch_limit=3,
    )


def _queue_refresh():
    return SimpleNamespace(
        discovered_pools=10,
        chain_observed_pools=4,
        refreshable_pools=4,
        batch_limit=2,
    )


def test_chain_history_record_redacts_raw_collector_errors() -> None:
    context = SimpleNamespace(
        queue_before=_queue_context(),
        capture=SimpleNamespace(
            pools_attempted=2,
            pools_captured=1,
            pools_failed=1,
            target_met=False,
            preferred_target_met=False,
            items=(
                SimpleNamespace(
                    pool_address="pool-a",
                    error="secret rpc credential detail",
                ),
            ),
        ),
        queue_after=_queue_context(),
    )
    refresh = SimpleNamespace(
        queue_before=_queue_refresh(),
        pools_attempted=2,
        pools_refreshed=1,
        pools_failed=1,
        items=(
            SimpleNamespace(
                pool_address="pool-b",
                error="secret executor detail",
            ),
        ),
        queue_after=_queue_refresh(),
    )
    report = MarketChainHistoryCycleReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        status="PARTIAL",
        context=context,
        refresh=refresh,
        stages=(
            MarketChainHistoryStage(
                stage="SEED_MISSING_CHAIN_CONTEXT",
                status="PARTIAL",
                error_category="CHAIN_CONTEXT_PARTIAL_FAILURE",
            ),
            MarketChainHistoryStage(
                stage="REFRESH_LONGITUDINAL_CHAIN_CONTEXT",
                status="PARTIAL",
                error_category="CHAIN_REFRESH_PARTIAL_FAILURE",
            ),
        ),
    )

    record = report.to_record()
    serialized = str(record)

    assert "secret rpc credential detail" not in serialized
    assert "secret executor detail" not in serialized
    assert record["context"]["capture"]["pools_failed"] == 1
    assert record["refresh"]["pools_failed"] == 1
    assert record["stages"][0]["error_category"] == (
        "CHAIN_CONTEXT_PARTIAL_FAILURE"
    )


def test_market_research_record_uses_redacted_chain_history_record() -> None:
    history = MarketChainHistoryCycleReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        status="FAILED",
        context=None,
        refresh=None,
        stages=(
            MarketChainHistoryStage(
                stage="SEED_MISSING_CHAIN_CONTEXT",
                status="FAILED",
                error_category="CHAIN_CONTEXT_CAPTURE_FAILED",
            ),
        ),
    )
    report = MarketResearchCycleReport(
        research_only=True,
        paper_only=True,
        policy_actionable=False,
        execution_wired=False,
        status="PARTIAL",
        discovery=None,
        chain_history=history,
        intake=None,
        stages=(
            MarketResearchStage(
                stage="COLLECT_CHAIN_HISTORY",
                status="FAILED",
                error_category="MARKET_CHAIN_HISTORY_FAILED",
            ),
        ),
    )

    record = report.to_record()

    assert record["chain_history"]["status"] == "FAILED"
    assert record["chain_history"]["stages"][0]["error_category"] == (
        "CHAIN_CONTEXT_CAPTURE_FAILED"
    )
    assert record["stages"][0]["error_category"] == (
        "MARKET_CHAIN_HISTORY_FAILED"
    )
