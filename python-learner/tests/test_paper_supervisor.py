from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import create_paper_account, open_paper_position
from meteora_learner.paper_chain import bind_paper_position_to_chain
from meteora_learner.paper_supervisor import run_paper_supervisor
from meteora_learner.pool_safety import PoolSafetyConfig
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.quote_registry import save_token_quote
from meteora_learner.storage import Storage


ENTRY = "2026-09-23T09:00:00+00:00"
LATEST = "2026-09-23T09:05:00+00:00"


def save_pool_api(storage):
    storage.save_pool_snapshot(
        {
            "address": "pool",
            "name": "pool",
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
        observed_at=LATEST,
    )


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


def safety():
    return PoolSafetyConfig(
        min_tvl_usd=0,
        min_volume_24h_usd=0,
        min_pool_age_hours=0,
        min_chain_observations=2,
        max_dynamic_fee_pct=1.0,
    )


def seed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_pool_api(storage)
    save_chain(storage, ENTRY)
    save_chain(storage, LATEST, Q64)
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
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
        observed_at=ENTRY,
        amount_x=0,
        amount_y=100,
        token_y_quote_per_atomic=1.0,
    )
    return storage


def test_supervisor_runs_when_chain_and_quote_are_fresh(tmp_path):
    storage = seed(tmp_path)
    save_token_quote(
        storage,
        token_mint="y",
        quote_per_atomic=1.0,
        source="TEST",
        observed_at=LATEST,
    )

    report = run_paper_supervisor(
        storage,
        account_id="paper",
        cycle_id="supervisor-1",
        chain_max_age_seconds=300,
        quote_max_age_seconds=300,
        as_of="2026-09-23T09:06:00+00:00",
        safety_config=safety(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )

    assert report.status == "COMPLETE"
    assert report.chain_blocked_pools == 0
    assert report.quote_blocked_mints == 0
    assert report.portfolio.scheduled_positions == 1
    assert report.portfolio.cycle.applied == 1


def test_supervisor_waits_on_stale_chain_and_emits_collection_task(tmp_path):
    storage = seed(tmp_path)
    save_token_quote(
        storage,
        token_mint="y",
        quote_per_atomic=1.0,
        source="TEST",
        observed_at=LATEST,
    )

    report = run_paper_supervisor(
        storage,
        account_id="paper",
        cycle_id="supervisor-stale",
        chain_max_age_seconds=300,
        quote_max_age_seconds=5000,
        as_of="2026-09-23T09:20:00+00:00",
        safety_config=safety(),
    )

    assert report.status == "WAITING_CHAIN"
    assert report.chain_blocked_pools == 1
    assert report.portfolio.scheduled_positions == 0
    assert report.chain_queue.items[0].shell_command is not None


def test_supervisor_waits_on_missing_quote(tmp_path):
    storage = seed(tmp_path)

    report = run_paper_supervisor(
        storage,
        account_id="paper",
        cycle_id="supervisor-quotes",
        chain_max_age_seconds=300,
        quote_max_age_seconds=300,
        as_of="2026-09-23T09:06:00+00:00",
        safety_config=safety(),
    )

    assert report.status == "WAITING_QUOTES"
    assert report.quote_blocked_mints == 1
    assert report.portfolio.scheduled_positions == 0
    assert report.quote_statuses[0].available is False


def test_supervisor_waits_on_missing_external_reward_quote(tmp_path):
    storage = seed(tmp_path)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE paper_counterfactual_positions
            SET reward_mint_0 = 'reward'
            WHERE position_id = 'pos'
            """
        )
    save_token_quote(
        storage,
        token_mint="y",
        quote_per_atomic=1.0,
        source="TEST",
        observed_at=LATEST,
    )

    report = run_paper_supervisor(
        storage,
        account_id="paper",
        cycle_id="supervisor-reward-quote",
        chain_max_age_seconds=300,
        quote_max_age_seconds=300,
        as_of="2026-09-23T09:06:00+00:00",
        safety_config=safety(),
    )

    assert report.status == "WAITING_QUOTES"
    assert report.quote_blocked_mints == 1
    assert report.portfolio.scheduled_positions == 0
    reward_status = next(
        item for item in report.quote_statuses
        if item.token_mint == "reward"
    )
    assert reward_status.available is False
