from __future__ import annotations

import json

import pytest

from meteora_learner.live_action_cost_evidence import (
    build_live_action_cost_evidence,
)
from meteora_learner.storage import Storage


def _seed_valued_position(storage: Storage) -> None:
    evidence = [
        {
            "decision_id": "OPEN",
            "observed_at": "2026-01-01T00:00:00+00:00",
            "composition_cost_quote": "10",
            "network_cost_quote": "2",
            "fee_income_quote": "0",
            "reward_income_quote": "0",
            "net_cashflow_quote": "-1012",
        },
        {
            "decision_id": "REBAL",
            "observed_at": "2026-01-01T01:00:00+00:00",
            "composition_cost_quote": "3",
            "network_cost_quote": "2",
            "fee_income_quote": "8",
            "reward_income_quote": "0",
            "net_cashflow_quote": "3",
        },
        {
            "decision_id": "EXIT",
            "observed_at": "2026-01-01T02:00:00+00:00",
            "composition_cost_quote": "0",
            "network_cost_quote": "2",
            "fee_income_quote": "12",
            "reward_income_quote": "0",
            "net_cashflow_quote": "1100",
        },
    ]

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_position_valuations(
                position_address, pool_address,
                opened_decision_id, closed_decision_id,
                quote_unit, valued_execution_count,
                principal_cashflow_quote,
                composition_cost_quote,
                fee_income_quote, reward_income_quote,
                network_cost_quote, realized_pnl_quote,
                entry_outflow_quote, realized_return_bps,
                max_age_seconds, created_at,
                quote_evidence_json, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "POS",
                "POOL",
                "OPEN",
                "EXIT",
                "USD",
                3,
                "100",
                "13",
                "20",
                "0",
                "6",
                "101",
                "1000",
                1010,
                60,
                "2026-01-01T02:01:00+00:00",
                json.dumps(evidence),
                "{}",
            ),
        )
        for decision_id, signature, event_time, action, prior, next_status in (
            (
                "OPEN",
                "SIG-OPEN",
                "2026-01-01T00:00:00+00:00",
                "ENTER",
                None,
                "OPEN",
            ),
            (
                "REBAL",
                "SIG-REBAL",
                "2026-01-01T01:00:00+00:00",
                "REBALANCE",
                "OPEN",
                "OPEN",
            ),
            (
                "EXIT",
                "SIG-EXIT",
                "2026-01-01T02:00:00+00:00",
                "EXIT",
                "OPEN",
                "CLOSED",
            ),
        ):
            conn.execute(
                """
                INSERT INTO live_position_events(
                    decision_id, signature, position_address,
                    event_time, action, prior_status, next_status,
                    min_bin_id, max_bin_id, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    signature,
                    "POS",
                    event_time,
                    action,
                    prior,
                    next_status,
                    -2,
                    2,
                    "{}",
                ),
            )


def test_live_action_costs_use_persisted_quote_evidence(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_valued_position(storage)

    report = build_live_action_cost_evidence(str(storage.path))

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.transition_pairs_inferred is False
    assert report.samples_seen == 3
    assert report.positions_seen == 1
    assert report.pools_seen == 1
    assert report.actions_seen == ("ENTER", "EXIT", "REBALANCE")

    by_action = {item.action: item for item in report.samples}
    assert by_action["ENTER"].direct_cost_bps == 120.0
    assert by_action["REBALANCE"].direct_cost_bps == 50.0
    assert by_action["EXIT"].direct_cost_bps == 20.0

    summaries = {
        item.action: item
        for item in report.action_summaries
    }
    assert summaries["ENTER"].mean_composition_cost_bps == 100.0
    assert summaries["EXIT"].mean_network_cost_bps == 20.0


def test_live_action_costs_do_not_infer_switch_pairs(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_valued_position(storage)

    report = build_live_action_cost_evidence(str(storage.path))
    record = report.to_record()

    assert record["transition_pairs_inferred"] is False
    assert "switch_cost_bps" not in record
    assert "recommended_action" not in record


def test_live_action_costs_require_exact_decision_evidence(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed_valued_position(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE live_position_valuations
            SET quote_evidence_json = '[]'
            WHERE position_address = 'POS'
            """
        )

    with pytest.raises(
        ValueError,
        match="exactly one quote evidence item",
    ):
        build_live_action_cost_evidence(str(storage.path))
