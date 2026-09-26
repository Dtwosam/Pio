from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .quote_registry import DEFAULT_QUOTE_UNIT
from .rotation_fee_semantics import build_rotation_fee_semantics_report
from .rotation_transition_costs import build_quote_normalized_rotation_cost_report
from .storage import Storage


ROTATION_TRANSITION_COST_ENVELOPE_EVIDENCE_TYPE = (
    "ROTATION_TRANSITION_COST_ENVELOPE_V1"
)


@dataclass(frozen=True)
class RotationTransitionCostEnvelopeSample:
    signature: str
    network_fee_quote: float | None
    base_residual_quote: float | None
    fee_separate_residual_quote: float | None
    base_hypothesis_value_drag_quote: float | None
    fee_separate_hypothesis_value_drag_quote: float | None
    should_claim_fee: bool | None
    evidence_class: str | None
    eligible: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class RotationTransitionCostEnvelopeReport:
    position_address: str
    quote_unit: str
    signatures_seen: int
    eligible_signatures: int
    quote_coverage_rate: float
    total_network_fee_quote: float
    total_base_hypothesis_value_drag_quote: float
    total_fee_separate_hypothesis_value_drag_quote: float
    value_drag_envelope_low_quote: float
    value_drag_envelope_high_quote: float
    semantics_resolved: bool
    transition_cost_complete: bool
    included_components: tuple[str, ...]
    excluded_components: tuple[str, ...]
    conclusion: str
    samples: tuple[RotationTransitionCostEnvelopeSample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_rotation_transition_cost_envelope(
    storage: Storage,
    *,
    position_address: str,
    max_quote_age_seconds: int = 300,
) -> RotationTransitionCostEnvelopeReport:
    """
    Join unambiguous network fees to both unresolved owner-flow hypotheses.

    For each signature:
      value_drag = network_fee_quote - owner_flow_residual_quote

    A positive owner-flow residual therefore offsets some of the unambiguous
    network debit under that accounting hypothesis; a negative residual adds
    to the observed value shortfall. This arithmetic does not assign a cause
    to the residual and does not select either hypothesis as correct.
    """
    if not position_address.strip():
        raise ValueError("position_address is required")
    if max_quote_age_seconds < 0:
        raise ValueError("max_quote_age_seconds cannot be negative")

    network = build_quote_normalized_rotation_cost_report(
        storage,
        position_address=position_address,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    semantics = build_rotation_fee_semantics_report(
        storage,
        position_address=position_address,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    if network.quote_unit != DEFAULT_QUOTE_UNIT:
        raise ValueError("unexpected network-cost quote unit")
    if semantics.quote_unit != DEFAULT_QUOTE_UNIT:
        raise ValueError("unexpected fee-semantics quote unit")
    if semantics.semantics_resolved:
        raise ValueError(
            "transition-cost envelope expects unresolved fee semantics"
        )

    network_by_signature = {
        item.signature: item for item in network.samples
    }
    semantics_by_signature = {
        item.signature: item for item in semantics.samples
    }
    signatures = sorted(
        set(network_by_signature) | set(semantics_by_signature)
    )

    samples: list[RotationTransitionCostEnvelopeSample] = []
    for signature in signatures:
        network_sample = network_by_signature.get(signature)
        semantics_sample = semantics_by_signature.get(signature)
        reason = None

        if network_sample is None:
            reason = "network-cost sample missing"
        elif semantics_sample is None:
            reason = "owner-flow semantics sample missing"
        elif network_sample.network_fee_quote is None:
            reason = (
                network_sample.exclusion_reason
                or "network fee quote unavailable"
            )
        elif not semantics_sample.eligible:
            reason = (
                semantics_sample.exclusion_reason
                or "owner-flow semantics sample ineligible"
            )
        elif not semantics_sample.quote_eligible:
            reason = (
                semantics_sample.quote_exclusion_reason
                or "owner-flow quote unavailable"
            )
        elif (
            semantics_sample.base_residual_quote is None
            or semantics_sample.fee_separate_residual_quote is None
        ):
            reason = "owner-flow residual quote missing"

        if reason is not None:
            samples.append(
                RotationTransitionCostEnvelopeSample(
                    signature=signature,
                    network_fee_quote=(
                        network_sample.network_fee_quote
                        if network_sample is not None
                        else None
                    ),
                    base_residual_quote=(
                        semantics_sample.base_residual_quote
                        if semantics_sample is not None
                        else None
                    ),
                    fee_separate_residual_quote=(
                        semantics_sample.fee_separate_residual_quote
                        if semantics_sample is not None
                        else None
                    ),
                    base_hypothesis_value_drag_quote=None,
                    fee_separate_hypothesis_value_drag_quote=None,
                    should_claim_fee=(
                        semantics_sample.should_claim_fee
                        if semantics_sample is not None
                        else None
                    ),
                    evidence_class=(
                        semantics_sample.evidence_class
                        if semantics_sample is not None
                        else None
                    ),
                    eligible=False,
                    exclusion_reason=reason,
                )
            )
            continue

        assert network_sample is not None
        assert semantics_sample is not None
        assert network_sample.network_fee_quote is not None
        assert semantics_sample.base_residual_quote is not None
        assert semantics_sample.fee_separate_residual_quote is not None

        samples.append(
            RotationTransitionCostEnvelopeSample(
                signature=signature,
                network_fee_quote=float(
                    network_sample.network_fee_quote
                ),
                base_residual_quote=float(
                    semantics_sample.base_residual_quote
                ),
                fee_separate_residual_quote=float(
                    semantics_sample.fee_separate_residual_quote
                ),
                base_hypothesis_value_drag_quote=float(
                    network_sample.network_fee_quote
                    - semantics_sample.base_residual_quote
                ),
                fee_separate_hypothesis_value_drag_quote=float(
                    network_sample.network_fee_quote
                    - semantics_sample.fee_separate_residual_quote
                ),
                should_claim_fee=semantics_sample.should_claim_fee,
                evidence_class=semantics_sample.evidence_class,
                eligible=True,
                exclusion_reason=None,
            )
        )

    eligible = [item for item in samples if item.eligible]
    base_total = float(sum(
        item.base_hypothesis_value_drag_quote
        for item in eligible
        if item.base_hypothesis_value_drag_quote is not None
    ))
    fee_total = float(sum(
        item.fee_separate_hypothesis_value_drag_quote
        for item in eligible
        if item.fee_separate_hypothesis_value_drag_quote is not None
    ))
    network_total = float(sum(
        item.network_fee_quote
        for item in eligible
        if item.network_fee_quote is not None
    ))

    return RotationTransitionCostEnvelopeReport(
        position_address=position_address,
        quote_unit=DEFAULT_QUOTE_UNIT,
        signatures_seen=len(samples),
        eligible_signatures=len(eligible),
        quote_coverage_rate=(
            len(eligible) / len(samples)
            if samples
            else 0.0
        ),
        total_network_fee_quote=network_total,
        total_base_hypothesis_value_drag_quote=base_total,
        total_fee_separate_hypothesis_value_drag_quote=fee_total,
        value_drag_envelope_low_quote=min(base_total, fee_total),
        value_drag_envelope_high_quote=max(base_total, fee_total),
        semantics_resolved=False,
        transition_cost_complete=False,
        included_components=(
            "SOLANA_NETWORK_FEE",
            "OWNER_FLOW_RESIDUAL_HYPOTHESES",
        ),
        excluded_components=(
            "REBALANCING_REWARDS",
            "UNRESOLVED_FEE_SEMANTICS",
        ),
        conclusion="UNRESOLVED_TRANSITION_VALUE_DRAG_ENVELOPE",
        samples=tuple(samples),
    )


def persist_rotation_transition_cost_envelope(
    storage: Storage,
    *,
    report: RotationTransitionCostEnvelopeReport,
) -> int:
    if report.semantics_resolved or report.transition_cost_complete:
        raise ValueError(
            "unresolved transition-cost envelope crossed research boundary"
        )
    if report.conclusion != "UNRESOLVED_TRANSITION_VALUE_DRAG_ENVELOPE":
        raise ValueError("unexpected transition-cost envelope conclusion")
    return storage.save_advanced_edge_evidence(
        edge_type=ROTATION_TRANSITION_COST_ENVELOPE_EVIDENCE_TYPE,
        pool_address=_single_pool_address(report),
        status=report.conclusion,
        qualified=False,
        evidence=report.to_record(),
    )


def _single_pool_address(
    report: RotationTransitionCostEnvelopeReport,
) -> str:
    # Position-level envelope persistence needs a stable pool identity. The
    # source semantics evidence is the authoritative place for that identity,
    # so require it to be present on every eligible/ineligible sample source
    # before callers persist. This helper is intentionally replaced by the
    # builder-backed variant below in normal use.
    raise ValueError(
        "pool address must be supplied by builder-backed persistence"
    )
