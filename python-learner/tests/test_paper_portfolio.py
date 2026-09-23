from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import create_paper_account, open_paper_position
from meteora_learner.paper_chain import bind_paper_position_to_chain
from meteora_learner.paper_portfolio import run_portfolio_live_paper_cycle
from meteora_learner.pool_safety import PoolSafetyConfig
from meteora_learner.quote_registry import save_token_quote
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


def save_pool_api(storage, address, observed_at):
    storage.save_pool_snapshot(
        {
            "address": address,
            "name": address,
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
        observed_at=observed_at,
    )


def save_chain(storage, address, observed_at, fee_checkpoint=0):
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
            "supports_limit_order": False,
            "reward_mints": [f"{address}-x", f"{address}-y"],
            "reward_rates": ["0", "0"],
            "reward_duration_ends": [0, 0],
            "reward_last_update_times": [0, 0],
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
                            "reward_per_token_stored": ["0", "0"],
                        }
                    ],
                }
            ],
        },
        observed_at=observed_at,
    )


def safety_config():
    return PoolSafetyConfig(
        min_tvl_usd=0,
        min_volume_24h_usd=0,
        min_pool_age_hours=0,
        min_chain_observations=2,
        max_dynamic_fee_pct=1.0,
    )


def bind(storage, position, pool, entry_at):
    open_paper_position(
        storage,
        event_key=f"{position}-enter",
        account_id="paper",
        position_id=position,
        pool_address=pool,
        policy_source="DETERMINISTIC",
        strategy="SPOT",
        min_bin_id=0,
        max_bin_id=0,
        capital_quote=100,
    )
    bind_paper_position_to_chain(
        storage,
        position_id=position,
        observed_at=entry_at,
        amount_x=0,
        amount_y=100,
        token_y_quote_per_atomic=1.0,
    )


def test_portfolio_cycle_discovers_bound_positions_and_skips_missing_quote(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)

    entry = "2026-09-23T09:00:00+00:00"
    latest = "2026-09-23T09:05:00+00:00"
    for pool in ("a", "b"):
        save_pool_api(storage, pool, latest)
        save_chain(storage, pool, entry)
        save_chain(storage, pool, latest, Q64)
    bind(storage, "pos-a", "a", entry)
    bind(storage, "pos-b", "b", entry)

    report = run_portfolio_live_paper_cycle(
        storage,
        account_id="paper",
        cycle_id="portfolio-1",
        token_y_quotes={"a-y": 1.0},
        safety_config=safety_config(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )

    assert report.open_positions_seen == 2
    assert report.eligible_positions == 1
    assert report.scheduled_positions == 1
    assert report.cycle is not None
    assert report.cycle.applied == 1
    missing = next(item for item in report.schedule if item.position_id == "pos-b")
    assert "missing token-Y quote" in missing.reason


def test_portfolio_cycle_skips_positions_without_new_chain_state(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    entry = "2026-09-23T09:00:00+00:00"
    save_pool_api(storage, "a", entry)
    save_chain(storage, "a", entry)
    bind(storage, "pos-a", "a", entry)

    report = run_portfolio_live_paper_cycle(
        storage,
        account_id="paper",
        cycle_id="portfolio-1",
        token_y_quotes={"a-y": 1.0},
        safety_config=PoolSafetyConfig(
            min_tvl_usd=0,
            min_volume_24h_usd=0,
            min_pool_age_hours=0,
            min_chain_observations=1,
            max_dynamic_fee_pct=1.0,
        ),
    )

    assert report.scheduled_positions == 0
    assert report.cycle is None
    assert report.schedule[0].reason == "no new chain observation"


def test_portfolio_cycle_prioritizes_oldest_position_when_capped(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)
    entry = "2026-09-23T09:00:00+00:00"
    latest = "2026-09-23T09:05:00+00:00"
    for pool in ("a", "b"):
        save_pool_api(storage, pool, latest)
        save_chain(storage, pool, entry)
        save_chain(storage, pool, latest)
    bind(storage, "pos-b", "b", entry)
    bind(storage, "pos-a", "a", entry)

    report = run_portfolio_live_paper_cycle(
        storage,
        account_id="paper",
        cycle_id="portfolio-cap",
        token_y_quotes={"a-y": 1.0, "b-y": 1.0},
        max_positions=1,
        safety_config=safety_config(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )

    assert report.eligible_positions == 2
    assert report.scheduled_positions == 1
    assert report.cycle is not None
    assert report.cycle.positions_requested == 1
    selected = [
        item.position_id
        for item in report.schedule
        if item.eligible and item.reason is None
    ]
    assert selected == ["pos-a"]
    deferred = next(item for item in report.schedule if item.position_id == "pos-b")
    assert deferred.reason == "eligible but deferred by max_positions cap"



def test_portfolio_cycle_uses_only_fresh_persisted_quotes(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)

    entry = "2026-09-23T09:00:00+00:00"
    latest = "2026-09-23T09:05:00+00:00"
    save_pool_api(storage, "a", latest)
    save_chain(storage, "a", entry)
    save_chain(storage, "a", latest, Q64)
    bind(storage, "pos-a", "a", entry)

    save_token_quote(
        storage,
        token_mint="a-y",
        quote_per_atomic=1.0,
        source="TEST",
        observed_at="2026-09-23T09:04:00+00:00",
    )
    fresh = run_portfolio_live_paper_cycle(
        storage,
        account_id="paper",
        cycle_id="registry-fresh",
        token_y_quotes=None,
        quote_max_age_seconds=300,
        quote_as_of="2026-09-23T09:05:00+00:00",
        safety_config=safety_config(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )
    assert fresh.scheduled_positions == 1
    assert fresh.cycle is not None

    # New position on the same pool, but evaluate the stored quote as stale.
    bind(storage, "pos-b", "a", entry)
    stale = run_portfolio_live_paper_cycle(
        storage,
        account_id="paper",
        cycle_id="registry-stale",
        token_y_quotes=None,
        quote_max_age_seconds=30,
        quote_as_of="2026-09-23T09:05:00+00:00",
        safety_config=safety_config(),
        management_config=PositionManagementConfig(stop_loss_bps=5000),
    )
    item = next(value for value in stale.schedule if value.position_id == "pos-b")
    assert item.eligible is False
    assert "quote is stale" in item.reason
