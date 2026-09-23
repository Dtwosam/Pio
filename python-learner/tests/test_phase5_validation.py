from meteora_learner.paper_account import (
    close_paper_position,
    create_paper_account,
    open_paper_position,
)
from meteora_learner.phase5_validation import (
    Phase5PromotionCriteria,
    evaluate_phase5_promotion,
)
from meteora_learner.phase_promotion import (
    PHASE3,
    PHASE3_EVIDENCE_TYPE,
    PHASE5,
    persist_phase5_promotion,
    phase_promotion_state,
)
from meteora_learner.storage import Storage


def seed_ready(storage):
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
        max_bin_id=1,
        capital_quote=100,
        event_time="2026-09-23T09:00:00+00:00",
    )
    close_paper_position(
        storage,
        event_key="exit",
        position_id="pos",
        final_mark_quote=101,
        event_time="2026-09-23T09:10:00+00:00",
    )

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_ticks(
                tick_id, account_id, started_at, finished_at, status
            ) VALUES (
                'tick', 'paper',
                '2026-09-23T09:00:00+00:00',
                '2026-09-23T09:10:00+00:00',
                'COMPLETE'
            )
            """
        )
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
                'pos', '2026-09-23T09:05:00+00:00',
                'valuation', 'APPLIED',
                0, '100', '0', '0', '0', '100',
                '0', '0', '0', '0', '{}', '{}'
            )
            """
        )


def criteria():
    return Phase5PromotionCriteria(
        min_runtime_hours=0,
        min_terminal_ticks=1,
        min_success_rate_pct=100,
        max_dependency_blocked_pct=0,
        max_consecutive_failures=0,
        max_stale_running_ticks=0,
        stale_running_after_seconds=60,
        min_applied_chain_valuations=1,
        min_distinct_positions_valued=1,
        min_closed_positions=1,
        min_distinct_pools=1,
    )


def test_phase5_gate_requires_persisted_phase3(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    report = evaluate_phase5_promotion(
        storage,
        account_id="paper",
        criteria=criteria(),
        as_of="2026-09-23T09:15:00+00:00",
    )

    assert report.promotion_ready is False
    assert report.endurance.passing is True
    assert report.ledger_audit.passing is True
    assert any("Phase 3" in reason for reason in report.reasons)


def test_phase5_ready_report_can_be_persisted(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    storage.save_phase_promotion_evidence(
        phase_name=PHASE3,
        evidence_type=PHASE3_EVIDENCE_TYPE,
        qualified=True,
        evidence={"test": True},
    )

    report = evaluate_phase5_promotion(
        storage,
        account_id="paper",
        criteria=criteria(),
        as_of="2026-09-23T09:15:00+00:00",
    )

    assert report.promotion_ready is True
    state = persist_phase5_promotion(storage, report=report)
    assert state.phase_name == PHASE5
    assert state.promoted is True
    assert phase_promotion_state(
        storage,
        phase_name=PHASE5,
    ).promoted is True


def test_phase5_promotion_rejects_unready_report(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    report = evaluate_phase5_promotion(
        storage,
        account_id="paper",
        criteria=criteria(),
        as_of="2026-09-23T09:15:00+00:00",
    )

    try:
        persist_phase5_promotion(storage, report=report)
    except ValueError as exc:
        assert "Phase 3" in str(exc)
    else:
        raise AssertionError("expected Phase 5 promotion refusal")
