from dataclasses import replace
from types import SimpleNamespace

import pytest

from meteora_learner.ml_dataset import MLTrainingExample
from meteora_learner.paper_candidate_cycle import (
    PAPER_EMPIRICAL_CANDIDATE_CYCLE_EVIDENCE_TYPE,
    persist_empirical_paper_candidate_cycle,
    run_empirical_paper_candidate_cycle,
)
from meteora_learner.paper_candidate_policy import PaperCandidateArm
from meteora_learner.storage import Storage


POOL = "pool"
T0 = "2026-09-26T09:55:00+00:00"
T1 = "2026-09-26T10:00:00+00:00"
T2 = "2026-09-26T10:05:00+00:00"


class FakeStore:
    def chain_observation_times(self, address, *, limit=None, ascending=False):
        assert address == POOL
        values = [T0, T1, T2]
        if not ascending:
            values = list(reversed(values))
        return values if limit is None else values[:limit]

    def chain_pool_snapshot_at(self, address, observed_at):
        assert address == POOL
        active = {T0: 98, T1: 99, T2: 100}[observed_at]
        return {"active_bin_id": active}

    def load_bin_liquidity(self, address, *, observed_at=None):
        assert address == POOL
        fee = "1" if observed_at == T2 else "0"
        return [
            {
                "bin_id": 99,
                "liquidity_supply": "10",
                "fee_amount_x_per_token_stored": fee,
                "fee_amount_y_per_token_stored": "0",
            },
            {
                "bin_id": 100,
                "liquidity_supply": "10",
                "fee_amount_x_per_token_stored": fee,
                "fee_amount_y_per_token_stored": "0",
            },
            {
                "bin_id": 101,
                "liquidity_supply": "20",
                "fee_amount_x_per_token_stored": fee,
                "fee_amount_y_per_token_stored": "0",
            },
        ]


def example(strategy, excess):
    return MLTrainingExample(
        pool_address=POOL,
        decision_observed_at="2026-09-26T09:40:00+00:00",
        forward_end_observed_at="2026-09-26T09:50:00+00:00",
        strategy=strategy,
        baseline_selected=1 if strategy == "SPOT" else 0,
        strategy_spot=1 if strategy == "SPOT" else 0,
        strategy_curve=1 if strategy == "CURVE" else 0,
        strategy_bid_ask=0,
        half_width=1,
        center_offset=0,
        range_width_bins=3,
        active_bin_id=100,
        active_bin_move_1=1,
        deposit_fee_rate_bps=1.0,
        occupied_bins=3,
        active_liquidity_ratio=0.25,
        near_active_liquidity_ratio=1.0,
        below_active_liquidity_ratio=0.25,
        above_active_liquidity_ratio=0.50,
        liquidity_weighted_distance_bins=0.75,
        fee_growth_bins_x=3,
        fee_growth_bins_y=0,
        trailing_range_survival_ratio=1.0,
        trailing_excess_vs_hold_bps=0,
        trailing_net_return_bps=0,
        trailing_max_observed_share_bps=100,
        target_net_return_bps=excess + 5,
        target_excess_vs_hold_bps=excess,
        target_range_survival_ratio=1.0,
        target_positive_excess=int(excess > 0),
    )


def install_cycle_fakes(monkeypatch):
    monkeypatch.setattr(
        "meteora_learner.paper_candidate_cycle.ResearchStore",
        lambda _: FakeStore(),
    )

    def fake_scan(*args, **kwargs):
        assert kwargs["observation_times"] == [T1, T2]
        return SimpleNamespace(
            attempted=3,
            accepted=2,
            rejected=1,
            candidates=(
                SimpleNamespace(
                    strategy="SPOT",
                    half_width=1,
                    center_offset=0,
                    status="ACCEPTED",
                ),
                SimpleNamespace(
                    strategy="CURVE",
                    half_width=1,
                    center_offset=0,
                    status="ACCEPTED",
                ),
                SimpleNamespace(
                    strategy="BID_ASK",
                    half_width=1,
                    center_offset=0,
                    status="REJECTED",
                ),
            ),
        )

    monkeypatch.setattr(
        "meteora_learner.paper_candidate_cycle.scan_chain_candidates",
        fake_scan,
    )

    def fake_dataset(*args, **kwargs):
        assert kwargs["max_observed_at"] == T2
        return SimpleNamespace(
            examples=(example("SPOT", 5), example("CURVE", 20)),
            decision_points=4,
            candidates_dropped=3,
        )

    monkeypatch.setattr(
        "meteora_learner.paper_candidate_cycle.build_ml_action_dataset",
        fake_dataset,
    )


def test_cycle_uses_current_chain_context_and_completed_history_only(
    tmp_path,
    monkeypatch,
):
    install_cycle_fakes(monkeypatch)

    report = run_empirical_paper_candidate_cycle(
        tmp_path / "pio.db",
        pool_address=POOL,
        amount_x=100,
        amount_y=200,
        network_cost_y_atomic=7,
        lookback_observations=2,
        half_widths=(1,),
        strategies=("SPOT", "CURVE", "BID_ASK"),
    )

    assert report.decision_observed_at == T2
    assert report.context.active_bin_move_1 == 1
    assert report.context.context_key == "UP|ABOVE|ACTIVE"
    assert report.current_candidates_seen == 3
    assert report.current_candidates_accepted == 2
    assert report.current_candidates_rejected == 1
    assert report.history_examples == 2
    assert report.selection.status == "PAPER_EMPIRICAL_SELECTION"
    assert report.selection.selected_arm == PaperCandidateArm("CURVE", 1, 0)
    assert report.paper_only is True
    assert report.policy_actionable is False
    assert report.live_authorized is False


def test_cycle_honors_as_of_cutoff_for_current_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "meteora_learner.paper_candidate_cycle.ResearchStore",
        lambda _: FakeStore(),
    )

    def fake_scan(*args, **kwargs):
        assert kwargs["observation_times"] == [T0, T1]
        return SimpleNamespace(
            attempted=1,
            accepted=1,
            rejected=0,
            candidates=(
                SimpleNamespace(
                    strategy="SPOT",
                    half_width=1,
                    center_offset=0,
                    status="ACCEPTED",
                ),
            ),
        )

    monkeypatch.setattr(
        "meteora_learner.paper_candidate_cycle.scan_chain_candidates",
        fake_scan,
    )
    monkeypatch.setattr(
        "meteora_learner.paper_candidate_cycle.build_ml_action_dataset",
        lambda *args, **kwargs: SimpleNamespace(
            examples=(),
            decision_points=0,
            candidates_dropped=0,
        ),
    )

    report = run_empirical_paper_candidate_cycle(
        tmp_path / "pio.db",
        pool_address=POOL,
        amount_x=1,
        amount_y=1,
        network_cost_y_atomic=0,
        lookback_observations=2,
        half_widths=(1,),
        strategies=("SPOT",),
        as_of="2026-09-26T10:02:00+00:00",
    )

    assert report.decision_observed_at == T1
    assert report.selection.status == "INSUFFICIENT_CONTEXT_EVIDENCE"
    assert report.selection.selected_arm is None


def test_cycle_persistence_is_explicitly_non_qualified(tmp_path, monkeypatch):
    install_cycle_fakes(monkeypatch)
    storage = Storage(tmp_path / "pio.db")
    report = run_empirical_paper_candidate_cycle(
        storage.path,
        pool_address=POOL,
        amount_x=100,
        amount_y=200,
        network_cost_y_atomic=7,
        lookback_observations=2,
        half_widths=(1,),
        strategies=("SPOT", "CURVE", "BID_ASK"),
    )

    evidence_id = persist_empirical_paper_candidate_cycle(
        storage,
        report=report,
    )
    assert evidence_id > 0
    saved = storage.latest_advanced_edge_evidence(
        edge_type=PAPER_EMPIRICAL_CANDIDATE_CYCLE_EVIDENCE_TYPE,
        pool_address=POOL,
    )
    assert saved is not None
    assert saved["qualified"] is False
    assert saved["status"] == "PAPER_EMPIRICAL_SELECTION"
    assert saved["as_of"] == T2
    assert saved["evidence"]["live_authorized"] is False


def test_cycle_persistence_rejects_live_boundary_crossing(
    tmp_path,
    monkeypatch,
):
    install_cycle_fakes(monkeypatch)
    storage = Storage(tmp_path / "pio.db")
    report = run_empirical_paper_candidate_cycle(
        storage.path,
        pool_address=POOL,
        amount_x=100,
        amount_y=200,
        network_cost_y_atomic=7,
        lookback_observations=2,
        half_widths=(1,),
        strategies=("SPOT", "CURVE", "BID_ASK"),
    )

    with pytest.raises(ValueError, match="safety boundary"):
        persist_empirical_paper_candidate_cycle(
            storage,
            report=replace(report, live_authorized=True),
        )
