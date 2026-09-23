from types import SimpleNamespace

import pytest

import meteora_learner.phase9_explicit_inputs as inputs_module
from meteora_learner.cross_pool_research import (
    CrossPoolResearchCandidate,
    CrossPoolResearchReport,
)
from meteora_learner.phase9_explicit_inputs import (
    PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE,
    PHASE9_EXPLICIT_INPUTS_SCOPE,
    audit_phase9_explicit_inputs,
    build_phase9_explicit_input_template,
    load_phase9_explicit_inputs,
    parse_phase9_explicit_inputs,
    persist_phase9_explicit_inputs,
    run_phase9_explicit_research,
)
from meteora_learner.storage import Storage


def filled_payload():
    return {
        "static_hedges": [
            {
                "pool_address": "pool-a",
                "amount_x": 100,
                "amount_y": 200,
                "instrument": {
                    "instrument_id": "SOL-PERP",
                    "venue": "TEST-VENUE",
                    "available_liquidity_y_atomic": 1_000_000,
                    "max_liquidity_share_bps": 1000,
                    "max_leverage": 1.0,
                    "funding_bps_per_holding_window": 2.0,
                },
                "criteria": {
                    "observation_limit": 96,
                    "holding_observations": 6,
                    "hedge_fraction": 1.0,
                    "hedge_round_trip_cost_bps": 10.0,
                    "min_windows": 20,
                    "min_mean_abs_return_reduction_bps": 0.0,
                    "min_worst_loss_improvement_bps": 0.0,
                    "max_mean_return_drag_bps": 100.0,
                },
                "as_of": "2026-09-23T13:00:00+00:00",
            }
        ],
        "pool_inputs": [
            {
                "pool_address": "pool-a",
                "amount_x": 100,
                "amount_y": 200,
                "requested_quote": 100.0,
                "network_cost_y_atomic": 1000,
            },
            {
                "pool_address": "pool-b",
                "amount_x": 120,
                "amount_y": 180,
                "requested_quote": 100.0,
                "network_cost_y_atomic": 1200,
            },
        ],
        "portfolio": {
            "account_equity_quote": 1000.0,
            "cash_quote": 700.0,
            "current_deployed_quote": 300.0,
            "portfolio_drawdown_bps": 200,
            "observation_limit": 12,
            "half_widths": [0, 1, 2, 5, 10],
            "center_offsets": [0],
            "strategies": ["SPOT", "CURVE", "BID_ASK"],
            "max_share_bps": 500,
            "favor_x_in_active_bin": False,
            "budget_quote": 200.0,
            "allocation_criteria": {
                "max_positions": 3,
                "min_positions": 2,
                "max_pool_allocation_bps": 5000,
                "min_range_survival_ratio": 0.75,
                "min_excess_vs_hold_bps": 0,
                "min_position_quote": 10.0,
                "min_budget_utilization_rate": 0.75,
            },
        },
    }


def test_explicit_input_template_does_not_invent_economic_assumptions(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": pool,
                "active_bin_id": 0,
                "bin_step": 25,
                "token_x_mint": f"{pool}-x",
                "token_y_mint": f"{pool}-y",
                "bin_arrays": [],
            },
            observed_at="2026-09-23T12:00:00+00:00",
        )

    template = build_phase9_explicit_input_template(storage)

    assert template["static_hedges"][0]["pool_address"] == "pool-a"
    assert template["static_hedges"][0]["amount_x"] is None
    assert template["static_hedges"][0]["amount_y"] is None
    assert (
        template["static_hedges"][0]["instrument"]["instrument_id"]
        is None
    )
    assert (
        template["static_hedges"][0]["instrument"][
            "available_liquidity_y_atomic"
        ]
        is None
    )
    assert (
        template["static_hedges"][0]["criteria"][
            "hedge_round_trip_cost_bps"
        ]
        is None
    )
    assert template["portfolio"]["account_equity_quote"] is None
    assert template["portfolio"]["budget_quote"] is None
    assert all(
        item["requested_quote"] is None
        and item["network_cost_y_atomic"] is None
        for item in template["pool_inputs"]
    )

    with pytest.raises(ValueError, match="is required"):
        parse_phase9_explicit_inputs(template)


def test_explicit_inputs_validate_and_round_trip_with_sha(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    inputs = parse_phase9_explicit_inputs(filled_payload())

    artifact = persist_phase9_explicit_inputs(
        storage,
        inputs=inputs,
    )
    loaded = load_phase9_explicit_inputs(
        storage,
        evidence_id=artifact.evidence_id,
    )

    assert loaded is not None
    assert len(artifact.artifact_sha256) == 64
    assert loaded.artifact_sha256 == artifact.artifact_sha256
    assert loaded.inputs.to_record() == inputs.to_record()
    assert loaded.inputs.research_only is True
    assert loaded.inputs.policy_actionable is False
    assert loaded.inputs.execution_wired is False


def test_explicit_inputs_reject_mixed_requested_notional():
    payload = filled_payload()
    payload["pool_inputs"][1]["requested_quote"] = 200.0

    with pytest.raises(ValueError, match="requested_quote"):
        parse_phase9_explicit_inputs(payload)


def test_explicit_inputs_reject_missing_hedge_market_assumption():
    payload = filled_payload()
    payload["static_hedges"][0]["instrument"][
        "available_liquidity_y_atomic"
    ] = None

    with pytest.raises(
        ValueError,
        match="available_liquidity_y_atomic is required",
    ):
        parse_phase9_explicit_inputs(payload)


class DummyResearchReport:
    research_qualified = True

    def __init__(self, kind):
        self.kind = kind

    def to_record(self):
        return {
            "kind": self.kind,
            "research_qualified": True,
            "research_only": True,
            "policy_actionable": False,
        }


def test_explicit_research_runner_binds_candidate_artifact_to_input_sha(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    artifact = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(filled_payload()),
    )
    comparison = CrossPoolResearchReport(
        plans_seen=2,
        comparable_plans=2,
        excluded_plans=0,
        leader_pool_address="pool-a",
        ranking_rule="test",
        candidates=(
            CrossPoolResearchCandidate(
                rank=1,
                pool_address="pool-a",
                strategy="SPOT",
                min_bin_id=-1,
                max_bin_id=1,
                half_width=1,
                center_offset=0,
                net_return_bps=100,
                hold_return_bps=50,
                excess_vs_hold_initial_bps=50,
                range_survival_ratio=1.0,
                max_observed_share_bps=100,
                sized_quote=100.0,
                phase2_ready=True,
                policy_authorized=True,
            ),
            CrossPoolResearchCandidate(
                rank=2,
                pool_address="pool-b",
                strategy="CURVE",
                min_bin_id=-2,
                max_bin_id=2,
                half_width=2,
                center_offset=0,
                net_return_bps=90,
                hold_return_bps=50,
                excess_vs_hold_initial_bps=40,
                range_survival_ratio=1.0,
                max_observed_share_bps=100,
                sized_quote=100.0,
                phase2_ready=True,
                policy_authorized=True,
            ),
        ),
    )
    seen = {}

    monkeypatch.setattr(
        inputs_module,
        "research_static_inventory_hedge",
        lambda *args, **kwargs: DummyResearchReport("hedge"),
    )
    monkeypatch.setattr(
        inputs_module,
        "persist_static_hedge_research",
        lambda *args, **kwargs: 101,
    )
    monkeypatch.setattr(
        inputs_module,
        "build_multi_pool_research",
        lambda *args, **kwargs: SimpleNamespace(comparison=comparison),
    )

    def persist_candidates(storage, *, comparison, source_inputs, assumptions):
        seen["comparison"] = comparison
        seen["assumptions"] = assumptions
        return 202, "a" * 64

    monkeypatch.setattr(
        inputs_module,
        "persist_portfolio_candidate_research",
        persist_candidates,
    )
    monkeypatch.setattr(
        inputs_module,
        "research_portfolio_allocation",
        lambda *args, **kwargs: DummyResearchReport("allocation"),
    )
    monkeypatch.setattr(
        inputs_module,
        "persist_portfolio_allocation_research",
        lambda *args, **kwargs: 303,
    )

    report = run_phase9_explicit_research(
        storage,
        artifact=artifact,
        persist=True,
    )

    assert report.explicit_research_ready is True
    assert report.static_hedge_evidence_ids == (101,)
    assert report.candidate_evidence_id == 202
    assert report.portfolio_allocation_evidence_id == 303
    assert seen["comparison"] is comparison
    assert (
        seen["assumptions"]["explicit_input_evidence_id"]
        == artifact.evidence_id
    )
    assert (
        seen["assumptions"]["explicit_input_artifact_sha256"]
        == artifact.artifact_sha256
    )
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_explicit_inputs_audit_accepts_valid_latest_artifact(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    artifact = persist_phase9_explicit_inputs(
        storage,
        inputs=parse_phase9_explicit_inputs(filled_payload()),
    )

    audit = audit_phase9_explicit_inputs(storage)

    assert audit.exists is True
    assert audit.valid is True
    assert audit.boundary_valid is True
    assert audit.evidence_id == artifact.evidence_id
    assert audit.artifact_sha256 == artifact.artifact_sha256
    assert audit.reasons == ()


def test_explicit_inputs_audit_rejects_wrong_sha(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    inputs = parse_phase9_explicit_inputs(filled_payload())
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE,
        pool_address=PHASE9_EXPLICIT_INPUTS_SCOPE,
        as_of=None,
        status="INPUTS_VALIDATED",
        qualified=False,
        evidence={
            "artifact_sha256": "0" * 64,
            "inputs": inputs.to_record(),
        },
    )

    audit = audit_phase9_explicit_inputs(storage)

    assert audit.valid is False
    assert any("SHA-256 does not match" in reason for reason in audit.reasons)


def test_explicit_inputs_audit_rejects_qualified_input_artifact(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    inputs = parse_phase9_explicit_inputs(filled_payload())
    record = inputs.to_record()
    digest = inputs_module._canonical_sha256(record)
    storage.save_advanced_edge_evidence(
        edge_type=PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE,
        pool_address=PHASE9_EXPLICIT_INPUTS_SCOPE,
        as_of=None,
        status="INPUTS_VALIDATED",
        qualified=True,
        evidence={
            "artifact_sha256": digest,
            "inputs": record,
        },
    )

    audit = audit_phase9_explicit_inputs(storage)

    assert audit.valid is False
    assert any(
        "must not be marked as qualified research" in reason
        for reason in audit.reasons
    )
