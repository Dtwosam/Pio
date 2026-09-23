from types import SimpleNamespace

import pytest

from meteora_learner.baseline_policy import BaselineProposal
from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import create_paper_account
from meteora_learner.paper_entry import open_bound_deterministic_paper_plan
from meteora_learner.phase_promotion import (
    persist_phase2_promotion,
    persist_phase3_promotion,
)
from meteora_learner.storage import Storage


OBS = "2026-09-23T09:00:00+00:00"


def save_chain(storage):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": 0,
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
            "reward_mints": ["x", "y"],
            "reward_rates": ["0", "0"],
            "reward_duration_ends": [0, 0],
            "reward_last_update_times": [0, 0],
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": 0,
                    "upper_bin_id": 0,
                    "bins": [
                        {
                            "bin_id": 0,
                            "price": str(Q64),
                            "amount_x": "1000",
                            "amount_y": "1000",
                            "liquidity_supply": str(2000 * Q64),
                            "fee_amount_x_per_token_stored": "0",
                            "fee_amount_y_per_token_stored": "0",
                            "reward_per_token_stored": ["0", "0"],
                        }
                    ],
                }
            ],
        },
        observed_at=OBS,
    )


def promote_phase3(storage):
    ready = SimpleNamespace(
        promotion_ready=True,
        to_record=lambda: {"promotion_ready": True},
    )
    persist_phase2_promotion(storage, report=ready)
    persist_phase3_promotion(storage, report=ready)


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
        policy_authorized=True,
        amount_x=0,
        amount_y=100,
        decision_observed_at=OBS,
        max_share_bps=500,
        favor_x_in_active_bin=False,
        entry_gate=SimpleNamespace(
            proposal=proposal,
            sized_quote=100.0,
        ),
    )


def seed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_chain(storage)
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    promote_phase3(storage)
    return storage


def test_bound_entry_opens_and_initializes_chain_state_atomically(tmp_path):
    storage = seed(tmp_path)

    result = open_bound_deterministic_paper_plan(
        storage,
        account_id="paper",
        position_id="pos",
        event_key="enter-pos",
        plan=plan(),
    )

    assert result.opened is True
    assert result.bound is True
    assert result.counterfactual.entry_observed_at == OBS
    assert result.account.cash_quote == 900

    with storage.connect() as conn:
        opened_at = conn.execute(
            "SELECT opened_at FROM paper_positions WHERE position_id = 'pos'"
        ).fetchone()[0]
        bound = conn.execute(
            """
            SELECT COUNT(*)
            FROM paper_counterfactual_positions
            WHERE position_id = 'pos'
            """
        ).fetchone()[0]
        events = conn.execute(
            """
            SELECT COUNT(*)
            FROM paper_events
            WHERE event_key = 'enter-pos'
            """
        ).fetchone()[0]
    assert opened_at == OBS
    assert bound == 1
    assert events == 1


def test_bound_entry_preflight_failure_does_not_debit_cash(tmp_path):
    storage = seed(tmp_path)
    bad = plan()
    bad.entry_gate.proposal = BaselineProposal(
        strategy="SPOT",
        half_width=0,
        center_offset=1,
        decision_active_bin_id=0,
        min_bin_id=1,
        max_bin_id=1,
    )

    with pytest.raises(ValueError):
        open_bound_deterministic_paper_plan(
            storage,
            account_id="paper",
            position_id="pos",
            event_key="enter-pos",
            plan=bad,
        )

    with storage.connect() as conn:
        cash = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id = 'paper'"
        ).fetchone()[0]
        positions = conn.execute(
            "SELECT COUNT(*) FROM paper_positions"
        ).fetchone()[0]
    assert float(cash) == 1000.0
    assert positions == 0


def test_bound_entry_rolls_back_position_when_binding_insert_fails(tmp_path):
    storage = seed(tmp_path)
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_counterfactual_positions(
                position_id, pool_address, entry_observed_at,
                amount_x_atomic, amount_y_atomic, idle_x_atomic, idle_y_atomic,
                entry_price_q64, entry_value_y_atomic, capital_quote,
                max_share_bps, favor_x_active, token_x_mint, token_y_mint,
                reward_mint_0, reward_mint_1, initial_state_json
            ) VALUES (
                'pos', 'pool', ?, '0', '100', '0', '0',
                ?, '100', '100', 500, 0, 'x', 'y', 'x', 'y',
                '{"bins":[]}'
            )
            """,
            (OBS, str(Q64)),
        )

    with pytest.raises(ValueError, match="counterfactual state already exists"):
        open_bound_deterministic_paper_plan(
            storage,
            account_id="paper",
            position_id="pos",
            event_key="enter-pos",
            plan=plan(),
        )

    with storage.connect() as conn:
        cash = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id = 'paper'"
        ).fetchone()[0]
        positions = conn.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE position_id = 'pos'"
        ).fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM paper_events WHERE event_key = 'enter-pos'"
        ).fetchone()[0]
    assert float(cash) == 1000.0
    assert positions == 0
    assert events == 0
