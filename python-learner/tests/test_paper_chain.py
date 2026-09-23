from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import (
    create_paper_account,
    open_paper_position,
    paper_account_snapshot,
)
from meteora_learner.paper_chain import (
    apply_chain_paper_observation,
    bind_paper_position_to_chain,
    paper_chain_binding,
    value_paper_position_from_chain,
)
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


def save_snapshot(storage, minute, *, active_id=0, fee_checkpoint=0):
    bins = []
    for bin_id in range(-2, 3):
        bins.append(
            {
                "bin_id": bin_id,
                "price": str(Q64),
                "amount_x": "1000",
                "amount_y": "1000",
                "liquidity_supply": str(2000 * Q64),
                "fee_amount_x_per_token_stored": "0",
                "fee_amount_y_per_token_stored": str(fee_checkpoint),
            }
        )
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
            "bin_arrays": [
                {
                    "address": "array",
                    "index": 0,
                    "lower_bin_id": -2,
                    "upper_bin_id": 2,
                    "bins": bins,
                }
            ],
        },
        observed_at=f"2026-09-23T00:{minute:02d}:00+00:00",
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
        min_bin_id=-1,
        max_bin_id=1,
        capital_quote=100,
    )


def test_chain_binding_derives_mark_and_fee_delta(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_snapshot(storage, 0, fee_checkpoint=0)
    save_snapshot(storage, 5, fee_checkpoint=Q64)
    seed_position(storage)

    binding = bind_paper_position_to_chain(
        storage,
        position_id="pos",
        observed_at="2026-09-23T00:00:00+00:00",
        amount_x=0,
        amount_y=100,
        token_y_quote_per_atomic=1.0,
    )
    assert binding.amount_y == 100

    valuation = value_paper_position_from_chain(
        storage,
        position_id="pos",
        observed_at="2026-09-23T00:05:00+00:00",
        token_y_quote_per_atomic=1.0,
    )
    assert valuation.mark_quote > 0
    assert valuation.fee_delta_quote >= 0
    assert valuation.rewards_valued is True


def test_chain_observation_updates_paper_ledger_without_manual_mark(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_snapshot(storage, 0, fee_checkpoint=0)
    save_snapshot(storage, 5, fee_checkpoint=Q64)
    seed_position(storage)
    bind_paper_position_to_chain(
        storage,
        position_id="pos",
        observed_at="2026-09-23T00:00:00+00:00",
        amount_x=0,
        amount_y=100,
        token_y_quote_per_atomic=1.0,
    )

    result = apply_chain_paper_observation(
        storage,
        position_id="pos",
        observed_at="2026-09-23T00:05:00+00:00",
        token_y_quote_per_atomic=1.0,
        pool_safe=True,
        config=PositionManagementConfig(stop_loss_bps=5000),
    )
    assert result.executed_action == "HOLD"
    account = paper_account_snapshot(storage, account_id="paper")
    assert account.open_positions == 1
    assert paper_chain_binding(storage, position_id="pos").last_observed_at == (
        "2026-09-23T00:05:00+00:00"
    )


def test_chain_binding_rejects_wrong_atomic_notional(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_snapshot(storage, 0)
    seed_position(storage)

    import pytest

    with pytest.raises(ValueError, match="notional error"):
        bind_paper_position_to_chain(
            storage,
            position_id="pos",
            observed_at="2026-09-23T00:00:00+00:00",
            amount_x=0,
            amount_y=10,
            token_y_quote_per_atomic=1.0,
        )
