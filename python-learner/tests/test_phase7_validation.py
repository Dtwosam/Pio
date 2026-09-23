from meteora_learner.phase7_validation import (
    Phase7PromotionCriteria,
    evaluate_phase7_promotion,
)
from meteora_learner.phase_promotion import (
    PHASE6,
    PHASE6_EVIDENCE_TYPE,
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    persist_phase7_promotion,
    phase_promotion_state,
)
from meteora_learner.storage import Storage


def seed_phase6(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE6,
        evidence_type=PHASE6_EVIDENCE_TYPE,
        qualified=True,
        evidence={
            "promotion_ready": True,
            "phase5_promoted": True,
            "passed_enter_intents": 10,
            "distinct_pools": 2,
            "blocked_intents": 2,
            "postsimulation_intents": 0,
            "invalid_passed_intents": 0,
            "distinct_authorized_wallets": ["wallet"],
            "criteria": {
                "min_passed_enter_intents": 10,
                "min_distinct_pools": 2,
                "min_blocked_intents": 2,
                "max_postsimulation_intents": 0,
            },
        },
    )


def seed_closed_position(storage, *, suffix, pool, pnl="1"):
    position = f"position-{suffix}"
    enter = f"enter-{suffix}"
    close = f"close-{suffix}"
    enter_sig = f"enter-signature-{suffix}"
    close_sig = f"close-signature-{suffix}"
    now = f"2026-09-23T1{suffix}:00:00+00:00"

    with storage.connect() as conn:
        for decision, signature, action, slot in (
            (enter, enter_sig, "ENTER", 100 + int(suffix)),
            (close, close_sig, "EXIT", 200 + int(suffix)),
        ):
            conn.execute(
                """
                INSERT INTO live_execution_receipts(
                    decision_id, signature, observed_at, mode, action,
                    pool_address, intent_status, slot, block_time,
                    network_fee_lamports, compute_units_consumed,
                    succeeded, event_count, add_request_count,
                    rebalance_request_count, raw_json
                ) VALUES (
                    ?, ?, ?, 'LIVE', ?, ?, 'CONFIRMED', ?, NULL,
                    5000, 1000, 1, 1, 0, 0, '{}'
                )
                """,
                (decision, signature, now, action, pool, slot),
            )

        conn.execute(
            """
            INSERT INTO live_execution_effects(
                decision_id, signature, observed_at, action,
                pool_address, position_address,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic, composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, chain_event_count, raw_json
            ) VALUES (
                ?, ?, ?, 'ENTER', ?, ?,
                '-10', '-10', '0', '0', '0', '0', '0', '0',
                5000, 1, '{}'
            )
            """,
            (enter, enter_sig, now, pool, position),
        )
        conn.execute(
            """
            INSERT INTO live_position_closure_proofs(
                decision_id, signature, position_address,
                receipt_slot, proof_slot, observed_at, closed, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, 1, '{}')
            """,
            (
                close,
                close_sig,
                position,
                200 + int(suffix),
                201 + int(suffix),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address,
                event_time, action, prior_status, next_status,
                min_bin_id, max_bin_id, raw_json
            ) VALUES (?, ?, ?, ?, 'ENTER', NULL, 'OPEN', -1, 1, '{}')
            """,
            (enter, enter_sig, position, now),
        )
        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address,
                event_time, action, prior_status, next_status,
                min_bin_id, max_bin_id, raw_json
            ) VALUES (?, ?, ?, ?, 'CLOSE', 'LIQUIDITY_REMOVED',
                      'CLOSED', NULL, NULL, '{}')
            """,
            (close, close_sig, position, now),
        )
        conn.execute(
            """
            INSERT INTO live_positions(
                position_address, pool_address, status,
                opened_decision_id, opened_signature, opened_at,
                min_bin_id, max_bin_id, last_decision_id,
                last_signature, last_observed_at, rebalances,
                closed_decision_id, closed_signature, raw_json
            ) VALUES (
                ?, ?, 'CLOSED', ?, ?, ?, -1, 1, ?, ?, ?, 0, ?, ?, '{}'
            )
            """,
            (
                position,
                pool,
                enter,
                enter_sig,
                now,
                close,
                close_sig,
                now,
                close,
                close_sig,
            ),
        )
        conn.execute(
            """
            INSERT INTO live_position_outcomes(
                position_address, pool_address, opened_decision_id,
                closed_decision_id, execution_count,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic, composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, label_status, created_at, raw_json
            ) VALUES (
                ?, ?, ?, ?, 2, '0', '0', '0', '0', '0', '0',
                '0', '0', 10000, 'VALUED', ?, '{}'
            )
            """,
            (position, pool, enter, close, now),
        )
        conn.execute(
            """
            INSERT INTO live_position_valuations(
                position_address, pool_address, opened_decision_id,
                closed_decision_id, quote_unit, valued_execution_count,
                principal_cashflow_quote, composition_cost_quote,
                fee_income_quote, reward_income_quote,
                network_cost_quote, realized_pnl_quote,
                entry_outflow_quote, realized_return_bps,
                max_age_seconds, created_at,
                quote_evidence_json, raw_json
            ) VALUES (
                ?, ?, ?, ?, 'USD', 2, '0', '0', '0', '0',
                '0', ?, '10', 100, 300, ?, '{}', '{}'
            )
            """,
            (position, pool, enter, close, pnl, now),
        )
        conn.execute(
            """
            INSERT INTO live_learning_labels(
                position_address, decision_id, pool_address,
                model_version, strategy, min_bin_id, max_bin_id,
                range_width_bins, proposed_capital_quote,
                expected_net_return_pct, expected_downside_pct,
                realized_pnl_quote, realized_return_bps,
                prediction_error_bps, target_positive_return,
                quote_unit, opened_signature, closed_decision_id,
                created_at, raw_json
            ) VALUES (
                ?, ?, ?, 'baseline', 'SPOT', -1, 1, 3,
                '10', '1', '1', ?, 100, 0, 1, 'USD', ?, ?, ?, '{}'
            )
            """,
            (position, enter, pool, pnl, enter_sig, close, now),
        )


def criteria():
    return Phase7PromotionCriteria(
        min_closed_positions=2,
        min_distinct_pools=2,
        min_confirmed_receipts=4,
        max_failed_receipts=0,
        max_open_positions_at_validation=0,
    )


def test_phase7_ready_live_corpus_can_be_persisted(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase6(storage)
    seed_closed_position(storage, suffix="1", pool="pool-a", pnl="-1")
    seed_closed_position(storage, suffix="2", pool="pool-b", pnl="2")

    report = evaluate_phase7_promotion(
        storage,
        criteria=criteria(),
    )

    assert report.promotion_ready is True
    assert report.ledger_audit.clean is True
    assert report.closed_positions == 2
    assert report.valued_closed_positions == 2
    assert report.labeled_closed_positions == 2
    assert report.distinct_closed_pools == 2
    assert report.failed_receipts == 0

    state = persist_phase7_promotion(storage, report=report)
    assert state.phase_name == PHASE7
    assert state.promoted is True
    assert state.evidence_type == PHASE7_EVIDENCE_TYPE
    assert phase_promotion_state(
        storage,
        phase_name=PHASE7,
    ).promoted is True


def test_phase7_does_not_require_profitable_outcomes(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase6(storage)
    seed_closed_position(storage, suffix="1", pool="pool-a", pnl="-5")
    seed_closed_position(storage, suffix="2", pool="pool-b", pnl="-2")

    report = evaluate_phase7_promotion(
        storage,
        criteria=criteria(),
    )

    assert report.promotion_ready is True


def test_phase7_requires_phase6_and_complete_valuation_labeling(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_closed_position(storage, suffix="1", pool="pool-a")
    seed_closed_position(storage, suffix="2", pool="pool-b")
    with storage.connect() as conn:
        conn.execute(
            """
            DELETE FROM live_learning_labels
            WHERE position_address = 'position-2'
            """
        )

    report = evaluate_phase7_promotion(
        storage,
        criteria=criteria(),
    )

    assert report.promotion_ready is False
    assert report.ledger_audit.clean is False
    assert any("Phase 6" in reason for reason in report.reasons)
    assert any("ledger audit" in reason for reason in report.reasons)
    assert any(
        "learning-labeled closed positions" in reason
        for reason in report.reasons
    )


def test_phase7_rejects_failed_receipt_and_open_position(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase6(storage)
    seed_closed_position(storage, suffix="1", pool="pool-a")
    seed_closed_position(storage, suffix="2", pool="pool-b")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_execution_receipts(
                decision_id, signature, observed_at, mode, action,
                pool_address, intent_status, slot, block_time,
                network_fee_lamports, compute_units_consumed,
                succeeded, event_count, add_request_count,
                rebalance_request_count, raw_json
            ) VALUES (
                'failed', 'failed-signature',
                '2026-09-23T13:00:00+00:00',
                'LIVE', 'ENTER', 'pool-a', 'FAILED', 999, NULL,
                5000, 1000, 0, 0, 0, 0, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_execution_effects(
                decision_id, signature, observed_at, action,
                pool_address, position_address,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic, composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, chain_event_count, raw_json
            ) VALUES (
                'failed', 'failed-signature',
                '2026-09-23T13:00:00+00:00',
                'ENTER', 'pool-a', NULL,
                '0', '0', '0', '0', '0', '0', '0', '0',
                5000, 0, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_positions(
                position_address, pool_address, status,
                opened_decision_id, opened_signature, opened_at,
                min_bin_id, max_bin_id, last_decision_id,
                last_signature, last_observed_at, rebalances, raw_json
            ) VALUES (
                'still-open', 'pool-c', 'OPEN',
                'open-decision', 'open-signature',
                '2026-09-23T13:00:00+00:00',
                -1, 1, 'open-decision', 'open-signature',
                '2026-09-23T13:00:00+00:00', 0, '{}'
            )
            """
        )

    report = evaluate_phase7_promotion(
        storage,
        criteria=criteria(),
    )

    assert report.promotion_ready is False
    assert report.failed_receipts == 1
    assert report.open_positions == 1
    assert any("failed live receipts" in reason for reason in report.reasons)
    assert any(
        "open/unsettled live positions" in reason
        for reason in report.reasons
    )
