import pytest

from meteora_learner.execution_decision_context_ingest import (
    ingest_execution_decision_context,
)
from meteora_learner.storage import Storage


def context(**overrides):
    payload = {
        "decision_id": "decision",
        "mode": "LIVE",
        "action": "ENTER",
        "pool_address": "pool",
        "status": "CONFIRMED",
        "created_at_unix": 100,
        "updated_at_unix": 110,
        "capital_quote": 25.0,
        "account_equity_quote": 1000.0,
        "portfolio_deployed_quote": 100.0,
        "daily_drawdown_pct": 0.5,
        "min_bin_id": -5,
        "max_bin_id": 5,
        "strategy": "CURVE",
        "expected_net_return_pct": 2.5,
        "expected_downside_pct": 1.0,
        "model_version": "model-v1",
        "data_age_seconds": 7,
        "signature": "signature",
    }
    payload.update(overrides)
    return payload


def insert_receipt(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_execution_receipts(
                decision_id, signature, observed_at, mode, action,
                pool_address, intent_status, slot, block_time,
                network_fee_lamports, compute_units_consumed, succeeded,
                event_count, add_request_count, rebalance_request_count,
                raw_json
            ) VALUES (
                'decision', 'signature',
                '2026-09-23T10:00:00+00:00',
                'LIVE', 'ENTER', 'pool', 'CONFIRMED',
                1, 2, 5000, 1000, 1, 1, 0, 0, '{}'
            )
            """
        )


def test_terminal_decision_context_ingests_and_reconciles_receipt(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    insert_receipt(storage)

    result = ingest_execution_decision_context(
        storage,
        context(),
    )

    assert result.reused_existing is False
    assert result.receipt_reconciled is True
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT strategy, model_version, capital_quote,
                   min_bin_id, max_bin_id
            FROM live_decision_contexts
            WHERE decision_id = 'decision'
            """
        ).fetchone()
    assert row == ("CURVE", "model-v1", "25.0", -5, 5)


def test_decision_context_is_idempotent_and_immutable(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    insert_receipt(storage)

    first = ingest_execution_decision_context(storage, context())
    second = ingest_execution_decision_context(storage, context())

    assert first.reused_existing is False
    assert second.reused_existing is True

    with pytest.raises(ValueError, match="different immutable"):
        ingest_execution_decision_context(
            storage,
            context(model_version="changed"),
        )


def test_receipt_conflict_fails_closed(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    insert_receipt(storage)

    with pytest.raises(ValueError, match="pool conflicts"):
        ingest_execution_decision_context(
            storage,
            context(pool_address="other"),
        )


def test_non_terminal_context_is_rejected(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="terminal"):
        ingest_execution_decision_context(
            storage,
            context(status="SENT"),
        )


def test_terminal_context_requires_signature(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="requires a signature"):
        ingest_execution_decision_context(
            storage,
            context(signature=None),
        )
