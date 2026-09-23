from meteora_learner.live_execution_audit import audit_live_execution_ledger
from meteora_learner.storage import Storage


def insert_receipt(conn, decision, signature):
    conn.execute(
        """
        INSERT INTO live_execution_receipts(
            decision_id, signature, observed_at, mode, action,
            pool_address, intent_status, slot, block_time,
            network_fee_lamports, compute_units_consumed,
            succeeded, event_count, add_request_count,
            rebalance_request_count, raw_json
        ) VALUES (?, ?, '2026-09-23T10:00:00+00:00',
                  'LIVE', 'ENTER', 'pool', 'CONFIRMED',
                  1, 2, 5000, 1000, 1, 1, 0, 0, '{}')
        """,
        (decision, signature),
    )


def test_live_ledger_audit_flags_unprocessed_receipt(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        insert_receipt(conn, "decision", "signature")

    report = audit_live_execution_ledger(storage)

    assert report.clean is False
    assert report.unprocessed_receipt_decisions == ("decision",)


def test_live_ledger_audit_flags_unapplied_position_effect(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        insert_receipt(conn, "decision", "signature")
        conn.execute(
            """
            INSERT INTO live_execution_effects(
                decision_id, signature, observed_at, action,
                pool_address, position_address,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic,
                composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, chain_event_count, raw_json
            ) VALUES (
                'decision', 'signature',
                '2026-09-23T10:00:00+00:00',
                'ENTER', 'pool', 'position',
                '-1', '-2', '0', '0', '0', '0', '0', '0',
                5000, 1, '{}'
            )
            """
        )

    report = audit_live_execution_ledger(storage)

    assert report.clean is False
    assert report.unapplied_position_effect_decisions == ("decision",)


def test_live_ledger_audit_flags_closed_position_missing_outcome(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_positions(
                position_address, pool_address, status,
                opened_decision_id, opened_signature, opened_at,
                last_decision_id, last_signature, last_observed_at,
                rebalances, closed_decision_id, closed_signature, raw_json
            ) VALUES (
                'position', 'pool', 'CLOSED',
                'enter', 'sig-enter', 't',
                'settle', 'sig-settle', 't',
                0, 'settle', 'sig-settle', '{}'
            )
            """
        )

    report = audit_live_execution_ledger(storage)

    assert report.clean is False
    assert report.closed_positions_missing_outcome == ("position",)


def test_live_ledger_audit_clean_for_fully_processed_closed_position(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        insert_receipt(conn, "enter", "sig-enter")
        conn.execute(
            """
            INSERT INTO live_execution_effects(
                decision_id, signature, observed_at, action,
                pool_address, position_address,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic,
                composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, chain_event_count, raw_json
            ) VALUES (
                'enter', 'sig-enter',
                '2026-09-23T10:00:00+00:00',
                'ENTER', 'pool', 'position',
                '-1', '-2', '0', '0', '0', '0', '0', '0',
                5000, 1, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_positions(
                position_address, pool_address, status,
                opened_decision_id, opened_signature, opened_at,
                last_decision_id, last_signature, last_observed_at,
                rebalances, closed_decision_id, closed_signature, raw_json
            ) VALUES (
                'position', 'pool', 'CLOSED',
                'enter', 'sig-enter', 't',
                'settle', 'sig-settle', 't',
                0, 'settle', 'sig-settle', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address, event_time,
                action, prior_status, next_status, raw_json
            ) VALUES (
                'enter', 'sig-enter', 'position', 't',
                'ENTER', NULL, 'OPEN', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_execution_receipts(
                decision_id, signature, observed_at, mode, action,
                pool_address, intent_status, slot, block_time,
                network_fee_lamports, compute_units_consumed,
                succeeded, event_count, add_request_count,
                rebalance_request_count, raw_json
            ) VALUES (
                'settle', 'sig-settle', 't',
                'LIVE', 'EXIT', 'pool', 'CONFIRMED',
                2, 3, 5000, 1000, 1, 0, 0, 0, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_position_closure_proofs(
                decision_id, signature, position_address,
                receipt_slot, proof_slot, observed_at, closed, raw_json
            ) VALUES (
                'settle', 'sig-settle', 'position',
                2, 3, 't', 1, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address, event_time,
                action, prior_status, next_status, raw_json
            ) VALUES (
                'settle', 'sig-settle', 'position', 't',
                'CLOSE', 'LIQUIDITY_REMOVED', 'CLOSED', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_position_outcomes(
                position_address, pool_address,
                opened_decision_id, closed_decision_id,
                execution_count,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic,
                composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, label_status,
                created_at, raw_json
            ) VALUES (
                'position', 'pool', 'enter', 'settle', 2,
                '0', '0', '0', '0', '0', '0', '0', '0',
                10000, 'ATOMIC_ONLY', 't', '{}'
            )
            """
        )

    report = audit_live_execution_ledger(storage)

    assert report.clean is True
    assert report.unprocessed_receipt_decisions == ()
    assert report.unapplied_position_effect_decisions == ()
    assert report.closure_without_position_event == ()
    assert report.closed_positions_missing_outcome == ()


def test_live_ledger_audit_flags_valued_outcome_without_valuation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_position_outcomes(
                position_address, pool_address,
                opened_decision_id, closed_decision_id,
                execution_count,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic,
                composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, label_status,
                created_at, raw_json
            ) VALUES (
                'position', 'pool', 'enter', 'settle', 1,
                '0', '0', '0', '0', '0', '0', '0', '0',
                0, 'VALUED', 't', '{}'
            )
            """
        )

    report = audit_live_execution_ledger(storage)

    assert report.clean is False
    assert report.valued_outcomes_missing_evidence == ("position",)


def test_live_ledger_audit_flags_orphan_valuation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_position_outcomes(
                position_address, pool_address,
                opened_decision_id, closed_decision_id,
                execution_count,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic,
                composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, label_status,
                created_at, raw_json
            ) VALUES (
                'position', 'pool', 'enter', 'settle', 1,
                '0', '0', '0', '0', '0', '0', '0', '0',
                0, 'ATOMIC_ONLY', 't', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_position_valuations(
                position_address, pool_address,
                opened_decision_id, closed_decision_id,
                quote_unit, valued_execution_count,
                principal_cashflow_quote, composition_cost_quote,
                fee_income_quote, reward_income_quote,
                network_cost_quote, realized_pnl_quote,
                entry_outflow_quote, realized_return_bps,
                max_age_seconds, created_at,
                quote_evidence_json, raw_json
            ) VALUES (
                'position', 'pool', 'enter', 'settle',
                'ACCOUNT_QUOTE', 1,
                '0', '0', '0', '0', '0', '0',
                '1', 0, 300, 't', '[]', '{}'
            )
            """
        )

    report = audit_live_execution_ledger(storage)

    assert report.clean is False
    assert report.orphan_valuation_positions == ("position",)
