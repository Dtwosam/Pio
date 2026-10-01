from __future__ import annotations

import json

from meteora_learner.live_position_transition import (
    record_live_position_transition,
)
from meteora_learner.live_transition_cost_evidence import (
    build_live_transition_cost_evidence,
)
from meteora_learner.storage import Storage


def _position(
    storage: Storage,
    *,
    position: str,
    pool: str,
    status: str,
    enter: str,
    enter_at: str,
    exit_decision: str | None,
    exit_at: str | None,
) -> None:
    last_decision = exit_decision or enter
    last_at = exit_at or enter_at
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO live_positions(
                position_address, pool_address, status,
                opened_decision_id, opened_signature, opened_at,
                min_bin_id, max_bin_id,
                last_decision_id, last_signature, last_observed_at,
                rebalances, closed_decision_id, closed_signature,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, -2, 2, ?, ?, ?, 0, ?, ?, '{}')
            """,
            (
                position,
                pool,
                status,
                enter,
                f"SIG-{enter}",
                enter_at,
                last_decision,
                f"SIG-{last_decision}",
                last_at,
                exit_decision,
                (
                    f"SIG-{exit_decision}"
                    if exit_decision is not None
                    else None
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address,
                event_time, action, prior_status, next_status,
                min_bin_id, max_bin_id, raw_json
            ) VALUES (?, ?, ?, ?, 'ENTER', NULL, 'OPEN', -2, 2, '{}')
            """,
            (enter, f"SIG-{enter}", position, enter_at),
        )
        if exit_decision is not None:
            conn.execute(
                """
                INSERT INTO live_position_events(
                    decision_id, signature, position_address,
                    event_time, action, prior_status, next_status,
                    min_bin_id, max_bin_id, raw_json
                ) VALUES (?, ?, ?, ?, 'EXIT', 'OPEN',
                          'LIQUIDITY_REMOVED', -2, 2, '{}')
                """,
                (
                    exit_decision,
                    f"SIG-{exit_decision}",
                    position,
                    exit_at,
                ),
            )


def _valuation(
    storage: Storage,
    *,
    position: str,
    pool: str,
    enter: str,
    exit_decision: str,
    quote_unit: str,
    entry_outflow: str,
    enter_composition: str,
    enter_network: str,
    exit_composition: str,
    exit_network: str,
) -> None:
    evidence = [
        {
            "decision_id": enter,
            "observed_at": (
                "2026-01-01T00:00:00+00:00"
                if position == "OLD"
                else "2026-01-01T02:00:00+00:00"
            ),
            "composition_cost_quote": enter_composition,
            "network_cost_quote": enter_network,
            "fee_income_quote": "0",
            "reward_income_quote": "0",
            "net_cashflow_quote": f"-{entry_outflow}",
        },
        {
            "decision_id": exit_decision,
            "observed_at": (
                "2026-01-01T01:00:00+00:00"
                if position == "OLD"
                else "2026-01-01T03:00:00+00:00"
            ),
            "composition_cost_quote": exit_composition,
            "network_cost_quote": exit_network,
            "fee_income_quote": "0",
            "reward_income_quote": "0",
            "net_cashflow_quote": entry_outflow,
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
            ) VALUES (?, ?, ?, ?, ?, 2, '0', ?, '0', '0', ?, '0',
                      ?, 0, 60, ?, ?, '{}')
            """,
            (
                position,
                pool,
                enter,
                exit_decision,
                quote_unit,
                str(
                    float(enter_composition)
                    + float(exit_composition)
                ),
                str(float(enter_network) + float(exit_network)),
                entry_outflow,
                (
                    "2026-01-01T01:01:00+00:00"
                    if position == "OLD"
                    else "2026-01-01T03:01:00+00:00"
                ),
                json.dumps(evidence),
            ),
        )


def _seed(
    storage: Storage,
    *,
    include_next_valuation: bool = True,
    next_quote_unit: str = "USD",
) -> None:
    _position(
        storage,
        position="OLD",
        pool="POOL-A",
        status="CLOSED",
        enter="OLD-ENTER",
        enter_at="2026-01-01T00:00:00+00:00",
        exit_decision="OLD-EXIT",
        exit_at="2026-01-01T01:00:00+00:00",
    )
    _position(
        storage,
        position="NEW",
        pool="POOL-B",
        status="CLOSED" if include_next_valuation else "OPEN",
        enter="NEW-ENTER",
        enter_at="2026-01-01T02:00:00+00:00",
        exit_decision=(
            "NEW-EXIT" if include_next_valuation else None
        ),
        exit_at=(
            "2026-01-01T03:00:00+00:00"
            if include_next_valuation
            else None
        ),
    )
    _valuation(
        storage,
        position="OLD",
        pool="POOL-A",
        enter="OLD-ENTER",
        exit_decision="OLD-EXIT",
        quote_unit="USD",
        entry_outflow="1000",
        enter_composition="10",
        enter_network="2",
        exit_composition="0",
        exit_network="3",
    )
    if include_next_valuation:
        _valuation(
            storage,
            position="NEW",
            pool="POOL-B",
            enter="NEW-ENTER",
            exit_decision="NEW-EXIT",
            quote_unit=next_quote_unit,
            entry_outflow="800",
            enter_composition="4",
            enter_network="1",
            exit_composition="0",
            exit_network="2",
        )

    record_live_position_transition(
        storage,
        previous_position_address="OLD",
        next_position_address="NEW",
    )


def test_transition_cost_uses_explicit_quote_backed_exit_and_enter(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)

    report = build_live_transition_cost_evidence(str(storage.path))

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.transition_pairs_inferred is False
    assert report.explicit_transition_links_required is True
    assert report.direct_costs_quote_backed is True
    assert report.full_transition_economics_included is False
    assert report.links_seen == 1
    assert report.samples_seen == 1
    assert report.gaps_seen == 0
    assert report.pool_switch_samples == 1

    sample = report.samples[0]
    assert sample.transition_kind == "POOL_SWITCH"
    assert sample.quote_unit == "USD"
    assert sample.exit_direct_cost_quote == "3"
    assert sample.enter_direct_cost_quote == "5"
    assert sample.direct_transition_cost_quote == "8"
    assert sample.exit_direct_cost_bps_on_previous_entry == 30.0
    assert sample.enter_direct_cost_bps_on_reentry_capital == 62.5
    assert (
        sample.direct_transition_cost_bps_on_reentry_capital
        == 100.0
    )
    assert sample.transition_gap_seconds == 3600.0
    assert (
        report.mean_direct_transition_cost_bps_on_reentry_capital
        == 100.0
    )


def test_unvalued_successor_stays_visible_as_gap(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage, include_next_valuation=False)

    report = build_live_transition_cost_evidence(str(storage.path))

    assert report.links_seen == 1
    assert report.samples_seen == 0
    assert report.gaps_seen == 1
    assert report.gaps[0].reason == "NEXT_ENTER_ACTION_NOT_VALUED"
    assert (
        report.mean_direct_transition_cost_bps_on_reentry_capital
        is None
    )


def test_quote_unit_mismatch_stays_visible_as_gap(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage, next_quote_unit="EUR")

    report = build_live_transition_cost_evidence(str(storage.path))

    assert report.links_seen == 1
    assert report.samples_seen == 0
    assert report.gaps_seen == 1
    assert report.gaps[0].reason == "QUOTE_UNIT_MISMATCH"


def test_no_explicit_links_produces_empty_descriptive_report(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")

    report = build_live_transition_cost_evidence(str(storage.path))

    assert report.links_seen == 0
    assert report.samples_seen == 0
    assert report.gaps_seen == 0
    assert report.transition_pairs_inferred is False
    assert report.full_transition_economics_included is False
