from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import (
    create_paper_account,
    open_paper_position,
)
from meteora_learner.paper_chain import bind_paper_position_to_chain
from meteora_learner.paper_latest import (
    LatestPaperCycleItem,
    run_latest_live_paper_cycle,
)
from meteora_learner.pool_safety import PoolSafetyConfig
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


def save_pool_api(storage, address):
    storage.save_pool_snapshot(
        {
            "address": address,
            "name": f"{address}-pool",
            "tvl": 100000,
            "volume_24h": 20000,
            "fees_24h": 100,
            "current_price": 1.0,
            "bin_step": 25,
            "active_bin_id": 0,
            "token_x": {"symbol": "X", "decimals": 6},
            "token_y": {"symbol": "Y", "decimals": 6},
            "dynamic_fee_pct": 0.5,
            "is_blacklisted": False,
            "created_at": 1790035200,
        },
        observed_at="2026-09-23T09:10:00+00:00",
    )


def save_chain(storage, address, minute, fee_checkpoint=0):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": address,
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": f"{address}-x",
            "token_y_mint": f"{address}-y",
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
                    "address": f"{address}-array",
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


def safety_config():
    return PoolSafetyConfig(
        min_tvl_usd=0,
        min_volume_24h_usd=0,
        min_pool_age_hours=0,
        min_chain_observations=2,
        max_dynamic_fee_pct=1.0,
    )


def seed_position(storage, position_id, pool, latest_minute):
    save_pool_api(storage, pool)
    save_chain(storage, pool, 0)
    save_chain(storage, pool, latest_minute, Q64)
    open_paper_position(
        storage,
        event_key=f"{position_id}-enter",
        account_id="paper",
        position_id=position_id,
        pool_address=pool,
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=0,
        max_bin_id=0,
        capital_quote=100,
    )
    bind_paper_position_to_chain(
        storage,
        position_id=position_id,
        observed_at="2026-09-23T09:00:00+00:00",
        amount_x=0,
        amount_y=100,
        token_y_quote_per_atomic=1.0,
    )


def test_latest_cycle_discovers_and_groups_latest_observations(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    seed_position(storage, "a", "pool-a", 5)
    seed_position(storage, "b", "pool-b", 10)

    report = run_latest_live_paper_cycle(
        storage,
        cycle_id="cycle-1",
        items=(
            LatestPaperCycleItem("a", 1.0),
            LatestPaperCycleItem("b", 1.0),
        ),
        safety_config=safety_config(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )

    assert report.positions_requested == 2
    assert report.groups == 2
    assert report.applied == 2
    assert report.failed == 0
    assert {item.observed_at for item in report.details} == {
        "2026-09-23T09:05:00+00:00",
        "2026-09-23T09:10:00+00:00",
    }


def test_latest_cycle_is_idempotent_for_same_cycle_and_chain_state(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    seed_position(storage, "a", "pool-a", 5)
    item = LatestPaperCycleItem("a", 1.0)

    first = run_latest_live_paper_cycle(
        storage,
        cycle_id="cycle-1",
        items=(item,),
        safety_config=safety_config(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )
    second = run_latest_live_paper_cycle(
        storage,
        cycle_id="cycle-1",
        items=(item,),
        safety_config=safety_config(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )

    assert first.applied == 1
    assert second.applied == 1
    assert second.details[0].report.reused_existing_run is True
