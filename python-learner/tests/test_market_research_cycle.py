from __future__ import annotations

from types import SimpleNamespace

from meteora_learner.market_research_cycle import run_market_research_cycle
from meteora_learner.storage import Storage


def _discovery():
    return SimpleNamespace(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
    )


def _history(*, status="COMPLETE"):
    return SimpleNamespace(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        status=status,
    )


def _intake():
    return SimpleNamespace(
        research_only=True,
        paper_only=True,
        policy_actionable=False,
        execution_wired=False,
    )


def test_cycle_runs_discovery_history_and_intake_in_order(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    def discovery(storage_arg, **kwargs):
        calls.append(("discovery", kwargs))
        return _discovery()

    def history(storage_arg, **kwargs):
        calls.append(("history", kwargs))
        return _history()

    def intake(database_path, **kwargs):
        calls.append(("intake", kwargs))
        assert database_path == storage.path
        return _intake()

    report = run_market_research_cycle(
        storage,
        discovery_page_size=250,
        discovery_max_pages=4,
        seed_batch_limit=7,
        refresh_batch_limit=3,
        minimum_chain_observations=8,
        intake_max_pools=100,
        observed_at="2026-09-27T08:00:00+00:00",
        discovery_runner=discovery,
        history_runner=history,
        intake_runner=intake,
    )

    assert [item[0] for item in calls] == [
        "discovery",
        "history",
        "intake",
    ]
    assert calls[0][1]["page_size"] == 250
    assert calls[1][1]["seed_batch_limit"] == 7
    assert calls[2][1]["minimum_chain_observations"] == 8
    assert report.status == "COMPLETE"
    assert report.paper_only is True
    assert report.execution_wired is False


def test_partial_chain_history_propagates_to_cycle_status(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = run_market_research_cycle(
        storage,
        discovery_runner=lambda storage_arg, **kwargs: _discovery(),
        history_runner=lambda storage_arg, **kwargs: _history(
            status="PARTIAL"
        ),
        intake_runner=lambda database_path, **kwargs: _intake(),
    )

    assert report.status == "PARTIAL"
    assert report.stages[1].status == "PARTIAL"
    assert report.stages[1].error_category == (
        "MARKET_CHAIN_HISTORY_PARTIAL"
    )


def test_discovery_failure_does_not_suppress_history_or_intake(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    def discovery(storage_arg, **kwargs):
        raise RuntimeError("secret transport detail")

    def history(storage_arg, **kwargs):
        calls.append("history")
        return _history()

    def intake(database_path, **kwargs):
        calls.append("intake")
        return _intake()

    report = run_market_research_cycle(
        storage,
        discovery_runner=discovery,
        history_runner=history,
        intake_runner=intake,
    )

    assert calls == ["history", "intake"]
    assert report.status == "PARTIAL"
    assert report.discovery is None
    assert report.stages[0].error_category == (
        "POOL_UNIVERSE_DISCOVERY_FAILED"
    )
    assert "secret transport detail" not in str(report.to_record())


def test_boundary_crossing_is_counted_as_stage_failure(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    bad = SimpleNamespace(
        research_only=True,
        paper_only=True,
        read_only_capture=True,
        policy_actionable=True,
        execution_wired=False,
    )

    report = run_market_research_cycle(
        storage,
        discovery_runner=lambda storage_arg, **kwargs: bad,
        history_runner=lambda storage_arg, **kwargs: bad,
        intake_runner=lambda database_path, **kwargs: bad,
    )

    assert report.status == "FAILED"
    assert all(item.status == "FAILED" for item in report.stages)
