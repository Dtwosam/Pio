from dataclasses import replace
from types import SimpleNamespace

import pytest

from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_action_replay import (
    PAPER_HOLD_VS_REBALANCE_EVIDENCE_TYPE,
    compare_paper_hold_vs_rebalance_gross,
    persist_paper_hold_vs_rebalance_gross,
)
from meteora_learner.storage import Storage


DECISION = "2026-09-26T10:00:00+00:00"
END = "2026-09-26T10:05:00+00:00"


def prior():
    return SimpleNamespace(
        pool_address="pool",
        end_observed_at=DECISION,
        max_share_bps=500,
    )


def hold_result(*, reward_one=0):
    return SimpleNamespace(
        start_position_x=10,
        start_position_y=20,
        idle_x=1,
        idle_y=2,
        start_active_bin_id=100,
        end_active_bin_id=101,
        ending_x=11,
        ending_y=21,
        fee_x=2,
        fee_y=3,
        reward_one=reward_one,
        reward_two=0,
        replay_fidelity="EXISTING_SMALL_LP_CONTINUATION_V1",
    )


def rebalance_result(*, reward_one=0):
    return SimpleNamespace(
        ending_x=12,
        ending_y=22,
        idle_x=1,
        idle_y=2,
        fee_x=3,
        fee_y=4,
        reward_one=reward_one,
        reward_two=0,
        entry_composition_fee_x=1,
        entry_composition_fee_y=1,
        replay_fidelity="SMALL_LP_CHAIN_PATH_V2",
    )


class FakeStore:
    def __init__(self, _):
        pass

    def bin_liquidity_at(
        self,
        pool_address,
        *,
        observed_at,
        bin_id,
    ):
        assert pool_address == "pool"
        assert observed_at in {DECISION, END}
        assert bin_id in {100, 101}
        return {"price": str(Q64)}


def install_fakes(monkeypatch, *, hold=None, rebalance=None):
    hold = hold or hold_result()
    rebalance = rebalance or rebalance_result()

    monkeypatch.setattr(
        "meteora_learner.paper_action_replay.continue_small_lp_replay",
        lambda *args, **kwargs: hold,
    )

    def fake_rebalance(*args, **kwargs):
        assert kwargs["amount_x"] == 11
        assert kwargs["amount_y"] == 22
        assert kwargs["observation_times"] == [DECISION, END]
        assert kwargs["max_share_bps"] == 500
        return rebalance

    monkeypatch.setattr(
        "meteora_learner.paper_action_replay.replay_small_lp_history",
        fake_rebalance,
    )
    monkeypatch.setattr(
        "meteora_learner.paper_action_replay.ResearchStore",
        FakeStore,
    )


def test_gross_comparison_uses_same_inventory_and_forward_window(
    tmp_path,
    monkeypatch,
):
    install_fakes(monkeypatch)

    report = compare_paper_hold_vs_rebalance_gross(
        tmp_path / "pio.db",
        prior=prior(),
        observation_times=[DECISION, END],
        min_bin_id=99,
        max_bin_id=103,
        strategy="SPOT",
    )

    # Q64 price == 1 Y per X in this fixture.
    assert report.start_x == 11
    assert report.start_y == 22
    assert report.start_value_y_atomic == 33

    # HOLD: (11+1+2) X + (21+2+3) Y = 40 Y.
    assert report.hold_inventory_fee_value_y_atomic == 40

    # Rebalance before composition:
    # (12+1+3) X + (22+2+4) Y = 44 Y.
    assert report.rebalance_inventory_fee_value_y_atomic == 44
    # Composition fee: 1 X + 1 Y = 2 Y.
    assert report.rebalance_composition_cost_y_atomic == 2
    assert report.rebalance_value_after_composition_y_atomic == 42
    assert report.gross_advantage_before_transition_cost_y_atomic == 2
    assert report.gross_advantage_before_transition_cost_bps == 500

    assert report.reward_value_complete is True
    assert report.transition_cost_complete is False
    assert report.economics_complete is False
    assert report.complete_economic_components == (
        "HOLD_INVENTORY_AND_FEE_VALUE",
        "REBALANCE_INVENTORY_AND_FEE_VALUE",
        "REBALANCE_COMPOSITION_COST",
    )
    assert report.incomplete_economic_components == (
        "REBALANCE_TRANSITION_COST",
    )
    assert report.paper_only is True
    assert report.actionable is False
    assert report.live_authorized is False
    assert report.status == "GROSS_COMPARISON_TRANSITION_COST_INCOMPLETE"


def test_nonzero_rewards_keep_gross_economics_incomplete(
    tmp_path,
    monkeypatch,
):
    install_fakes(
        monkeypatch,
        hold=hold_result(reward_one=7),
        rebalance=rebalance_result(reward_one=8),
    )

    report = compare_paper_hold_vs_rebalance_gross(
        tmp_path / "pio.db",
        prior=prior(),
        observation_times=[DECISION, END],
        min_bin_id=99,
        max_bin_id=103,
        strategy="CURVE",
    )

    assert report.reward_value_complete is False
    assert report.economics_complete is False
    assert report.incomplete_economic_components == (
        "REBALANCE_TRANSITION_COST",
        "REWARD_VALUATION",
    )
    assert report.actionable is False


def test_gross_comparison_requires_forward_window_from_prior_end(
    tmp_path,
    monkeypatch,
):
    install_fakes(monkeypatch)

    with pytest.raises(ValueError, match="prior replay end"):
        compare_paper_hold_vs_rebalance_gross(
            tmp_path / "pio.db",
            prior=prior(),
            observation_times=[
                "2026-09-26T09:55:00+00:00",
                END,
            ],
            min_bin_id=99,
            max_bin_id=103,
            strategy="SPOT",
        )


def test_research_evidence_persists_non_qualified(
    tmp_path,
    monkeypatch,
):
    install_fakes(monkeypatch)
    storage = Storage(tmp_path / "pio.db")
    report = compare_paper_hold_vs_rebalance_gross(
        storage.path,
        prior=prior(),
        observation_times=[DECISION, END],
        min_bin_id=99,
        max_bin_id=103,
        strategy="SPOT",
    )

    evidence_id = persist_paper_hold_vs_rebalance_gross(
        storage,
        report=report,
    )

    assert evidence_id > 0
    saved = storage.latest_advanced_edge_evidence(
        edge_type=PAPER_HOLD_VS_REBALANCE_EVIDENCE_TYPE,
        pool_address="pool",
    )
    assert saved is not None
    assert saved["qualified"] is False
    assert saved["evidence"]["actionable"] is False
    assert saved["evidence"]["transition_cost_complete"] is False


def test_research_persistence_rejects_actionability_leak(
    tmp_path,
    monkeypatch,
):
    install_fakes(monkeypatch)
    storage = Storage(tmp_path / "pio.db")
    report = compare_paper_hold_vs_rebalance_gross(
        storage.path,
        prior=prior(),
        observation_times=[DECISION, END],
        min_bin_id=99,
        max_bin_id=103,
        strategy="SPOT",
    )

    with pytest.raises(ValueError, match="research-only boundary"):
        persist_paper_hold_vs_rebalance_gross(
            storage,
            report=replace(report, actionable=True),
        )
