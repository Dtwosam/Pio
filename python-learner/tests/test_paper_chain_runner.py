from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import (
    create_paper_account,
    open_paper_position,
    paper_account_snapshot,
)
from meteora_learner.paper_chain import bind_paper_position_to_chain
from meteora_learner.paper_chain_runner import (
    PaperChainBatchItem,
    run_chain_paper_batch,
)
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


def save_snapshot(storage, minute, fee_checkpoint=0):
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
                            "fee_amount_y_per_token_stored": str(fee_checkpoint),
                        }
                    ],
                }
            ],
        },
        observed_at=f"2026-09-23T09:{minute:02d}:00+00:00",
    )


def seed(storage):
    save_snapshot(storage, 0)
    save_snapshot(storage, 5, Q64)
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
    bind_paper_position_to_chain(
        storage,
        position_id="pos",
        observed_at="2026-09-23T09:00:00+00:00",
        amount_x=0,
        amount_y=100,
        token_y_quote_per_atomic=1.0,
    )


def test_chain_batch_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    item = PaperChainBatchItem(
        position_id="pos",
        token_y_quote_per_atomic=1.0,
    )

    first = run_chain_paper_batch(
        storage,
        run_id="chain-1",
        observed_at="2026-09-23T09:05:00+00:00",
        items=(item,),
        config=PositionManagementConfig(stop_loss_bps=5000),
    )
    assert first.status == "COMPLETE"
    assert first.items_applied == 1
    account_first = paper_account_snapshot(storage, account_id="paper")

    second = run_chain_paper_batch(
        storage,
        run_id="chain-1",
        observed_at="2026-09-23T09:05:00+00:00",
        items=(item,),
        config=PositionManagementConfig(stop_loss_bps=5000),
    )
    account_second = paper_account_snapshot(storage, account_id="paper")

    assert second.reused_existing_run is True
    assert account_second == account_first


def test_chain_batch_rejects_changed_quote_for_same_run(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    run_chain_paper_batch(
        storage,
        run_id="chain-1",
        observed_at="2026-09-23T09:05:00+00:00",
        items=(
            PaperChainBatchItem(
                position_id="pos",
                token_y_quote_per_atomic=1.0,
            ),
        ),
    )

    import pytest

    with pytest.raises(ValueError, match="inputs do not match"):
        run_chain_paper_batch(
            storage,
            run_id="chain-1",
            observed_at="2026-09-23T09:05:00+00:00",
            items=(
                PaperChainBatchItem(
                    position_id="pos",
                    token_y_quote_per_atomic=2.0,
                ),
            ),
        )
