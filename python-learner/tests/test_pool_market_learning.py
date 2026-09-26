from __future__ import annotations

import pandas as pd
import pytest

from meteora_learner.pool_market_learning import (
    POOL_MARKET_FEATURE_COLUMNS,
    POOL_MARKET_TARGET_COLUMNS,
    build_pool_market_learning_dataset,
)


def _history() -> pd.DataFrame:
    rows = []
    for pool, price_base, tvl_base in (
        ("POOL_A", 100.0, 1_000_000.0),
        ("POOL_B", 10.0, 250_000.0),
    ):
        for index in range(20):
            rows.append(
                {
                    "pool_address": pool,
                    "observed_at": pd.Timestamp(
                        "2026-01-01T00:00:00Z"
                    )
                    + pd.Timedelta(hours=index),
                    "price": price_base * (1.0 + 0.01 * index),
                    "tvl_usd": tvl_base * (1.0 + 0.005 * index),
                    "volume_24h_usd": (
                        tvl_base * (0.20 + 0.01 * index)
                    ),
                    "fees_24h_usd": (
                        tvl_base * (0.0005 + 0.00001 * index)
                    ),
                }
            )
    return pd.DataFrame(rows)


def test_dataset_has_continuous_features_and_targets() -> None:
    report = build_pool_market_learning_dataset(
        _history(),
        horizon_rows=3,
    )

    assert report.pools_seen == 2
    assert report.examples_built > 0

    columns = set(report.to_frame().columns)
    assert set(POOL_MARKET_FEATURE_COLUMNS) <= columns
    assert set(POOL_MARKET_TARGET_COLUMNS) <= columns

    lowered = {column.lower() for column in columns}
    assert "safe" not in lowered
    assert "unsafe" not in lowered
    assert "collapse" not in lowered
    assert "risk_score" not in lowered


def test_decision_features_do_not_change_when_future_changes() -> None:
    base = _history()
    first = build_pool_market_learning_dataset(
        base,
        horizon_rows=3,
    ).to_frame()

    decision_time = first.loc[
        first["pool_address"] == "POOL_A",
        "decision_observed_at",
    ].iloc[2]

    altered = base.copy()
    decision_ts = pd.Timestamp(decision_time)
    mask = (
        (altered["pool_address"] == "POOL_A")
        & (altered["observed_at"] > decision_ts)
    )
    altered.loc[mask, "price"] *= 0.25
    altered.loc[mask, "tvl_usd"] *= 0.30

    second = build_pool_market_learning_dataset(
        altered,
        horizon_rows=3,
    ).to_frame()

    left = first[
        (first["pool_address"] == "POOL_A")
        & (first["decision_observed_at"] == decision_time)
    ].iloc[0]
    right = second[
        (second["pool_address"] == "POOL_A")
        & (second["decision_observed_at"] == decision_time)
    ].iloc[0]

    for column in POOL_MARKET_FEATURE_COLUMNS:
        assert left[column] == pytest.approx(right[column])

    assert right["target_price_return"] < left["target_price_return"]
    assert (
        right["target_max_price_drawdown"]
        < left["target_max_price_drawdown"]
    )


def test_future_downside_is_preserved_as_continuous_target() -> None:
    history = _history()
    report = build_pool_market_learning_dataset(
        history,
        horizon_rows=4,
    ).to_frame()

    decision = report[
        report["pool_address"] == "POOL_A"
    ].iloc[0]

    assert isinstance(decision["target_max_price_drawdown"], float)
    assert isinstance(decision["target_max_tvl_drawdown"], float)


def test_pool_feature_history_never_crosses_between_pools() -> None:
    history = _history()

    pool_b = history["pool_address"] == "POOL_B"
    history.loc[pool_b, "price"] = 10.0

    report = build_pool_market_learning_dataset(
        history,
        horizon_rows=2,
    ).to_frame()

    pool_b_examples = report[report["pool_address"] == "POOL_B"]
    assert not pool_b_examples.empty
    assert (
        pool_b_examples["price_return_1"].abs() < 1e-12
    ).all()


def test_invalid_market_values_fail_as_data_integrity_error() -> None:
    history = _history()
    history.loc[0, "tvl_usd"] = 0

    with pytest.raises(ValueError, match="tvl_usd must be positive"):
        build_pool_market_learning_dataset(history)
