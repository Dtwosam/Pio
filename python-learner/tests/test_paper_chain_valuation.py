from types import SimpleNamespace

import pytest

from meteora_learner.baseline_policy import BaselineProposal
from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import (
    create_paper_account,
    open_paper_position,
    paper_account_snapshot,
)
from meteora_learner.paper_chain_valuation import (
    apply_paper_chain_valuation,
    initialize_paper_counterfactual,
    prepare_paper_chain_valuation,
)
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


def save_chain(storage, observed_at, *, active_id=0, fee_checkpoint=0, reward_checkpoint=0, reward_mint="x"):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": active_id,
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
            "reward_mint_0": reward_mint,
            "reward_mint_1": "y",
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": 0,
                    "upper_bin_id": 1,
                    "bins": [
                        {
                            "bin_id": 0,
                            "price": str(Q64),
                            "amount_x": "1000",
                            "amount_y": "1000",
                            "liquidity_supply": str(2000 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": str(fee_checkpoint),
                            "reward_per_token_stored_0": str(reward_checkpoint),
                            "reward_per_token_stored_1": "0",
                        },
                        {
                            "bin_id": 1,
                            "price": str(Q64),
                            "amount_x": "1000",
                            "amount_y": "1000",
                            "liquidity_supply": str(2000 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": str(fee_checkpoint),
                            "reward_per_token_stored_0": str(reward_checkpoint),
                            "reward_per_token_stored_1": "0",
                        },
                    ],
                }
            ],
        },
        observed_at=observed_at,
    )


def plan():
    proposal = BaselineProposal(
        strategy="SPOT",
        half_width=0,
        center_offset=0,
        decision_active_bin_id=0,
        min_bin_id=0,
        max_bin_id=0,
    )
    return SimpleNamespace(
        pool_address="pool",
        amount_x=0,
        amount_y=10,
        decision_observed_at="2026-09-23T09:00:00+00:00",
        max_share_bps=500,
        favor_x_in_active_bin=False,
        entry_gate=SimpleNamespace(proposal=proposal),
    )


def seed_position(storage):
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    open_paper_position(
        storage,
        event_key="enter",
        account_id="paper",
        position_id="pos",
        pool_address="pool",
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=0,
        max_bin_id=0,
        capital_quote=100,
    )


def test_chain_valuation_marks_inventory_and_fee_income_idempotently(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_chain(storage, "2026-09-23T09:00:00+00:00")
    save_chain(
        storage,
        "2026-09-23T09:05:00+00:00",
        fee_checkpoint=Q64,
    )
    seed_position(storage)

    state = initialize_paper_counterfactual(
        storage,
        position_id="pos",
        plan=plan(),
    )
    assert state.entry_value_y_atomic == 10
    assert state.bins == 1

    valuation = prepare_paper_chain_valuation(
        storage,
        position_id="pos",
        observed_at="2026-09-23T09:05:00+00:00",
    )
    assert valuation.mark_quote == 100.0
    assert valuation.fee_delta_quote > 0

    applied = apply_paper_chain_valuation(
        storage,
        position_id="pos",
        observed_at="2026-09-23T09:05:00+00:00",
        holding_observations=1,
        config=PositionManagementConfig(stop_loss_bps=5000),
    )
    assert applied.executed_action == "HOLD"
    first = paper_account_snapshot(storage, account_id="paper")

    again = apply_paper_chain_valuation(
        storage,
        position_id="pos",
        observed_at="2026-09-23T09:05:00+00:00",
        holding_observations=1,
        config=PositionManagementConfig(stop_loss_bps=5000),
    )
    second = paper_account_snapshot(storage, account_id="paper")
    assert again.executed_action == "ALREADY_APPLIED"
    assert second == first


def test_chain_valuation_leaves_out_of_range_rebalance_pending(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_chain(storage, "2026-09-23T09:00:00+00:00")
    save_chain(
        storage,
        "2026-09-23T09:05:00+00:00",
        active_id=1,
    )
    seed_position(storage)
    initialize_paper_counterfactual(
        storage,
        position_id="pos",
        plan=plan(),
    )

    applied = apply_paper_chain_valuation(
        storage,
        position_id="pos",
        observed_at="2026-09-23T09:05:00+00:00",
        holding_observations=1,
        config=PositionManagementConfig(stop_loss_bps=5000),
    )
    assert applied.executed_action == "REBALANCE_PENDING_COST"


def test_chain_valuation_fails_closed_on_unvalued_reward(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_chain(
        storage,
        "2026-09-23T09:00:00+00:00",
        reward_mint="other",
    )
    save_chain(
        storage,
        "2026-09-23T09:05:00+00:00",
        reward_checkpoint=Q64,
        reward_mint="other",
    )
    seed_position(storage)
    initialize_paper_counterfactual(
        storage,
        position_id="pos",
        plan=plan(),
    )

    with pytest.raises(ValueError, match="no token-Y valuation"):
        prepare_paper_chain_valuation(
            storage,
            position_id="pos",
            observed_at="2026-09-23T09:05:00+00:00",
        )



def test_chain_valuation_continues_after_rebalance_reset(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_chain(storage, "2026-09-23T09:00:00+00:00")
    save_chain(
        storage,
        "2026-09-23T09:05:00+00:00",
        active_id=1,
    )
    save_chain(
        storage,
        "2026-09-23T09:10:00+00:00",
        active_id=1,
        fee_checkpoint=Q64,
    )
    seed_position(storage)
    initialize_paper_counterfactual(
        storage,
        position_id="pos",
        plan=plan(),
    )

    first = apply_paper_chain_valuation(
        storage,
        position_id="pos",
        observed_at="2026-09-23T09:05:00+00:00",
        holding_observations=1,
        rebalance_cost_quote=1.0,
        config=PositionManagementConfig(
            stop_loss_bps=5000,
            max_rebalances=3,
        ),
    )
    assert first.executed_action == "REBALANCE"

    second = apply_paper_chain_valuation(
        storage,
        position_id="pos",
        observed_at="2026-09-23T09:10:00+00:00",
        holding_observations=2,
        config=PositionManagementConfig(
            stop_loss_bps=5000,
            max_rebalances=3,
        ),
    )
    assert second.executed_action == "HOLD"
    assert second.valuation.fee_delta_quote >= 0

    with storage.connect() as conn:
        reset = conn.execute(
            """
            SELECT next_state_json
            FROM paper_chain_valuations
            WHERE position_id = 'pos'
              AND observed_at = '2026-09-23T09:05:00+00:00'
            """
        ).fetchone()[0]
    assert '"rebalance_reset":true' in reset
