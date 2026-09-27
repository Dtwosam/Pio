from __future__ import annotations

from dataclasses import replace

import pytest

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import create_paper_account
from meteora_learner.paper_candidate_cycle import (
    EmpiricalPaperCandidateCycleReport,
    PaperCandidateContext,
)
from meteora_learner.paper_candidate_policy import (
    EmpiricalPaperCandidateSelection,
    PaperCandidateArm,
)
from meteora_learner.paper_empirical_entry import open_empirical_paper_candidate
from meteora_learner.storage import Storage


DECISION = "2026-09-27T08:00:00+00:00"


def _save_chain(storage: Storage) -> None:
    bins = []
    for bin_id in range(8, 13):
        bins.append(
            {
                "bin_id": bin_id,
                "price": str(Q64),
                "amount_x": "1000000",
                "amount_y": "1000000",
                "liquidity_supply": str(10_000_000 * Q64),
                "fee_amount_x_per_token_stored": "0",
                "fee_amount_y_per_token_stored": "0",
                "reward_per_token_stored_0": "0",
                "reward_per_token_stored_1": "0",
            }
        )
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": 10,
            "bin_step": 25,
            "token_x_mint": "x",
            "token_y_mint": "y",
            "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
            "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
            "base_fee_rate": "0",
            "variable_fee_rate": "0",
            "total_fee_rate": "0",
            "deposit_total_fee_rate": "0",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
            "supports_limit_order": False,
            "reward_mint_0": None,
            "reward_mint_1": None,
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": 8,
                    "upper_bin_id": 12,
                    "bins": bins,
                }
            ],
        },
        observed_at=DECISION,
    )


def _report(
    *,
    arm: PaperCandidateArm | None = PaperCandidateArm(
        strategy="SPOT",
        half_width=1,
        center_offset=1,
    ),
) -> EmpiricalPaperCandidateCycleReport:
    selection = EmpiricalPaperCandidateSelection(
        pool_address="pool",
        decision_observed_at=DECISION,
        context_key="FLAT|BALANCED|QUIET",
        status=("PAPER_EXPLORATION" if arm is not None else "INSUFFICIENT_CONTEXT_EVIDENCE"),
        selection_mode=("UNSEEN_ARM" if arm is not None else "NO_SELECTION"),
        selected_arm=arm,
        paper_only=True,
        live_authorized=False,
        evidence=(),
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
        selection=selection,
        paper_only=True,
        policy_actionable=False,
        live_authorized=False,
    )


def test_empirical_entry_opens_chain_bound_virtual_position(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_chain(storage)
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )

    result = open_empirical_paper_candidate(
        storage,
        account_id="paper",
        position_id="emp-1",
        event_key="emp-enter-1",
        report=_report(),
        amount_x=0,
        amount_y=100,
        capital_quote=100,
    )

    assert result.opened is True
    assert result.bound is True
    assert result.position is not None
    assert result.position.policy_source == "EMPIRICAL_PAPER"
    assert result.position.strategy == "SPOT"
    assert result.position.min_bin_id == 10
    assert result.position.max_bin_id == 12
    assert result.counterfactual is not None
    assert result.counterfactual.entry_observed_at == DECISION
    assert result.counterfactual.bins > 0
    assert result.account.cash_quote == 900

    with storage.connect() as conn:
        bound = conn.execute(
            "SELECT entry_observed_at FROM paper_counterfactual_positions "
            "WHERE position_id = ?",
            ("emp-1",),
        ).fetchone()
    assert bound is not None
    assert str(bound[0]) == DECISION


def test_empirical_entry_does_not_open_without_selection(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    result = open_empirical_paper_candidate(
        storage,
        account_id="paper",
        position_id="emp-none",
        event_key="emp-none",
        report=_report(arm=None),
        amount_x=0,
        amount_y=100,
        capital_quote=100,
    )
    assert result.opened is False
    assert result.bound is False
    assert result.position is None
    assert result.account.cash_quote == 1000


def test_empirical_entry_fails_closed_on_live_authorization(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    report = replace(_report(), live_authorized=True)

    with pytest.raises(ValueError, match="PAPER-only boundary"):
        open_empirical_paper_candidate(
            storage,
            account_id="paper",
            position_id="emp-live",
            event_key="emp-live",
            report=report,
            amount_x=0,
            amount_y=100,
            capital_quote=100,
        )
