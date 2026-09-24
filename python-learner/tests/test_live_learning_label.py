import json
import sqlite3

import pytest

from meteora_learner.live_learning_label import build_live_learning_label
from meteora_learner.storage import Storage


def seed(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_positions(
                position_address, pool_address, status,
                opened_decision_id, opened_signature, opened_at,
                min_bin_id, max_bin_id,
                last_decision_id, last_signature, last_observed_at,
                rebalances, closed_decision_id, closed_signature, raw_json
            ) VALUES (
                'position', 'pool', 'CLOSED',
                'enter', 'sig-enter', '2026-09-23T09:00:00+00:00',
                -3, 7,
                'settle', 'sig-settle', '2026-09-23T10:00:00+00:00',
                1, 'settle', 'sig-settle', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address, event_time,
                action, prior_status, next_status,
                min_bin_id, max_bin_id, raw_json
            ) VALUES (
                'enter', 'sig-enter', 'position',
                '2026-09-23T09:00:00+00:00',
                'ENTER', NULL, 'OPEN', -5, 5, '{}'
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
                'position', 'pool', 'enter', 'settle', 3,
                '0', '0', '0', '0', '0', '0', '0', '0',
                15000, 'VALUED',
                '2026-09-23T10:00:00+00:00', '{}'
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
                'ACCOUNT_QUOTE', 3,
                '-10', '4', '40', '15', '0.0015',
                '40.9985', '404.0005', 1014,
                300, '2026-09-23T10:00:00+00:00',
                '[]', '{}'
            )
            """
        )
        context = {
            "decision_id": "enter",
            "mode": "LIVE",
            "action": "ENTER",
            "pool_address": "pool",
            "status": "CONFIRMED",
            "created_at_unix": 100,
            "updated_at_unix": 110,
            "capital_quote": "400",
            "account_equity_quote": "1000",
            "portfolio_deployed_quote": "100",
            "daily_drawdown_pct": "0.5",
            "min_bin_id": -5,
            "max_bin_id": 5,
            "strategy": "CURVE",
            "expected_net_return_pct": "8.5",
            "expected_downside_pct": "2",
            "model_version": "model-v1",
            "data_age_seconds": 5,
            "signature": "sig-enter",
        }
        conn.execute(
            """
            INSERT INTO live_decision_contexts(
                decision_id, mode, action, pool_address, status,
                created_at_unix, updated_at_unix,
                capital_quote, account_equity_quote,
                portfolio_deployed_quote, daily_drawdown_pct,
                min_bin_id, max_bin_id, strategy,
                expected_net_return_pct, expected_downside_pct,
                model_version, data_age_seconds, signature, raw_json
            ) VALUES (
                'enter', 'LIVE', 'ENTER', 'pool', 'CONFIRMED',
                100, 110, '400', '1000', '100', '0.5',
                -5, 5, 'CURVE', '8.5', '2',
                'model-v1', 5, 'sig-enter', ?
            )
            """,
            (json.dumps(context, sort_keys=True, separators=(",", ":")),),
        )


def test_builds_realized_live_learning_label(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)

    result = build_live_learning_label(
        storage,
        position_address="position",
    )

    assert result.reused_existing is False
    label = result.label
    assert label.model_version == "model-v1"
    assert label.strategy == "CURVE"
    assert (label.min_bin_id, label.max_bin_id) == (-5, 5)
    assert label.range_width_bins == 11
    assert label.proposed_capital_quote == "400"
    assert label.realized_pnl_quote == "40.9985"
    assert label.realized_return_bps == 1014
    assert label.prediction_error_bps == 164
    assert label.target_positive_return == 1


def test_live_learning_label_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)

    first = build_live_learning_label(
        storage,
        position_address="position",
    )
    second = build_live_learning_label(
        storage,
        position_address="position",
    )

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.label == second.label


def test_range_attribution_mismatch_fails_closed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE live_decision_contexts
            SET min_bin_id = -4
            WHERE decision_id = 'enter'
            """
        )

    with pytest.raises(ValueError, match="range differs"):
        build_live_learning_label(
            storage,
            position_address="position",
        )


def test_unvalued_position_cannot_be_labeled(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            DELETE FROM live_position_valuations
            WHERE position_address = 'position'
            """
        )

    with pytest.raises(ValueError, match="valued"):
        build_live_learning_label(
            storage,
            position_address="position",
        )


def test_opening_context_must_be_confirmed_enter(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE live_decision_contexts
            SET action = 'REBALANCE'
            WHERE decision_id = 'enter'
            """
        )

    with pytest.raises(ValueError, match="LIVE ENTER"):
        build_live_learning_label(
            storage,
            position_address="position",
        )


def test_live_learning_labels_are_database_immutable(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    build_live_learning_label(
        storage,
        position_address="position",
    )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with storage.connect() as conn:
            conn.execute(
                """
                UPDATE live_learning_labels
                SET realized_return_bps = 0
                WHERE position_address = 'position'
                """
            )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with storage.connect() as conn:
            conn.execute(
                """
                DELETE FROM live_learning_labels
                WHERE position_address = 'position'
                """
            )
