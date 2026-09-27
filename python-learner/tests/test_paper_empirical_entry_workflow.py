from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from meteora_learner.paper_candidate_cycle import (
    EmpiricalPaperCandidateCycleReport,
    PaperCandidateContext,
)
from meteora_learner.paper_candidate_policy import (
    EmpiricalPaperCandidateSelection,
    PaperCandidateArm,
)
from meteora_learner.paper_empirical_entry_workflow import (
    run_empirical_paper_entry_workflow,
)
from meteora_learner.storage import Storage


DECISION = "2026-09-27T08:00:00+00:00"


def _cycle(arm=True):
    selected = (
        PaperCandidateArm(strategy="SPOT", half_width=1, center_offset=0)
        if arm
        else None
    )
    return EmpiricalPaperCandidateCycleReport(
        pool_address="pool",
        decision_observed_at=DECISION,
        context=PaperCandidateContext(
            previous_observed_at="2026-09-27T07:55:00+00:00",
            decision_observed_at=DECISION,
            active_bin_id=10,
            active_bin_move_1=0,
            below_active_liquidity_ratio=0.5,
            above_active_liquidity_ratio=0.5,
            fee_growth_bins_x=0,
            fee_growth_bins_y=0,
            context_key="FLAT|BALANCED|QUIET",
        ),
        history_examples=0,
        history_decision_points=0,
        history_candidates_dropped=0,
        history_error=None,
        current_candidates_seen=1,
        current_candidates_accepted=1,
        current_candidates_rejected=0,
        selection=EmpiricalPaperCandidateSelection(
            pool_address="pool",
            decision_observed_at=DECISION,
            context_key="FLAT|BALANCED|QUIET",
            status=("PAPER_EXPLORATION" if arm else "INSUFFICIENT_CONTEXT_EVIDENCE"),
            selection_mode=("UNSEEN_ARM" if arm else "NO_SELECTION"),
            selected_arm=selected,
            paper_only=True,
            live_authorized=False,
            evidence=(),
        ),
        paper_only=True,
        policy_actionable=False,
        live_authorized=False,
    )


def test_workflow_opens_only_selected_paper_candidate(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seen = {}

    def candidate_runner(database_path, **kwargs):
        seen["database_path"] = database_path
        seen["candidate"] = kwargs
        return _cycle()

    def entry_opener(storage_arg, **kwargs):
        seen["entry"] = kwargs
        return SimpleNamespace(opened=True)

    report = run_empirical_paper_entry_workflow(
        storage,
        account_id="paper",
        position_id="pos",
        event_key="enter-pos",
        pool_address="pool",
        amount_x=10,
        amount_y=20,
        capital_quote=100,
        network_cost_y_atomic=5,
        as_of=DECISION,
        candidate_runner=candidate_runner,
        entry_opener=entry_opener,
    )

    assert seen["database_path"] == storage.path
    assert seen["candidate"]["pool_address"] == "pool"
    assert seen["candidate"]["as_of"] == DECISION
    assert seen["entry"]["report"].selection.selected_arm is not None
    assert seen["entry"]["capital_quote"] == 100
    assert report.status == "OPENED"
    assert report.paper_only is True
    assert report.live_authorized is False


def test_workflow_no_selection_never_calls_entry(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def entry_opener(*args, **kwargs):
        raise AssertionError("entry must not be called without a selection")

    report = run_empirical_paper_entry_workflow(
        storage,
        account_id="paper",
        position_id="pos",
        event_key="enter-pos",
        pool_address="pool",
        amount_x=10,
        amount_y=20,
        capital_quote=100,
        network_cost_y_atomic=5,
        candidate_runner=lambda database_path, **kwargs: _cycle(arm=False),
        entry_opener=entry_opener,
    )
    assert report.status == "NO_SELECTION"
    assert report.entry is None


def test_workflow_rejects_live_authorized_cycle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    bad = replace(_cycle(), live_authorized=True)

    with pytest.raises(ValueError, match="PAPER-only boundary"):
        run_empirical_paper_entry_workflow(
            storage,
            account_id="paper",
            position_id="pos",
            event_key="enter-pos",
            pool_address="pool",
            amount_x=10,
            amount_y=20,
            capital_quote=100,
            network_cost_y_atomic=5,
            candidate_runner=lambda database_path, **kwargs: bad,
        )
