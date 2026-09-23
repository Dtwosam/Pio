from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.paper_account import create_paper_account, open_paper_position
from meteora_learner.paper_chain import bind_paper_position_to_chain
from meteora_learner.paper_health import build_paper_health
from meteora_learner.quote_registry import save_token_quote
from meteora_learner.storage import Storage


NOW = "2026-09-23T10:00:00+00:00"
ENTRY = "2026-09-23T09:55:00+00:00"


def chain_payload():
    return {
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
    }


def seed_open(storage):
    storage.save_chain_pool_snapshot(chain_payload(), observed_at=ENTRY)
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


def test_health_is_idle_without_open_positions(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    create_paper_account(storage, account_id="paper", starting_cash_quote=1000)

    report = build_paper_health(
        storage,
        account_id="paper",
        as_of=NOW,
    )

    assert report.status == "IDLE"
    assert report.open_positions == 0
    assert report.reasons == ()


def test_health_is_healthy_with_recent_tick_chain_and_quote(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_open(storage)
    storage.save_chain_pool_snapshot(
        chain_payload(),
        observed_at="2026-09-23T09:59:00+00:00",
    )
    save_token_quote(
        storage,
        token_mint="y",
        quote_per_atomic=1.0,
        source="TEST",
        observed_at="2026-09-23T09:59:00+00:00",
    )
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_ticks(
                tick_id, account_id, started_at, finished_at, status
            ) VALUES (
                'tick', 'paper', '2026-09-23T09:59:00+00:00',
                '2026-09-23T09:59:10+00:00', 'COMPLETE'
            )
            """
        )

    report = build_paper_health(
        storage,
        account_id="paper",
        max_tick_age_seconds=300,
        max_chain_age_seconds=300,
        max_quote_age_seconds=300,
        as_of=NOW,
    )

    assert report.status == "HEALTHY"
    assert report.reasons == ()


def test_health_degrades_on_missing_quote(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_open(storage)
    storage.save_chain_pool_snapshot(
        chain_payload(),
        observed_at="2026-09-23T09:59:00+00:00",
    )
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_ticks(
                tick_id, account_id, started_at, finished_at, status
            ) VALUES (
                'tick', 'paper', '2026-09-23T09:59:00+00:00',
                '2026-09-23T09:59:10+00:00', 'WAITING_QUOTES'
            )
            """
        )

    report = build_paper_health(
        storage,
        account_id="paper",
        as_of=NOW,
    )

    assert report.status == "DEGRADED"
    assert any("quote" in reason.lower() for reason in report.reasons)


def test_health_is_unhealthy_on_missed_ticks_and_failure_streak(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_open(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_ticks(
                tick_id, account_id, started_at, finished_at, status
            ) VALUES (
                'old', 'paper', '2026-09-23T09:30:00+00:00',
                '2026-09-23T09:30:10+00:00', 'FAILED'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_scheduler_state(
                account_id, owner_id, lease_until, heartbeat_at,
                last_tick_id, last_started_at, last_finished_at,
                last_status, consecutive_failures, total_ticks, updated_at
            ) VALUES (
                'paper', NULL, NULL, '2026-09-23T09:30:10+00:00',
                'old', '2026-09-23T09:30:00+00:00',
                '2026-09-23T09:30:10+00:00', 'FAILED', 3, 3,
                '2026-09-23T09:30:10+00:00'
            )
            """
        )

    report = build_paper_health(
        storage,
        account_id="paper",
        max_tick_age_seconds=600,
        max_consecutive_failures=2,
        as_of=NOW,
    )

    assert report.status == "UNHEALTHY"
    assert any("stale" in reason for reason in report.reasons)
    assert any("failure threshold" in reason for reason in report.reasons)
