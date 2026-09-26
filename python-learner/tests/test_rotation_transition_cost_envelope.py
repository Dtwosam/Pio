from dataclasses import replace
from types import SimpleNamespace

import pytest

from meteora_learner.rotation_transition_cost_envelope import (
    ROTATION_TRANSITION_COST_ENVELOPE_EVIDENCE_TYPE,
    build_rotation_transition_cost_envelope,
    persist_rotation_transition_cost_envelope,
)
from meteora_learner.storage import Storage


def network_sample(signature, value, *, reason=None):
    return SimpleNamespace(
        signature=signature,
        network_fee_quote=value,
        exclusion_reason=reason,
    )


def semantics_sample(
    signature,
    *,
    pool="pool",
    base_residual=0.0,
    fee_residual=0.0,
    eligible=True,
    quote_eligible=True,
    exclusion_reason=None,
    quote_exclusion_reason=None,
    claim=True,
    evidence_class="FEE_SEPARATE_ONLY_EXACT",
):
    return SimpleNamespace(
        signature=signature,
        pool_address=pool,
        base_residual_quote=base_residual,
        fee_separate_residual_quote=fee_residual,
        eligible=eligible,
        quote_eligible=quote_eligible,
        exclusion_reason=exclusion_reason,
        quote_exclusion_reason=quote_exclusion_reason,
        should_claim_fee=claim,
        evidence_class=evidence_class,
    )


def install_reports(monkeypatch, *, network_samples, semantics_samples):
    monkeypatch.setattr(
        "meteora_learner.rotation_transition_cost_envelope.build_quote_normalized_rotation_cost_report",
        lambda storage, *, position_address, max_quote_age_seconds: SimpleNamespace(
            position_address=position_address,
            quote_unit="ACCOUNT_QUOTE",
            samples=tuple(network_samples),
        ),
    )
    monkeypatch.setattr(
        "meteora_learner.rotation_transition_cost_envelope.build_rotation_fee_semantics_report",
        lambda storage, *, position_address, max_quote_age_seconds: SimpleNamespace(
            position_address=position_address,
            quote_unit="ACCOUNT_QUOTE",
            semantics_resolved=False,
            samples=tuple(semantics_samples),
        ),
    )


def test_transition_envelope_joins_network_fee_to_both_owner_flow_hypotheses(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_reports(
        monkeypatch,
        network_samples=(
            network_sample("sig-a", 2.0),
            network_sample("sig-b", 3.0),
        ),
        semantics_samples=(
            semantics_sample(
                "sig-a",
                base_residual=1.0,
                fee_residual=0.0,
            ),
            semantics_sample(
                "sig-b",
                base_residual=-2.0,
                fee_residual=1.0,
                evidence_class="NEITHER_HYPOTHESIS_EXACT",
            ),
        ),
    )

    report = build_rotation_transition_cost_envelope(
        storage,
        position_address="position",
    )

    assert report.pool_address == "pool"
    assert report.quote_unit == "ACCOUNT_QUOTE"
    assert report.signatures_seen == 2
    assert report.eligible_signatures == 2
    assert report.quote_coverage_rate == 1.0
    assert report.total_network_fee_quote == pytest.approx(5.0)
    assert report.total_base_hypothesis_value_drag_quote == pytest.approx(6.0)
    assert report.total_fee_separate_hypothesis_value_drag_quote == pytest.approx(4.0)
    assert report.value_drag_envelope_low_quote == pytest.approx(4.0)
    assert report.value_drag_envelope_high_quote == pytest.approx(6.0)
    assert report.semantics_resolved is False
    assert report.transition_cost_complete is False
    assert report.conclusion == "UNRESOLVED_TRANSITION_VALUE_DRAG_ENVELOPE"

    by_signature = {item.signature: item for item in report.samples}
    assert by_signature["sig-a"].base_hypothesis_value_drag_quote == pytest.approx(1.0)
    assert by_signature["sig-a"].fee_separate_hypothesis_value_drag_quote == pytest.approx(2.0)
    assert by_signature["sig-b"].base_hypothesis_value_drag_quote == pytest.approx(5.0)
    assert by_signature["sig-b"].fee_separate_hypothesis_value_drag_quote == pytest.approx(2.0)


def test_transition_envelope_keeps_unquoted_semantics_visible_but_excluded(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_reports(
        monkeypatch,
        network_samples=(network_sample("sig", 2.0),),
        semantics_samples=(
            semantics_sample(
                "sig",
                base_residual=None,
                fee_residual=None,
                quote_eligible=False,
                quote_exclusion_reason="token X quote stale at transaction time",
            ),
        ),
    )

    report = build_rotation_transition_cost_envelope(
        storage,
        position_address="position",
    )

    assert report.signatures_seen == 1
    assert report.eligible_signatures == 0
    assert report.quote_coverage_rate == 0.0
    assert report.total_network_fee_quote == 0.0
    assert report.samples[0].eligible is False
    assert report.samples[0].exclusion_reason == (
        "token X quote stale at transaction time"
    )


def test_transition_envelope_fails_closed_on_multiple_pool_identities(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_reports(
        monkeypatch,
        network_samples=(
            network_sample("sig-a", 1.0),
            network_sample("sig-b", 1.0),
        ),
        semantics_samples=(
            semantics_sample("sig-a", pool="pool-a"),
            semantics_sample("sig-b", pool="pool-b"),
        ),
    )

    with pytest.raises(ValueError, match="exactly one pool"):
        build_rotation_transition_cost_envelope(
            storage,
            position_address="position",
        )


def test_transition_envelope_persistence_stays_non_qualified(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_reports(
        monkeypatch,
        network_samples=(network_sample("sig", 2.0),),
        semantics_samples=(
            semantics_sample(
                "sig",
                base_residual=1.0,
                fee_residual=0.0,
            ),
        ),
    )
    report = build_rotation_transition_cost_envelope(
        storage,
        position_address="position",
    )

    evidence_id = persist_rotation_transition_cost_envelope(
        storage,
        report=report,
    )

    assert evidence_id > 0
    saved = storage.latest_advanced_edge_evidence(
        edge_type=ROTATION_TRANSITION_COST_ENVELOPE_EVIDENCE_TYPE,
        pool_address="pool",
    )
    assert saved is not None
    assert saved["qualified"] is False
    assert saved["status"] == "UNRESOLVED_TRANSITION_VALUE_DRAG_ENVELOPE"
    assert saved["evidence"]["transition_cost_complete"] is False

    with pytest.raises(ValueError, match="crossed research boundary"):
        persist_rotation_transition_cost_envelope(
            storage,
            report=replace(report, transition_cost_complete=True),
        )
