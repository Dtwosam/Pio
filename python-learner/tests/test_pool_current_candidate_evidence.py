from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from meteora_learner import pool_current_candidate_evidence as module


class _Forecast:
    def __init__(self):
        self.universe_reference_time = "2026-09-26T12:00:00+00:00"
        self.forecasts = (
            SimpleNamespace(
                pool_address="B",
                observed_at="2026-09-26T11:00:00+00:00",
                snapshot_lag_seconds=3600.0,
                price=2.0,
                tvl_usd=200.0,
                volume_24h_usd=50.0,
                fees_24h_usd=2.0,
                history_observations=40,
                predictions=(
                    ("target_price_return", 0.1),
                    ("target_tvl_return", 0.2),
                ),
            ),
            SimpleNamespace(
                pool_address="A",
                observed_at="2026-09-26T12:00:00+00:00",
                snapshot_lag_seconds=0.0,
                price=1.0,
                tvl_usd=100.0,
                volume_24h_usd=25.0,
                fees_24h_usd=1.0,
                history_observations=50,
                predictions=(
                    ("target_price_return", 0.3),
                    ("target_tvl_return", 0.4),
                ),
            ),
        )

    def to_record(self):
        return {
            "research_only": True,
            "policy_actionable": False,
            "execution_wired": False,
        }


def _add(frame: pd.DataFrame, values: dict[str, tuple[float, float]]):
    out = frame.copy()
    for column, pair in values.items():
        out[column] = [
            pair[0] if pool == "B" else pair[1]
            for pool in out["pool_address"]
        ]
    return out


def test_current_candidate_evidence_consolidates_without_ranking(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        module,
        "build_current_pool_market_forecast_from_store",
        lambda *args, **kwargs: _Forecast(),
    )

    def api(*args, **kwargs):
        frame = args[1]
        return _add(
            frame,
            {
                "api_snapshot_age_seconds": (10.0, 20.0),
                **{
                    col: (1.0, 1.0)
                    for col in module.POOL_API_METADATA_FEATURE_COLUMNS
                    if col != "api_snapshot_age_seconds"
                },
            },
        ), None

    def mint(*args, **kwargs):
        frame = args[1]
        values = {
            col: (1.0, 1.0)
            for col in module.TOKEN_MINT_CONTEXT_FEATURE_COLUMNS
        }
        values["mint_chain_snapshot_age_seconds"] = (1.0, 1.0)
        values["mint_x_snapshot_age_seconds"] = (1.0, 1.0)
        values["mint_y_snapshot_age_seconds"] = (1.0, np.nan)
        return _add(frame, values), None

    def chain(*args, **kwargs):
        frame = args[1]
        values = {
            col: (1.0, 1.0)
            for col in module.POOL_CHAIN_CONTEXT_FEATURE_COLUMNS
        }
        values["chain_snapshot_age_seconds"] = (1.0, 1.0)
        return _add(frame, values), None

    def execution(*args, **kwargs):
        frame = args[1]
        values = {
            col: (1.0, 1.0)
            for col in module.POOL_EXECUTION_COST_FEATURE_COLUMNS
        }
        values["execution_history_samples"] = (1.0, np.nan)
        return _add(frame, values), None

    monkeypatch.setattr(
        module,
        "attach_pool_api_metadata_from_store",
        api,
    )
    monkeypatch.setattr(
        module,
        "attach_token_mint_context_from_store",
        mint,
    )
    monkeypatch.setattr(
        module,
        "attach_pool_chain_context_from_store",
        chain,
    )
    monkeypatch.setattr(
        module,
        "attach_execution_cost_context_from_store",
        execution,
    )

    report = module.build_current_pool_candidate_evidence(
        "unused.db"
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert [item.pool_address for item in report.candidates] == [
        "A",
        "B",
    ]
    assert report.pools_seen == 2
    assert report.pools_with_api_context == 2
    assert report.pools_with_mint_context == 1
    assert report.pools_with_chain_context == 2
    assert report.pools_with_execution_context == 1
    assert report.pools_with_all_context_groups == 1

    a = report.candidates[0]
    b = report.candidates[1]
    assert a.market_snapshot_lag_seconds == 0.0
    assert a.mint_context_available is False
    assert a.execution_context_available is False
    assert a.complete_context_groups == 2
    assert b.complete_context_groups == 4

    record = report.to_record()
    text = str(record).lower()
    assert "selected_pool" not in text
    assert "recommended_action" not in text
    assert "allocation" not in text
    assert "winner" not in text
    assert "rank" not in record


def test_candidate_context_serializes_missing_values_as_null(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        module,
        "build_current_pool_market_forecast_from_store",
        lambda *args, **kwargs: _Forecast(),
    )

    def missing(frame):
        out = frame.copy()
        for column in (
            *module.POOL_API_METADATA_FEATURE_COLUMNS,
            *module.TOKEN_MINT_CONTEXT_FEATURE_COLUMNS,
            *module.POOL_CHAIN_CONTEXT_FEATURE_COLUMNS,
            *module.POOL_EXECUTION_COST_FEATURE_COLUMNS,
        ):
            out[column] = np.nan
        return out, None

    monkeypatch.setattr(
        module,
        "attach_pool_api_metadata_from_store",
        lambda db, frame: missing(frame),
    )
    monkeypatch.setattr(
        module,
        "attach_token_mint_context_from_store",
        lambda db, frame: missing(frame),
    )
    monkeypatch.setattr(
        module,
        "attach_pool_chain_context_from_store",
        lambda db, frame: missing(frame),
    )
    monkeypatch.setattr(
        module,
        "attach_execution_cost_context_from_store",
        lambda db, frame: missing(frame),
    )

    report = module.build_current_pool_candidate_evidence(
        "unused.db"
    )
    record = report.to_record()

    assert report.pools_with_all_context_groups == 0
    assert all(
        value is None
        for value in record["candidates"][0][
            "execution_context"
        ].values()
    )
