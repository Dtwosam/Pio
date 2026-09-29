from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from meteora_learner.ml_dataset import ML_FEATURE_COLUMNS
from meteora_learner.ml_inference import (
    MLCandidatePrediction,
    MLInferenceReport,
)


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "python-learner"
    / "src"
    / "meteora_learner"
    / "ml_current_candidates.py"
)

SPEC = importlib.util.spec_from_file_location(
    "meteora_learner.ml_current_candidates_test_target",
    TOOL,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


TIMES = (
    "2026-09-29T20:00:00+00:00",
    "2026-09-29T20:05:00+00:00",
    "2026-09-29T20:10:00+00:00",
)


class _Store:
    def __init__(self, path):
        self.path = Path(path)

    def chain_observation_times(
        self,
        pool_address,
        *,
        limit=None,
        ascending=True,
    ):
        assert pool_address == "pool-1"
        assert limit is None
        assert ascending is True
        return list(TIMES)

    def chain_pool_snapshot_at(self, pool_address, observed_at):
        assert pool_address == "pool-1"
        if observed_at == TIMES[1]:
            return {
                "active_bin_id": 100,
                "deposit_total_fee_rate": "2500000",
            }
        if observed_at == TIMES[2]:
            return {
                "active_bin_id": 101,
                "deposit_total_fee_rate": "2500000",
            }
        return {
            "active_bin_id": 99,
            "deposit_total_fee_rate": "2500000",
        }

    def load_bin_liquidity(self, pool_address, *, observed_at):
        assert pool_address == "pool-1"
        return [{"bin_id": 100, "observed_at": observed_at}]

    def bin_liquidity_at(
        self,
        pool_address,
        *,
        observed_at,
        bin_id,
    ):
        assert pool_address == "pool-1"
        assert observed_at in TIMES
        assert bin_id in {100, 101}
        return {"price": "18446744073709551616"}


def _shape():
    return SimpleNamespace(
        total_liquidity_supply=1000,
        active_liquidity_supply=250,
        occupied_bins=7,
        near_active_liquidity_ratio=0.60,
        below_active_liquidity_ratio=0.20,
        above_active_liquidity_ratio=0.20,
        liquidity_weighted_distance_bins=1.25,
    )


def _activity():
    return SimpleNamespace(
        bins_with_x_growth=2,
        bins_with_y_growth=3,
    )


def _candidate():
    replay = SimpleNamespace(
        start_observed_at=TIMES[1],
        end_observed_at=TIMES[2],
        start_active_bin_id=100,
        end_active_bin_id=101,
        max_observed_share_bps=75,
    )
    return SimpleNamespace(
        strategy="SPOT",
        half_width=2,
        center_offset=0,
        min_bin_id=98,
        max_bin_id=102,
        status="ACCEPTED",
        rejection_reason=None,
        range_survival_ratio=0.80,
        replay=replay,
    )


def _scan(candidate=None):
    return SimpleNamespace(
        attempted=1,
        accepted=1,
        rejected=0,
        candidates=(candidate if candidate is not None else _candidate(),),
    )


def _economics(*, unvalued=False):
    return SimpleNamespace(
        excess_vs_hold_bps=12,
        net_return_bps=8,
        has_unvalued_rewards=unvalued,
    )


def _patch_frame_dependencies(monkeypatch, *, unvalued=False):
    monkeypatch.setattr(MODULE, "ResearchStore", _Store)
    monkeypatch.setattr(
        MODULE,
        "summarize_liquidity_shape",
        lambda *args, **kwargs: _shape(),
    )
    monkeypatch.setattr(
        MODULE,
        "fee_checkpoint_activity",
        lambda *args, **kwargs: _activity(),
    )

    observed = {}

    def scan(*args, **kwargs):
        observed["observation_times"] = tuple(kwargs["observation_times"])
        return _scan()

    monkeypatch.setattr(MODULE, "scan_chain_candidates", scan)
    monkeypatch.setattr(
        MODULE,
        "replay_economics",
        lambda *args, **kwargs: _economics(unvalued=unvalued),
    )
    return observed


def test_current_frame_uses_latest_trailing_window_only(
    monkeypatch,
    tmp_path,
):
    observed = _patch_frame_dependencies(monkeypatch)

    report = MODULE.build_current_ml_candidate_frame(
        str(tmp_path / "pio.db"),
        pool_address="pool-1",
        amount_x=10,
        amount_y=20,
        network_cost_y_atomic=5,
        lookback_observations=2,
    )

    assert observed["observation_times"] == TIMES[1:]
    assert report.previous_observed_at == TIMES[1]
    assert report.decision_observed_at == TIMES[2]
    assert report.observation_count == 2
    assert report.candidates_seen == 1
    assert report.candidates_built == 1
    assert report.candidates_dropped == 0
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.live_authorized is False

    row = report.rows[0]
    assert row["pool_address"] == "pool-1"
    assert row["decision_observed_at"] == TIMES[2]
    assert row["active_bin_id"] == 101
    assert row["active_bin_move_1"] == 1
    assert row["strategy_spot"] == 1
    assert row["strategy_curve"] == 0
    assert row["strategy_bid_ask"] == 0
    assert row["half_width"] == 2
    assert row["range_width_bins"] == 5
    assert row["active_liquidity_ratio"] == 0.25
    assert row["trailing_range_survival_ratio"] == 0.80
    assert row["trailing_excess_vs_hold_bps"] == 12
    assert row["trailing_net_return_bps"] == 8
    assert row["trailing_max_observed_share_bps"] == 75
    assert set(ML_FEATURE_COLUMNS).issubset(row)


def test_as_of_prevents_future_observation_use(monkeypatch, tmp_path):
    observed = _patch_frame_dependencies(monkeypatch)

    report = MODULE.build_current_ml_candidate_frame(
        str(tmp_path / "pio.db"),
        pool_address="pool-1",
        amount_x=10,
        amount_y=20,
        network_cost_y_atomic=5,
        lookback_observations=2,
        as_of=TIMES[1],
    )

    assert observed["observation_times"] == TIMES[:2]
    assert report.decision_observed_at == TIMES[1]


def test_unvalued_trailing_rewards_fail_closed_per_candidate(
    monkeypatch,
    tmp_path,
):
    _patch_frame_dependencies(monkeypatch, unvalued=True)

    report = MODULE.build_current_ml_candidate_frame(
        str(tmp_path / "pio.db"),
        pool_address="pool-1",
        amount_x=10,
        amount_y=20,
        network_cost_y_atomic=5,
        lookback_observations=2,
    )

    assert report.candidates_seen == 1
    assert report.candidates_built == 0
    assert report.candidates_dropped == 1
    assert report.drop_reasons == (("trailing_rewards_unvalued", 1),)


class _Storage:
    def __init__(self, path):
        self.path = Path(path)

    def model_registry_entry(self, model_id):
        assert model_id == "challenger-1"
        return {
            "status": "PAPER_CHALLENGER",
            "dataset_version": "dataset-v2",
        }


def _cycle(**overrides):
    value = {
        "cycle_id": "cycle-1",
        "status": "PAPER_CHALLENGER",
        "challenger_model_id": "challenger-1",
        "champion_model_id": "champion-1",
        "target_dataset_version": "dataset-v2",
    }
    value.update(overrides)
    return SimpleNamespace(**value)


def _frame_report():
    row = {
        "pool_address": "pool-1",
        "decision_observed_at": TIMES[2],
        "strategy": "SPOT",
        "half_width": 2,
        "center_offset": 0,
    }
    for column in ML_FEATURE_COLUMNS:
        row.setdefault(column, 1)
    return MODULE.MLCurrentCandidateFrameReport(
        pool_address="pool-1",
        decision_observed_at=TIMES[2],
        previous_observed_at=TIMES[1],
        observation_count=12,
        candidates_seen=1,
        candidates_built=1,
        candidates_dropped=0,
        drop_reasons=(),
        rows=(row,),
        research_only=True,
        policy_actionable=False,
        live_authorized=False,
    )


def _prediction():
    return MLCandidatePrediction(
        row_index=0,
        pool_address="pool-1",
        decision_observed_at=TIMES[2],
        strategy="SPOT",
        half_width=2,
        center_offset=0,
        predicted_net_return_bps=20.0,
        predicted_excess_vs_hold_bps=15.0,
        predicted_downside_bps=2.0,
        predicted_range_survival=0.8,
        predicted_positive_excess_probability=0.7,
        risk_adjusted_score_bps=12.0,
        eligible=True,
        rejection_reasons=(),
    )


def _patch_score_dependencies(monkeypatch, *, cycle=None):
    monkeypatch.setattr(
        MODULE,
        "active_retraining_cycle",
        lambda storage: cycle if cycle is not None else _cycle(),
    )
    monkeypatch.setattr(
        MODULE,
        "load_registered_ml_v1",
        lambda storage, model_id: SimpleNamespace(
            feature_columns=ML_FEATURE_COLUMNS,
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "build_current_ml_candidate_frame",
        lambda *args, **kwargs: _frame_report(),
    )
    monkeypatch.setattr(
        MODULE,
        "score_ml_candidates",
        lambda bundle, frame, config: MLInferenceReport(
            candidates_seen=1,
            candidates_eligible=1,
            research_choice=_prediction(),
            policy_actionable=False,
            ranking_rule="test",
            predictions=(_prediction(),),
        ),
    )


def test_scoring_is_bound_to_active_paper_cycle_and_model(
    monkeypatch,
    tmp_path,
):
    _patch_score_dependencies(monkeypatch)
    storage = _Storage(tmp_path / "pio.db")

    decision = MODULE.score_current_registered_paper_challenger(
        storage,
        cycle_id="cycle-1",
        model_id="challenger-1",
        pool_address="pool-1",
        amount_x=10,
        amount_y=20,
        network_cost_y_atomic=5,
    )

    assert decision.cycle_id == "cycle-1"
    assert decision.model_id == "challenger-1"
    assert decision.champion_model_id == "champion-1"
    assert decision.target_dataset_version == "dataset-v2"
    assert decision.selection_ready is True
    assert decision.paper_entry_authorized is False
    assert decision.paper_only is True
    assert decision.research_only is True
    assert decision.policy_actionable is False
    assert decision.live_authorized is False
    assert decision.inference.research_choice is not None


def test_scoring_rejects_non_active_cycle(monkeypatch, tmp_path):
    _patch_score_dependencies(
        monkeypatch,
        cycle=_cycle(cycle_id="cycle-2"),
    )
    storage = _Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="not the active cycle"):
        MODULE.score_current_registered_paper_challenger(
            storage,
            cycle_id="cycle-1",
            model_id="challenger-1",
            pool_address="pool-1",
            amount_x=10,
            amount_y=20,
            network_cost_y_atomic=5,
        )


def test_scoring_rejects_cycle_model_mismatch(monkeypatch, tmp_path):
    _patch_score_dependencies(
        monkeypatch,
        cycle=_cycle(challenger_model_id="challenger-2"),
    )
    storage = _Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="challenger/model mismatch"):
        MODULE.score_current_registered_paper_challenger(
            storage,
            cycle_id="cycle-1",
            model_id="challenger-1",
            pool_address="pool-1",
            amount_x=10,
            amount_y=20,
            network_cost_y_atomic=5,
        )


def test_scoring_rejects_changed_feature_contract(monkeypatch, tmp_path):
    _patch_score_dependencies(monkeypatch)
    monkeypatch.setattr(
        MODULE,
        "load_registered_ml_v1",
        lambda storage, model_id: SimpleNamespace(
            feature_columns=tuple(ML_FEATURE_COLUMNS[:-1]),
        ),
    )
    storage = _Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="feature contract changed"):
        MODULE.score_current_registered_paper_challenger(
            storage,
            cycle_id="cycle-1",
            model_id="challenger-1",
            pool_address="pool-1",
            amount_x=10,
            amount_y=20,
            network_cost_y_atomic=5,
        )


def test_current_inference_has_no_paper_or_live_mutation_primitive():
    source = TOOL.read_text(encoding="utf-8")

    assert "open_paper_position(" not in source
    assert "_open_paper_position_in_conn(" not in source
    assert "create_paper_account(" not in source
    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert "paper_entry_authorized=False" in source
    assert "policy_actionable=False" in source
    assert "live_authorized=False" in source
