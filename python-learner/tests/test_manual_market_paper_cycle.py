from __future__ import annotations

from types import SimpleNamespace

from meteora_learner.manual_market_paper_cycle import (
    run_manual_market_paper_cycle,
)
from meteora_learner.storage import Storage


NOW = "2026-09-27T11:30:00+00:00"


def _research(status="COMPLETE"):
    return SimpleNamespace(
        research_only=True,
        paper_only=True,
        policy_actionable=False,
        execution_wired=False,
        status=status,
        intake=SimpleNamespace(pools_ready=3),
        stages=(),
    )


def _scheduler(status="COMPLETE", error=None):
    return SimpleNamespace(
        status=status,
        tick_id="tick",
        lease_acquired=(status != "BUSY"),
        recovered_stale_lease=False,
        error=error,
    )


def _exploration(opened=1, status="COMPLETE"):
    return SimpleNamespace(
        paper_only=True,
        policy_actionable=False,
        live_authorized=False,
        status=status,
        pools_ready=3,
        pools_considered=2,
        positions_opened=opened,
        positions_already_applied=0,
        max_new_positions=1,
        max_pools_considered=25,
        items=(),
    )


def test_manual_cycle_orders_research_scheduler_then_new_entries(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    def research(storage_arg, **kwargs):
        calls.append(("research", kwargs))
        return _research()

    def scheduler(storage_arg, **kwargs):
        calls.append(("scheduler", kwargs))
        return _scheduler()

    def explore(storage_arg, **kwargs):
        calls.append(("explore", kwargs))
        return _exploration()

    report = run_manual_market_paper_cycle(
        storage,
        account_id="paper",
        run_id="r1",
        per_position_capital_quote=100,
        network_cost_quote=1,
        observed_at=NOW,
        research_runner=research,
        scheduler_runner=scheduler,
        exploration_runner=explore,
    )

    assert [name for name, _ in calls] == [
        "research",
        "scheduler",
        "explore",
    ]
    assert calls[0][1]["observed_at"] == NOW
    assert calls[1][1]["as_of"] == NOW
    assert calls[1][1]["retry_failed"] is True
    assert calls[1][1]["refresh_jupiter_quotes"] is True
    assert calls[2][1]["observed_at"] == NOW
    assert calls[2][1]["max_pools_considered"] == 25
    assert report.status == "COMPLETE"
    assert report.new_positions_pending_next_tick == 1
    assert report.paper_only is True
    assert report.live_authorized is False


def test_exploration_busy_race_is_fail_closed(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = run_manual_market_paper_cycle(
        storage,
        account_id="paper",
        run_id="race",
        per_position_capital_quote=100,
        network_cost_quote=1,
        observed_at=NOW,
        research_runner=lambda storage_arg, **kwargs: _research(),
        scheduler_runner=lambda storage_arg, **kwargs: _scheduler(),
        exploration_runner=lambda storage_arg, **kwargs: _exploration(
            opened=0,
            status="SCHEDULER_BUSY",
        ),
    )

    assert report.status == "BUSY"
    assert report.new_entries_skipped_reason == (
        "EXPLORATION_SCHEDULER_BUSY"
    )
    assert report.new_positions_pending_next_tick == 0


def test_busy_scheduler_blocks_new_entries(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def explore(*args, **kwargs):
        raise AssertionError("busy scheduler must block new entries")

    report = run_manual_market_paper_cycle(
        storage,
        account_id="paper",
        run_id="r2",
        per_position_capital_quote=100,
        network_cost_quote=1,
        observed_at=NOW,
        research_runner=lambda storage_arg, **kwargs: _research(),
        scheduler_runner=lambda storage_arg, **kwargs: _scheduler("BUSY"),
        exploration_runner=explore,
    )

    assert report.status == "BUSY"
    assert report.exploration is None
    assert report.new_entries_skipped_reason == "SCHEDULER_BUSY"


def test_waiting_quotes_scheduler_status_blocks_new_entries(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def explore(*args, **kwargs):
        raise AssertionError("WAITING_QUOTES must block new entries")

    report = run_manual_market_paper_cycle(
        storage,
        account_id="paper",
        run_id="waiting-quotes",
        per_position_capital_quote=100,
        network_cost_quote=1,
        observed_at=NOW,
        research_runner=lambda storage_arg, **kwargs: _research(),
        scheduler_runner=lambda storage_arg, **kwargs: _scheduler(
            "WAITING_QUOTES"
        ),
        exploration_runner=explore,
    )

    assert report.status == "PARTIAL"
    assert report.exploration is None
    assert report.new_entries_skipped_reason == (
        "SCHEDULER_WAITING_QUOTES"
    )


def test_exploration_unhealthy_race_is_partial_and_fail_closed(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = run_manual_market_paper_cycle(
        storage,
        account_id="paper",
        run_id="unhealthy-race",
        per_position_capital_quote=100,
        network_cost_quote=1,
        observed_at=NOW,
        research_runner=lambda storage_arg, **kwargs: _research(),
        scheduler_runner=lambda storage_arg, **kwargs: _scheduler(),
        exploration_runner=lambda storage_arg, **kwargs: _exploration(
            opened=0,
            status="SCHEDULER_UNHEALTHY",
        ),
    )

    assert report.status == "PARTIAL"
    assert report.new_entries_skipped_reason == (
        "EXPLORATION_SCHEDULER_UNHEALTHY"
    )
    assert report.new_positions_pending_next_tick == 0


def test_failed_scheduler_blocks_entries_and_redacts_raw_error(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = run_manual_market_paper_cycle(
        storage,
        account_id="paper",
        run_id="r3",
        per_position_capital_quote=100,
        network_cost_quote=1,
        observed_at=NOW,
        research_runner=lambda storage_arg, **kwargs: _research(),
        scheduler_runner=lambda storage_arg, **kwargs: _scheduler(
            "FAILED",
            error="secret rpc credential detail",
        ),
        exploration_runner=lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError("failed scheduler must block entries")
            )
        ),
    )

    record = report.to_record()
    assert report.status == "PARTIAL"
    assert report.new_entries_skipped_reason == "SCHEDULER_FAILED"
    assert "secret rpc credential detail" not in str(record)


def test_partial_market_research_blocks_new_entries(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def explore(*args, **kwargs):
        raise AssertionError("partial research must block new entries")

    report = run_manual_market_paper_cycle(
        storage,
        account_id="paper",
        run_id="partial-research",
        per_position_capital_quote=100,
        network_cost_quote=1,
        observed_at=NOW,
        research_runner=lambda storage_arg, **kwargs: _research("PARTIAL"),
        scheduler_runner=lambda storage_arg, **kwargs: _scheduler(),
        exploration_runner=explore,
    )

    assert report.status == "PARTIAL"
    assert report.exploration is None
    assert report.new_entries_skipped_reason == (
        "MARKET_RESEARCH_PARTIAL"
    )


def test_failed_market_research_blocks_new_entries_but_keeps_scheduler(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = run_manual_market_paper_cycle(
        storage,
        account_id="paper",
        run_id="r4",
        per_position_capital_quote=100,
        network_cost_quote=1,
        observed_at=NOW,
        research_runner=lambda storage_arg, **kwargs: _research("FAILED"),
        scheduler_runner=lambda storage_arg, **kwargs: _scheduler(),
        exploration_runner=lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError("failed research must block entries")
            )
        ),
    )

    assert report.status == "PARTIAL"
    assert report.new_entries_skipped_reason == "MARKET_RESEARCH_FAILED"
    assert report.scheduler is not None
