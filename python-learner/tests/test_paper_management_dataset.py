from types import SimpleNamespace

import pytest

from meteora_learner.paper_action_replay import (
    PAPER_HOLD_VS_REBALANCE_EVIDENCE_TYPE,
)
from meteora_learner.paper_management_dataset import (
    PAPER_MANAGEMENT_DATASET_EVIDENCE_TYPE,
    build_paper_management_dataset,
    build_persisted_paper_management_dataset,
    load_persisted_paper_management_reports,
    persist_paper_management_dataset,
)
from meteora_learner.storage import Storage


def report(
    *,
    pool="pool",
    gross=100.0,
    quote_complete=True,
    reward_complete=True,
    transition_complete=False,
    economics_complete=False,
    final_net=None,
    decision="2026-09-26T10:00:00+00:00",
    end="2026-09-26T10:05:00+00:00",
):
    values = {
        "pool_address": pool,
        "decision_observed_at": decision,
        "end_observed_at": end,
        "strategy": "SPOT",
        "min_bin_id": 99,
        "max_bin_id": 103,
        "decision_active_bin_id": 100,
        "rebalance_lower_offset_bins": -1,
        "rebalance_upper_offset_bins": 3,
        "start_x": 11,
        "start_y": 22,
        "start_value_y_atomic": 33,
        "quote_unit": "ACCOUNT_QUOTE",
        "quote_normalization_complete": quote_complete,
        "reward_value_complete": reward_complete,
        "transition_cost_complete": transition_complete,
        "economics_complete": economics_complete,
        "gross_advantage_before_transition_cost_quote": gross,
        "incomplete_economic_components": (
            ()
            if economics_complete
            else ("REBALANCE_TRANSITION_COST",)
        ),
        "paper_only": True,
        "actionable": False,
        "live_authorized": False,
    }
    if final_net is not None:
        values["net_advantage_after_all_costs_quote"] = final_net
    return SimpleNamespace(**values)


def test_positive_gross_advantage_is_not_a_training_label_when_costs_incomplete():
    dataset = build_paper_management_dataset(
        (report(gross=500.0),)
    )

    assert dataset.observations_seen == 1
    assert dataset.observations_recorded == 1
    assert dataset.training_examples_built == 0
    assert dataset.training_examples_dropped == 1
    assert dataset.drop_reasons == (("TRANSITION_COST_INCOMPLETE", 1),)

    observation = dataset.observations[0]
    assert observation.gross_advantage_before_transition_cost_quote == 500.0
    assert observation.training_eligible is False
    assert observation.target_action is None
    assert observation.target_net_advantage_after_all_costs_quote is None


def test_complete_positive_and_negative_net_outcomes_create_labels():
    dataset = build_paper_management_dataset(
        (
            report(
                gross=8.0,
                transition_complete=True,
                economics_complete=True,
                final_net=2.5,
            ),
            report(
                gross=8.0,
                transition_complete=True,
                economics_complete=True,
                final_net=-1.25,
                decision="2026-09-26T10:10:00+00:00",
                end="2026-09-26T10:15:00+00:00",
            ),
        )
    )

    assert dataset.training_examples_built == 2
    assert dataset.training_examples_dropped == 0
    assert [item.target_action for item in dataset.observations] == [
        "REBALANCE",
        "HOLD",
    ]
    assert [
        item.target_rebalance_wins for item in dataset.training_examples
    ] == [1, 0]
    assert dataset.training_examples[0].decision_active_bin_id == 100
    assert dataset.training_examples[0].rebalance_lower_offset_bins == -1
    assert dataset.training_examples[0].rebalance_upper_offset_bins == 3
    assert dataset.training_examples[0].target_net_advantage_after_all_costs_quote == pytest.approx(
        2.5
    )
    assert dataset.training_examples[1].target_net_advantage_after_all_costs_quote == pytest.approx(
        -1.25
    )


def test_realized_tie_is_not_forced_into_hold_or_rebalance_label():
    dataset = build_paper_management_dataset(
        (
            report(
                transition_complete=True,
                economics_complete=True,
                final_net=0.0,
            ),
        )
    )

    assert dataset.training_examples_built == 0
    assert dataset.drop_reasons == (("REALIZED_ACTION_TIE", 1),)
    assert dataset.observations[0].target_action is None


def test_quote_and_reward_incompleteness_block_before_labeling():
    dataset = build_paper_management_dataset(
        (
            report(
                quote_complete=False,
                transition_complete=True,
                economics_complete=True,
                final_net=4.0,
            ),
            report(
                reward_complete=False,
                transition_complete=True,
                economics_complete=True,
                final_net=4.0,
                decision="2026-09-26T10:10:00+00:00",
                end="2026-09-26T10:15:00+00:00",
            ),
        )
    )

    assert dataset.training_examples_built == 0
    assert dict(dataset.drop_reasons) == {
        "QUOTE_NORMALIZATION_INCOMPLETE": 1,
        "REWARD_VALUATION_INCOMPLETE": 1,
    }


def test_complete_economics_without_final_net_advantage_stays_unlabeled():
    dataset = build_paper_management_dataset(
        (
            report(
                transition_complete=True,
                economics_complete=True,
                final_net=None,
            ),
        )
    )

    assert dataset.training_examples_built == 0
    assert dataset.drop_reasons == (("FINAL_NET_ADVANTAGE_MISSING", 1),)


def test_management_dataset_rejects_lookahead_or_live_boundary_crossing():
    with pytest.raises(ValueError, match="end after"):
        build_paper_management_dataset(
            (
                report(
                    decision="2026-09-26T10:05:00+00:00",
                    end="2026-09-26T10:05:00+00:00",
                ),
            )
        )

    unsafe = report()
    unsafe.live_authorized = True
    with pytest.raises(ValueError, match="PAPER-only"):
        build_paper_management_dataset((unsafe,))


def test_management_dataset_persistence_is_non_qualified(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    dataset = build_paper_management_dataset((report(),))

    evidence_id = persist_paper_management_dataset(
        storage,
        pool_address="pool",
        report=dataset,
    )

    assert evidence_id > 0
    saved = storage.latest_advanced_edge_evidence(
        edge_type=PAPER_MANAGEMENT_DATASET_EVIDENCE_TYPE,
        pool_address="pool",
    )
    assert saved is not None
    assert saved["qualified"] is False
    assert saved["status"] == "LABELS_BLOCKED_INCOMPLETE_ECONOMICS"
    assert saved["evidence"]["training_examples_built"] == 0


def test_management_dataset_persistence_rejects_cross_pool_rows(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    dataset = build_paper_management_dataset((report(pool="other"),))

    with pytest.raises(ValueError, match="another pool"):
        persist_paper_management_dataset(
            storage,
            pool_address="pool",
            report=dataset,
        )



def test_persisted_legacy_gross_evidence_stays_unlabeled(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    legacy = {
        "pool_address": "pool",
        "decision_observed_at": "2026-09-26T10:00:00+00:00",
        "end_observed_at": "2026-09-26T10:05:00+00:00",
        "strategy": "SPOT",
        "min_bin_id": 99,
        "max_bin_id": 103,
        "start_x": 11,
        "start_y": 22,
        "start_value_y_atomic": 33,
        "gross_advantage_before_transition_cost_y_atomic": 999,
        "reward_value_complete": True,
        "transition_cost_complete": False,
        "economics_complete": False,
        "incomplete_economic_components": ["REBALANCE_TRANSITION_COST"],
        "paper_only": True,
        "actionable": False,
        "live_authorized": False,
    }
    storage.save_advanced_edge_evidence(
        edge_type=PAPER_HOLD_VS_REBALANCE_EVIDENCE_TYPE,
        pool_address="pool",
        as_of=legacy["decision_observed_at"],
        status="GROSS_COMPARISON_TRANSITION_COST_INCOMPLETE",
        qualified=False,
        evidence=legacy,
    )

    reports = load_persisted_paper_management_reports(
        storage,
        pool_address="pool",
    )
    assert len(reports) == 1

    dataset = build_persisted_paper_management_dataset(
        storage,
        pool_address="pool",
    )

    assert dataset.training_examples_built == 0
    assert dataset.drop_reasons == (
        ("QUOTE_NORMALIZATION_INCOMPLETE", 1),
    )
    observation = dataset.observations[0]
    assert observation.quote_unit == "UNKNOWN"
    assert observation.gross_advantage_before_transition_cost_quote is None
    assert observation.target_action is None


def test_persisted_dataset_requires_existing_gross_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="no persisted PAPER"):
        build_persisted_paper_management_dataset(
            storage,
            pool_address="pool",
        )



def test_complete_economics_without_decision_relative_context_stays_unlabeled():
    row = report(
        transition_complete=True,
        economics_complete=True,
        final_net=3.0,
    )
    del row.decision_active_bin_id
    del row.rebalance_lower_offset_bins
    del row.rebalance_upper_offset_bins

    dataset = build_paper_management_dataset((row,))

    assert dataset.training_examples_built == 0
    assert dataset.drop_reasons == (("DECISION_CONTEXT_INCOMPLETE", 1),)
    assert dataset.observations[0].training_eligible is False
