from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import (
    create_paper_account,
    open_paper_position,
)
from meteora_learner.paper_chain import bind_paper_position_to_chain
from meteora_learner.paper_live import (
    LivePaperChainBatchItem,
    apply_live_chain_paper_observation,
    assess_live_pool_safety,
    run_live_chain_paper_batch,
)
from meteora_learner.pool_safety import PoolSafetyConfig
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


def save_pool_api(storage, *, blacklisted=False):
    storage.save_pool_snapshot(
        {
            "address": "pool",
            "name": "X-Y",
            "tvl": 100000,
            "volume_24h": 20000,
            "fees_24h": 100,
            "current_price": 1.0,
            "bin_step": 25,
            "active_bin_id": 0,
            "token_x": {"symbol": "X", "decimals": 6},
            "token_y": {"symbol": "Y", "decimals": 6},
            "dynamic_fee_pct": 0.5,
            "is_blacklisted": blacklisted,
            "created_at": 1790035200,
        },
        observed_at="2026-09-23T09:05:00+00:00",
    )


def save_chain(storage, minute, fee_checkpoint=0):
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


def seed(storage, *, blacklisted=False):
    save_pool_api(storage, blacklisted=blacklisted)
    save_chain(storage, 0)
    save_chain(storage, 5, Q64)
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


def safety_config():
    return PoolSafetyConfig(
        min_tvl_usd=0,
        min_volume_24h_usd=0,
        min_pool_age_hours=0,
        min_chain_observations=2,
        max_dynamic_fee_pct=1.0,
    )


def test_live_pool_safety_requires_latest_observation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)

    stale = assess_live_pool_safety(
        storage,
        pool_address="pool",
        observed_at="2026-09-23T09:00:00+00:00",
        config=safety_config(),
    )
    assert stale.safe is False
    assert "not latest" in stale.reason

    latest = assess_live_pool_safety(
        storage,
        pool_address="pool",
        observed_at="2026-09-23T09:05:00+00:00",
        config=safety_config(),
    )
    assert latest.safe is True


def test_live_chain_observation_exits_blacklisted_pool(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage, blacklisted=True)

    result = apply_live_chain_paper_observation(
        storage,
        position_id="pos",
        observed_at="2026-09-23T09:05:00+00:00",
        token_y_quote_per_atomic=1.0,
        safety_config=safety_config(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )

    assert result.safety.safe is False
    assert "blacklisted" in result.safety.reason
    assert result.executed_action == "EXIT"


def test_live_chain_batch_derives_safety_for_each_item(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)

    report = run_live_chain_paper_batch(
        storage,
        run_id="live-1",
        observed_at="2026-09-23T09:05:00+00:00",
        items=(
            LivePaperChainBatchItem(
                position_id="pos",
                token_y_quote_per_atomic=1.0,
            ),
        ),
        safety_config=safety_config(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )

    assert report.status == "COMPLETE"
    assert report.items_applied == 1
    assert report.items[0].executed_action == "HOLD"
