from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from meteora_learner.pool_market_research_cycle import (
    run_pool_market_research_cycle,
)
from meteora_learner.storage import Storage


def _pool(address: str, index: int, pool_index: int) -> dict:
    wave = math.sin(index / 5.0 + pool_index)
    return {
        "address": address,
        "tvl": 100_000.0 + 700.0 * index + 2_000.0 * wave,
        "volume_24h": 20_000.0 + 150.0 * index + 2_500.0 * abs(wave),
        "fees_24h": 80.0 + 0.7 * index + 6.0 * abs(wave),
        "current_price": 10.0 + pool_index + 0.03 * index + 0.3 * wave,
        "bin_step": 25,
        "token_x": {"symbol": "X", "decimals": 6},
        "token_y": {"symbol": "Y", "decimals": 6},
    }


def _seed(storage: Storage, observations: int = 80) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(observations):
        observed_at = (
            start + timedelta(hours=index)
        ).isoformat()
        for pool_index, address in enumerate(("A", "B", "C")):
            storage.save_pool_snapshot(
                _pool(address, index, pool_index),
                observed_at=observed_at,
            )


def test_cycle_reports_collecting_history_without_enough_data(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = run_pool_market_research_cycle(
        storage,
        capture_universe=True,
        observed_at="2026-09-26T00:00:00+00:00",
        fetch_page=lambda page, page_size: {
            "data": [_pool("A", 0, 0)]
        },
        page_size=10,
        max_pages=1,
        min_train_decision_times=5,
        validation_decision_times=2,
        min_train_rows=2,
    )

    assert report.status == "COLLECTING_HISTORY"
    assert report.reason is not None
    assert report.discovery is not None
    assert report.discovery.unique_pools_seen == 1
    assert report.walk_forward is None
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_cycle_builds_dataset_and_walk_forward_when_history_exists(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)

    report = run_pool_market_research_cycle(
        storage,
        capture_universe=False,
        horizon_rows=4,
        volatility_window=4,
        drawdown_window=6,
        activity_window=4,
        min_train_decision_times=25,
        validation_decision_times=8,
        step_decision_times=8,
        min_train_rows=50,
    )

    assert report.status == "EVALUATED"
    assert report.reason is None
    assert report.discovery is None
    assert report.dataset is not None
    assert report.dataset.examples_built > 0
    assert report.walk_forward is not None
    assert len(report.walk_forward.folds) > 0
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_cycle_never_turns_missing_history_into_synthetic_result(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = run_pool_market_research_cycle(
        storage,
        capture_universe=False,
    )

    assert report.status == "COLLECTING_HISTORY"
    assert report.dataset is None
    assert report.walk_forward is None
