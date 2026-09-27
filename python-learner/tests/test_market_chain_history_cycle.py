from __future__ import annotations

from types import SimpleNamespace

from meteora_learner.market_chain_history_cycle import (
    run_market_chain_history_cycle,
)
from meteora_learner.storage import Storage


def _context_report(*, pools_failed=0):
    return SimpleNamespace(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        capture=SimpleNamespace(pools_failed=pools_failed),
    )


def _refresh_report(*, pools_failed=0):
    return SimpleNamespace(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        pools_failed=pools_failed,
    )


def test_cycle_runs_seed_then_refresh_with_same_bounds(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    def context(storage_arg, **kwargs):
        calls.append(("context", kwargs))
        return _context_report()

    def refresh(storage_arg, **kwargs):
        calls.append(("refresh", kwargs))
        return _refresh_report()

    report = run_market_chain_history_cycle(
        storage,
        seed_batch_limit=7,
        refresh_batch_limit=3,
        bin_array_radius=1,
        timeout_seconds=45,
        observed_at="2026-09-27T08:00:00+00:00",
        context_runner=context,
        refresh_runner=refresh,
    )

    assert [item[0] for item in calls] == ["context", "refresh"]
    assert calls[0][1]["batch_limit"] == 7
    assert calls[1][1]["batch_limit"] == 3
    assert calls[0][1]["ingest_observed_at"] == calls[1][1]["ingest_observed_at"]
    assert report.status == "COMPLETE"
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_seed_failure_does_not_suppress_longitudinal_refresh(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    def context(storage_arg, **kwargs):
        raise RuntimeError("rpc failure with sensitive detail")

    def refresh(storage_arg, **kwargs):
        calls.append("refresh")
        return _refresh_report()

    report = run_market_chain_history_cycle(
        storage,
        context_runner=context,
        refresh_runner=refresh,
    )

    assert calls == ["refresh"]
    assert report.status == "PARTIAL"
    assert report.context is None
    assert report.refresh is not None
    assert report.stages[0].error_category == "CHAIN_CONTEXT_CAPTURE_FAILED"
    assert "sensitive" not in str(report.to_record())


def test_cycle_reports_partial_when_batches_have_pool_failures(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = run_market_chain_history_cycle(
        storage,
        context_runner=lambda storage_arg, **kwargs: _context_report(
            pools_failed=1
        ),
        refresh_runner=lambda storage_arg, **kwargs: _refresh_report(
            pools_failed=2
        ),
    )

    assert report.status == "PARTIAL"
    assert [item.status for item in report.stages] == ["PARTIAL", "PARTIAL"]
    assert report.stages[0].error_category == (
        "CHAIN_CONTEXT_PARTIAL_FAILURE"
    )
    assert report.stages[1].error_category == (
        "CHAIN_REFRESH_PARTIAL_FAILURE"
    )


def test_cycle_fails_closed_on_boundary_crossing(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    bad = SimpleNamespace(
        research_only=True,
        read_only_capture=True,
        policy_actionable=True,
        execution_wired=False,
    )
    report = run_market_chain_history_cycle(
        storage,
        context_runner=lambda storage_arg, **kwargs: bad,
        refresh_runner=lambda storage_arg, **kwargs: bad,
    )

    assert report.status == "FAILED"
    assert all(item.status == "FAILED" for item in report.stages)
