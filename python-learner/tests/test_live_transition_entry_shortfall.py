from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import meteora_learner.live_transition_entry_shortfall as MODULE


def _transition():
    return {
        "transition_id": "T1",
        "previous_position_address": "OLD",
        "next_position_address": "NEW",
        "previous_pool_address": "POOL-A",
        "next_pool_address": "POOL-B",
        "previous_exit_decision_id": "OLD-EXIT",
        "next_enter_decision_id": "NEW-ENTER",
        "previous_exit_at": "2026-01-01T01:00:00+00:00",
        "next_enter_at": "2026-01-01T02:00:00+00:00",
        "transition_kind": "POOL_SWITCH",
        "annotation_source": "EXPLICIT_RESEARCH_REVIEW",
    }


def _enter_row(*, quote_evidence=None):
    if quote_evidence is None:
        quote_evidence = [
            {
                "decision_id": "NEW-ENTER",
                "quotes": {
                    "principal_x": {
                        "quote_unit": "USD",
                        "quote_per_atomic": "2",
                    },
                    "principal_y": {
                        "quote_unit": "USD",
                        "quote_per_atomic": "1",
                    },
                },
            }
        ]
    return {
        "position_address": "NEW",
        "pool_address": "POOL-B",
        "quote_unit": "USD",
        "entry_outflow_quote": "330",
        "max_age_seconds": 60,
        "quote_evidence_json": json.dumps(quote_evidence),
        "decision_id": "NEW-ENTER",
        "signature": "SIG-ENTER",
        "event_time": "2026-01-01T02:00:00+00:00",
        "action": "ENTER",
        "prior_status": None,
        "next_status": "OPEN",
    }


def _event(*, parent=7, amount_x="90", amount_y="150"):
    return {
        "event_type": "AddLiquidity",
        "position_address": "NEW",
        "lb_pair": "POOL-B",
        "parent_ix_index": parent,
        "amount_x": amount_x,
        "amount_y": amount_y,
    }


class Store:
    def __init__(
        self,
        *,
        transitions=None,
        enter_rows=None,
        events=None,
        request=None,
    ):
        self.transitions = transitions if transitions is not None else [_transition()]
        self.enter_rows = enter_rows if enter_rows is not None else [_enter_row()]
        self.events = events if events is not None else [_event()]
        self.request = request if request is not None else {
            "requested_amount_x": "100",
            "requested_amount_y": "200",
        }

    def live_position_transition_rows(self):
        return list(self.transitions)

    def live_valued_action_rows(self):
        return list(self.enter_rows)

    def load_transaction_events(self, signature):
        assert signature == "SIG-ENTER"
        return list(self.events)

    def add_liquidity_request(self, signature, instruction_index):
        assert signature == "SIG-ENTER"
        assert instruction_index == 7
        return self.request


def install(monkeypatch, store):
    monkeypatch.setattr(
        MODULE,
        "ResearchStore",
        lambda _database_path: store,
    )


def test_entry_shortfall_values_requested_vs_actual_with_persisted_enter_quotes(
    monkeypatch,
):
    install(monkeypatch, Store())

    report = MODULE.build_live_transition_entry_shortfall_evidence("/db")

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.transition_pairs_inferred is False
    assert report.explicit_transition_links_required is True
    assert report.successor_enter_only is True
    assert report.request_event_match_required is True
    assert report.quote_backed is True
    assert report.exit_execution_shortfall_included is False
    assert report.market_impact_included is False
    assert report.opportunity_cost_included is False
    assert report.full_transition_economics_included is False
    assert report.links_seen == 1
    assert report.samples_seen == 1
    assert report.gaps_seen == 0

    sample = report.samples[0]
    assert sample.requested_value_quote == "400"
    assert sample.actual_value_quote == "330"
    assert sample.entry_execution_shortfall_quote == "70"
    assert sample.entry_execution_shortfall_bps == 1750.0
    assert sample.token_x_quote_per_atomic == "2"
    assert sample.token_y_quote_per_atomic == "1"
    assert report.mean_entry_execution_shortfall_bps == 1750.0


def test_entry_shortfall_requires_unique_matching_add_event(monkeypatch):
    install(
        monkeypatch,
        Store(events=[_event(), _event(parent=8)]),
    )

    report = MODULE.build_live_transition_entry_shortfall_evidence("/db")

    assert report.samples_seen == 0
    assert report.gaps_seen == 1
    assert report.gaps[0].reason == "ENTER_ADD_EVENT_AMBIGUOUS"


def test_entry_shortfall_keeps_missing_exact_side_quote_visible(monkeypatch):
    evidence = [
        {
            "decision_id": "NEW-ENTER",
            "quotes": {
                "principal_x": {
                    "quote_unit": "USD",
                    "quote_per_atomic": "2",
                },
            },
        }
    ]
    install(
        monkeypatch,
        Store(enter_rows=[_enter_row(quote_evidence=evidence)]),
    )

    report = MODULE.build_live_transition_entry_shortfall_evidence("/db")

    assert report.samples_seen == 0
    assert report.gaps_seen == 1
    assert report.gaps[0].reason == "ENTER_TOKEN_Y_QUOTE_MISSING"


def test_entry_shortfall_refuses_actual_amount_above_decoded_request(monkeypatch):
    install(
        monkeypatch,
        Store(events=[_event(amount_x="101")]),
    )

    report = MODULE.build_live_transition_entry_shortfall_evidence("/db")

    assert report.samples_seen == 0
    assert report.gaps[0].reason == "ENTER_ACTUAL_EXCEEDS_REQUESTED"


def test_entry_shortfall_refuses_non_explicit_transition(monkeypatch):
    row = _transition()
    row["annotation_source"] = "INFERRED"
    install(monkeypatch, Store(transitions=[row]))

    with pytest.raises(ValueError, match="explicit research review"):
        MODULE.build_live_transition_entry_shortfall_evidence("/db")
