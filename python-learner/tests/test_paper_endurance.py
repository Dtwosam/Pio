from meteora_learner.paper_account import (
    create_paper_account,
    open_paper_position,
)
from meteora_learner.paper_endurance import (
    PaperEnduranceCriteria,
    build_paper_endurance_report,
)
from meteora_learner.storage import Storage


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


def insert_tick(
    storage,
    *,
    tick_id,
    started_at,
    finished_at,
    status,
):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_ticks(
                tick_id, account_id, started_at, finished_at, status
            ) VALUES (?, 'paper', ?, ?, ?)
            """,
            (tick_id, started_at, finished_at, status),
        )


def insert_valuation(storage, observed_at):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_chain_valuations(
                position_id, observed_at, event_key_prefix, status,
                active_bin_id, mark_quote, fee_delta_quote,
                reward_delta_quote, inventory_x_atomic,
                inventory_y_atomic, fee_x_atomic, fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                next_state_json, valuation_json
            ) VALUES (
                'pos', ?, ?, 'APPLIED',
                0, '100', '0', '0', '0', '100',
                '0', '0', '0', '0', '{}', '{}'
            )
            """,
            (observed_at, f"valuation:{observed_at}"),
        )


def test_endurance_report_passes_only_when_configured_evidence_exists(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_position(storage)

    insert_tick(
        storage,
        tick_id="t1",
        started_at="2026-09-23T00:00:00+00:00",
        finished_at="2026-09-23T00:01:00+00:00",
        status="COMPLETE",
    )
    insert_tick(
        storage,
        tick_id="t2",
        started_at="2026-09-23T01:00:00+00:00",
        finished_at="2026-09-23T01:01:00+00:00",
        status="COMPLETE",
    )
    insert_tick(
        storage,
        tick_id="t3",
        started_at="2026-09-23T02:00:00+00:00",
        finished_at="2026-09-23T02:01:00+00:00",
        status="WAITING_CHAIN",
    )
    insert_tick(
        storage,
        tick_id="t4",
        started_at="2026-09-23T03:00:00+00:00",
        finished_at="2026-09-23T03:01:00+00:00",
        status="COMPLETE",
    )
    insert_valuation(storage, "2026-09-23T01:00:00+00:00")
    insert_valuation(storage, "2026-09-23T02:00:00+00:00")

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_scheduler_events(
                account_id, event_time, event_type, tick_id,
                owner_id, status
            ) VALUES (
                'paper', '2026-09-23T01:00:00+00:00',
                'LEASE_RECOVERED', 't2', 'worker', 'RUNNING'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_scheduler_events(
                account_id, event_time, event_type, tick_id,
                owner_id, status
            ) VALUES (
                'paper', '2026-09-23T02:00:00+00:00',
                'LEASE_BUSY', 't3', 'worker', 'BUSY'
            )
            """
        )

    report = build_paper_endurance_report(
        storage,
        account_id="paper",
        criteria=PaperEnduranceCriteria(
            min_runtime_hours=3,
            min_terminal_ticks=4,
            min_success_rate_pct=100,
            max_dependency_blocked_pct=25,
            max_consecutive_failures=0,
            max_stale_running_ticks=0,
            stale_running_after_seconds=300,
            min_applied_chain_valuations=2,
            min_distinct_positions_valued=1,
        ),
        as_of="2026-09-23T04:00:00+00:00",
    )

    assert report.passing is True
    assert report.terminal_ticks == 4
    assert report.failure_ticks == 0
    assert report.dependency_blocked_ticks == 1
    assert report.success_rate_pct == 100
    assert report.dependency_blocked_pct == 25
    assert report.applied_chain_valuations == 2
    assert report.distinct_positions_valued == 1
    assert report.lease_recoveries == 1
    assert report.lease_busy_events == 1
    assert report.reasons == ()


def test_endurance_report_exposes_failure_and_stale_run_gaps(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_position(storage)

    insert_tick(
        storage,
        tick_id="f1",
        started_at="2026-09-23T00:00:00+00:00",
        finished_at="2026-09-23T00:01:00+00:00",
        status="FAILED",
    )
    insert_tick(
        storage,
        tick_id="f2",
        started_at="2026-09-23T00:10:00+00:00",
        finished_at="2026-09-23T00:11:00+00:00",
        status="MARKET_REFRESH_FAILED",
    )
    insert_tick(
        storage,
        tick_id="q1",
        started_at="2026-09-23T00:20:00+00:00",
        finished_at="2026-09-23T00:21:00+00:00",
        status="WAITING_QUOTES",
    )
    insert_tick(
        storage,
        tick_id="running",
        started_at="2026-09-23T00:30:00+00:00",
        finished_at=None,
        status="RUNNING",
    )

    report = build_paper_endurance_report(
        storage,
        account_id="paper",
        criteria=PaperEnduranceCriteria(
            min_runtime_hours=0,
            min_terminal_ticks=3,
            min_success_rate_pct=90,
            max_dependency_blocked_pct=0,
            max_consecutive_failures=1,
            max_stale_running_ticks=0,
            stale_running_after_seconds=300,
            min_applied_chain_valuations=1,
            min_distinct_positions_valued=1,
        ),
        as_of="2026-09-23T01:00:00+00:00",
    )

    assert report.passing is False
    assert report.failure_ticks == 2
    assert report.max_observed_consecutive_failures == 2
    assert report.stale_running_ticks == 1
    assert any("non-failure tick rate" in reason for reason in report.reasons)
    assert any("dependency-blocked" in reason for reason in report.reasons)
    assert any("consecutive failures" in reason for reason in report.reasons)
    assert any("stale RUNNING" in reason for reason in report.reasons)
    assert any("chain valuations" in reason for reason in report.reasons)
    assert any("distinct valued positions" in reason for reason in report.reasons)


def test_endurance_report_requires_known_account(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    try:
        build_paper_endurance_report(
            storage,
            account_id="missing",
        )
    except ValueError as exc:
        assert "unknown paper account" in str(exc)
    else:
        raise AssertionError("expected unknown account failure")
