from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import (
    create_paper_account,
    mark_paper_position,
    open_paper_position,
    paper_account_snapshot,
)
from meteora_learner.paper_chain import (
    apply_prepared_chain_valuation,
    bind_paper_position_to_chain,
    prepare_chain_valuation,
)
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


OBS0 = "2026-09-23T09:00:00+00:00"
OBS1 = "2026-09-23T09:05:00+00:00"


def save_chain(storage, observed_at, fee_checkpoint=0):
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
                            "fee_amount_y_per_token_stored": str(fee_checkpoint),
                            "reward_per_token_stored": ["0", "0"],
                        }
                    ],
                }
            ],
        },
        observed_at=observed_at,
    )


def seed(path):
    storage = Storage(path)
    save_chain(storage, OBS0)
    save_chain(storage, OBS1, Q64)
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
        observed_at=OBS0,
        amount_x=0,
        amount_y=100,
        token_y_quote_per_atomic=1.0,
    )
    return storage


def test_prepared_chain_valuation_survives_restart(tmp_path):
    path = tmp_path / "pio.db"
    storage = seed(path)
    prepared = prepare_chain_valuation(
        storage,
        position_id="pos",
        observed_at=OBS1,
        token_y_quote_per_atomic=1.0,
    )
    assert prepared.fee_delta_quote > 0

    with storage.connect() as conn:
        status = conn.execute(
            """
            SELECT status
            FROM paper_chain_valuations
            WHERE position_id = 'pos' AND observed_at = ?
            """,
            (OBS1,),
        ).fetchone()[0]
    assert status == "PREPARED"

    restarted = Storage(path)
    applied = apply_prepared_chain_valuation(
        restarted,
        position_id="pos",
        observed_at=OBS1,
        token_y_quote_per_atomic=1.0,
        pool_safe=True,
        config=PositionManagementConfig(stop_loss_bps=5000),
    )
    assert applied.executed_action == "HOLD"
    assert applied.detail["recovered"] is False

    first = paper_account_snapshot(restarted, account_id="paper")
    again = apply_prepared_chain_valuation(
        Storage(path),
        position_id="pos",
        observed_at=OBS1,
        token_y_quote_per_atomic=1.0,
        pool_safe=True,
        config=PositionManagementConfig(stop_loss_bps=5000),
    )
    second = paper_account_snapshot(Storage(path), account_id="paper")

    assert again.executed_action == "ALREADY_APPLIED"
    assert second == first


def test_mark_written_before_checkpoint_is_recovered_without_double_fee(tmp_path):
    path = tmp_path / "pio.db"
    storage = seed(path)
    prepared = prepare_chain_valuation(
        storage,
        position_id="pos",
        observed_at=OBS1,
        token_y_quote_per_atomic=1.0,
    )

    mark_paper_position(
        storage,
        event_key=f"paper-chain:pos:{OBS1}:mark",
        position_id="pos",
        mark_quote=prepared.mark_quote,
        fee_delta_quote=prepared.fee_delta_quote,
        reward_delta_quote=prepared.reward_delta_quote,
        event_time=OBS1,
    )
    after_crash = paper_account_snapshot(storage, account_id="paper")

    with storage.connect() as conn:
        status = conn.execute(
            """
            SELECT status
            FROM paper_chain_valuations
            WHERE position_id = 'pos' AND observed_at = ?
            """,
            (OBS1,),
        ).fetchone()[0]
    assert status == "PREPARED"

    restarted = Storage(path)
    recovered = apply_prepared_chain_valuation(
        restarted,
        position_id="pos",
        observed_at=OBS1,
        token_y_quote_per_atomic=1.0,
        pool_safe=True,
        config=PositionManagementConfig(stop_loss_bps=5000),
    )
    after_recovery = paper_account_snapshot(restarted, account_id="paper")

    assert recovered.executed_action == "HOLD_RECOVERED"
    assert recovered.detail["recovered"] is True
    assert after_recovery == after_crash

    with restarted.connect() as conn:
        status = conn.execute(
            """
            SELECT status
            FROM paper_chain_valuations
            WHERE position_id = 'pos' AND observed_at = ?
            """,
            (OBS1,),
        ).fetchone()[0]
        mark_events = conn.execute(
            """
            SELECT COUNT(*)
            FROM paper_events
            WHERE event_key = ?
            """,
            (f"paper-chain:pos:{OBS1}:mark",),
        ).fetchone()[0]
    assert status == "APPLIED"
    assert mark_events == 1
