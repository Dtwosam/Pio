from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "python-learner"
    / "src"
    / "meteora_learner"
    / "ml_current_candidates.py"
)

SPEC = importlib.util.spec_from_file_location(
    "meteora_learner.ml_current_candidates_test_target",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


T1 = "2026-09-29T20:00:00+00:00"
T2 = "2026-09-29T20:05:00+00:00"
T3 = "2026-09-29T20:10:00+00:00"


class FakeStore:
    snapshot_calls: list[str] = []

    def __init__(self, path):
        self.path = path

    def chain_observation_times(self, pool_address, limit=None, ascending=True):
        return [T1, T2, T3]

    def chain_pool_snapshot_at(self, pool_address, observed_at):
        self.snapshot_calls.append(observed_at)
        active = 100 if observed_at == T1 else 101 if observed_at == T2 else 102
        return {
            "active_bin_id": active,
            "deposit_total_fee_rate": "100000",
        }

    def load_bin_liquidity(self, pool_address, observed_at):
        return [{"bin_id": 1}]

    def bin_liquidity_at(
        self,
        pool_address,
        *,
        observed_at,
        bin_id,
    ):
        return {"price": "18446744073709551616"}


def _candidate(*, status="ACCEPTED"):
    replay = SimpleNamespace(
        start_observed_at=T1,
        end_observed_at=T2,
        start_active_bin_id=100,
        end_active_bin_id=101,
        max_observed_share_bps=75,
    )
    return SimpleNamespace(
        status=status,
        replay=replay if status == "ACCEPTED" else None,
        range_survival_ratio=0.75 if status == "ACCEPTED" else None,
        strategy="SPOT",
        half_width=2,
        center_offset=0,
        min_bin_id=99,
        max_bin_id=103,
    )


def _install(monkeypatch, *, candidates=None):
    FakeStore.snapshot_calls = []
    chosen = _candidate()
    candidate_values = (
        candidates
        if candidates is not None
        else [chosen, _candidate(status="REJECTED")]
    )

    monkeypatch.setattr(MODULE, "ResearchStore", FakeStore)
    monkeypatch.setattr(
        MODULE,
        "summarize_liquidity_shape",
        lambda rows, active_bin_id, near_radius: SimpleNamespace(
            total_liquidity_supply=1000,
            active_liquidity_supply=250,
            occupied_bins=5,
            near_active_liquidity_ratio=0.8,
            below_active_liquidity_ratio=0.4,
            above_active_liquidity_ratio=0.6,
            liquidity_weighted_distance_bins=1.25,
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "fee_checkpoint_activity",
        lambda previous, current: SimpleNamespace(
            bins_with_x_growth=2,
            bins_with_y_growth=3,
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "scan_chain_candidates",
        lambda *args, **kwargs: SimpleNamespace(
            candidates=candidate_values,
            attempted=len(candidate_values),
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "select_deterministic_baseline",
        lambda *args, **kwargs: SimpleNamespace(
            research_choice=chosen,
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "replay_economics",
        lambda *args, **kwargs: SimpleNamespace(
            excess_vs_hold_bps=120,
            net_return_bps=90,
            has_unvalued_rewards=False,
        ),
    )


def test_current_candidate_frame_uses_trailing_state_only(monkeypatch):
    _install(monkeypatch)

    result = MODULE.build_current_ml_candidate_frame(
        "/tmp/pio.db",
        pool_address="pool-1",
        amount_x=100,
        amount_y=200,
        network_cost_y_atomic=3,
        lookback_observations=2,
        as_of=T2,
    )
    frame = result.to_frame()

    assert result.no_lookahead is True
    assert result.previous_observed_at == T1
    assert result.decision_observed_at == T2
    assert result.candidates_seen == 2
    assert result.candidates_built == 1
    assert result.candidates_dropped == 1
    assert ("trailing_replay_rejected", 1) in result.drop_reasons

    assert len(frame) == 1
    assert frame.columns.is_unique
    assert tuple(frame.columns) == MODULE.CURRENT_CANDIDATE_COLUMNS
    assert not any(column.startswith("target_") for column in frame.columns)
    assert frame.iloc[0]["baseline_selected"] == 1
    assert frame.iloc[0]["active_bin_id"] == 101
    assert frame.iloc[0]["active_bin_move_1"] == 1
    assert frame.iloc[0]["trailing_excess_vs_hold_bps"] == 120
    assert frame.iloc[0]["trailing_net_return_bps"] == 90
    assert frame.iloc[0]["trailing_max_observed_share_bps"] == 75
    assert set(FakeStore.snapshot_calls) == {T1, T2}
    assert T3 not in FakeStore.snapshot_calls


def test_current_candidate_frame_contains_every_ml_feature(monkeypatch):
    _install(monkeypatch)
    frame = MODULE.build_current_ml_candidate_frame(
        "/tmp/pio.db",
        pool_address="pool-1",
        amount_x=100,
        amount_y=200,
        network_cost_y_atomic=3,
        lookback_observations=2,
        as_of=T2,
    ).to_frame()

    assert set(MODULE.ML_FEATURE_COLUMNS).issubset(frame.columns)
    assert {
        "pool_address",
        "decision_observed_at",
        "strategy",
        "half_width",
        "center_offset",
    }.issubset(frame.columns)


def test_current_candidate_frame_refuses_when_no_replay_valid_candidates(
    monkeypatch,
):
    _install(monkeypatch, candidates=[_candidate(status="REJECTED")])

    with pytest.raises(ValueError, match="no replay-valid candidates"):
        MODULE.build_current_ml_candidate_frame(
            "/tmp/pio.db",
            pool_address="pool-1",
            amount_x=100,
            amount_y=200,
            network_cost_y_atomic=3,
            lookback_observations=2,
            as_of=T2,
        )


def test_current_candidate_frame_requires_positive_atomic_notional(
    monkeypatch,
):
    _install(monkeypatch)

    with pytest.raises(ValueError, match="token amount"):
        MODULE.build_current_ml_candidate_frame(
            "/tmp/pio.db",
            pool_address="pool-1",
            amount_x=0,
            amount_y=0,
            network_cost_y_atomic=3,
        )


def test_current_candidate_frame_rejects_future_as_of_without_enough_history(
    monkeypatch,
):
    _install(monkeypatch)

    with pytest.raises(ValueError, match="not enough chain observations"):
        MODULE.build_current_ml_candidate_frame(
            "/tmp/pio.db",
            pool_address="pool-1",
            amount_x=100,
            amount_y=200,
            network_cost_y_atomic=3,
            lookback_observations=2,
            as_of="2026-09-29T19:00:00+00:00",
        )
