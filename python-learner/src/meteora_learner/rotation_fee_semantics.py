from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .research_store import ResearchStore
from .storage import Storage


@dataclass(frozen=True)
class RotationFeeSemanticsSample:
    signature: str
    position_address: str
    pool_address: str | None
    owner_address: str | None
    parent_ix_index: int
    succeeded: bool | None
    rebalance_events_in_transaction: int
    should_claim_fee: bool | None
    x_mint: str | None
    y_mint: str | None
    owner_x_delta: int | None
    owner_y_delta: int | None
    x_withdrawn_amount: int
    x_added_amount: int
    y_withdrawn_amount: int
    y_added_amount: int
    reported_x_fee_amount: int
    reported_y_fee_amount: int
    base_flow_x: int
    base_flow_y: int
    base_residual_x: int | None
    base_residual_y: int | None
    fee_separate_residual_x: int | None
    fee_separate_residual_y: int | None
    base_flow_exact: bool | None
    fee_separate_exact: bool | None
    evidence_class: str
    eligible: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class RotationFeeSemanticsReport:
    position_address: str
    transactions_seen: int
    eligible_transactions: int
    claim_fee_true_samples: int
    claim_fee_false_samples: int
    base_flow_only_matches: int
    fee_separate_only_matches: int
    both_hypotheses_match: int
    neither_hypothesis_matches: int
    semantics_resolved: bool
    conclusion: str
    samples: tuple[RotationFeeSemanticsSample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _owner_delta_by_mint(
    store: ResearchStore,
    *,
    signature: str,
    owner_address: str,
) -> tuple[dict[str, int], str | None]:
    rows = store.transaction_token_balance_deltas(
        signature,
        owner_address=owner_address,
    )
    grouped: dict[str, int] = {}
    for row in rows:
        pre_owner = (
            str(row["pre_owner"])
            if row.get("pre_owner") is not None
            else None
        )
        post_owner = (
            str(row["post_owner"])
            if row.get("post_owner") is not None
            else None
        )
        if (
            pre_owner is not None
            and post_owner is not None
            and pre_owner != post_owner
        ):
            return {}, (
                "token account owner changed during transaction"
            )
        grouped[str(row["mint"])] = (
            grouped.get(str(row["mint"]), 0)
            + int(str(row["delta_amount"]))
        )
    return grouped, None


def _ineligible_sample(
    *,
    event: dict[str, Any],
    succeeded: bool | None,
    rebalance_events_in_transaction: int,
    should_claim_fee: bool | None,
    reason: str,
) -> RotationFeeSemanticsSample:
    x_withdrawn = int(str(event["x_withdrawn_amount"]))
    x_added = int(str(event["x_added_amount"]))
    y_withdrawn = int(str(event["y_withdrawn_amount"]))
    y_added = int(str(event["y_added_amount"]))
    return RotationFeeSemanticsSample(
        signature=str(event["signature"]),
        position_address=str(event["position_address"]),
        pool_address=(
            str(event["lb_pair"])
            if event.get("lb_pair") is not None
            else None
        ),
        owner_address=(
            str(event["owner_address"])
            if event.get("owner_address") is not None
            else None
        ),
        parent_ix_index=int(event["parent_ix_index"]),
        succeeded=succeeded,
        rebalance_events_in_transaction=rebalance_events_in_transaction,
        should_claim_fee=should_claim_fee,
        x_mint=None,
        y_mint=None,
        owner_x_delta=None,
        owner_y_delta=None,
        x_withdrawn_amount=x_withdrawn,
        x_added_amount=x_added,
        y_withdrawn_amount=y_withdrawn,
        y_added_amount=y_added,
        reported_x_fee_amount=int(str(event["x_fee_amount"])),
        reported_y_fee_amount=int(str(event["y_fee_amount"])),
        base_flow_x=x_withdrawn - x_added,
        base_flow_y=y_withdrawn - y_added,
        base_residual_x=None,
        base_residual_y=None,
        fee_separate_residual_x=None,
        fee_separate_residual_y=None,
        base_flow_exact=None,
        fee_separate_exact=None,
        evidence_class="INELIGIBLE",
        eligible=False,
        exclusion_reason=reason,
    )


def build_rotation_fee_semantics_report(
    storage: Storage,
    *,
    position_address: str,
) -> RotationFeeSemanticsReport:
    """
    Compare two observable-flow hypotheses without assigning economic meaning.

    H0/base:
        owner token delta == withdrawn - added
    H1/fee-separate:
        owner token delta == withdrawn - added + reported fee

    Exact residuals are reported alongside the rebalance request's
    should_claim_fee flag. A match is observational evidence only: this
    function never labels x_fee_amount/y_fee_amount as income or cost.
    """
    if not position_address.strip():
        raise ValueError("position_address is required")

    store = ResearchStore(str(storage.path))
    events = store.load_position_transaction_events(
        position_address,
        event_type="Rebalancing",
    )
    if not events:
        raise ValueError(
            f"no decoded Rebalancing events for position {position_address}"
        )

    samples: list[RotationFeeSemanticsSample] = []
    for event in events:
        signature = str(event["signature"])
        receipt = store.transaction_snapshot(signature)
        succeeded = (
            bool(receipt["succeeded"])
            if receipt is not None
            and receipt.get("succeeded") is not None
            else None
        )
        event_count = store.transaction_event_count(
            signature,
            event_type="Rebalancing",
        )
        request = store.rebalance_request(
            signature,
            int(event["parent_ix_index"]),
        )
        should_claim_fee = (
            bool(request["should_claim_fee"])
            if request is not None
            else None
        )

        if receipt is None:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=None,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=should_claim_fee,
                    reason="transaction receipt missing",
                )
            )
            continue
        if succeeded is not True:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=succeeded,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=should_claim_fee,
                    reason="transaction is not confirmed successful",
                )
            )
            continue
        if event_count != 1:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=succeeded,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=should_claim_fee,
                    reason=(
                        "transaction contains multiple Rebalancing events; "
                        "owner token flow is not uniquely attributable"
                    ),
                )
            )
            continue
        if request is None:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=succeeded,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=None,
                    reason="matching rebalance request decode missing",
                )
            )
            continue

        pool_address = (
            str(event["lb_pair"])
            if event.get("lb_pair") is not None
            else ""
        )
        owner_address = (
            str(event["owner_address"])
            if event.get("owner_address") is not None
            else ""
        )
        if not pool_address:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=succeeded,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=should_claim_fee,
                    reason="rebalance event pool address missing",
                )
            )
            continue
        if not owner_address:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=succeeded,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=should_claim_fee,
                    reason="rebalance event owner address missing",
                )
            )
            continue

        try:
            identity = store.stable_pool_token_mints(pool_address)
        except ValueError as exc:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=succeeded,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=should_claim_fee,
                    reason=str(exc),
                )
            )
            continue
        if identity is None:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=succeeded,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=should_claim_fee,
                    reason="pool token mint identity missing",
                )
            )
            continue
        x_mint, y_mint = identity
        if x_mint == y_mint:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=succeeded,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=should_claim_fee,
                    reason="pool token X/Y mints are identical",
                )
            )
            continue

        owner_deltas, flow_error = _owner_delta_by_mint(
            store,
            signature=signature,
            owner_address=owner_address,
        )
        if flow_error is not None:
            samples.append(
                _ineligible_sample(
                    event=event,
                    succeeded=succeeded,
                    rebalance_events_in_transaction=event_count,
                    should_claim_fee=should_claim_fee,
                    reason=flow_error,
                )
            )
            continue

        owner_x = owner_deltas.get(x_mint, 0)
        owner_y = owner_deltas.get(y_mint, 0)
        x_withdrawn = int(str(event["x_withdrawn_amount"]))
        x_added = int(str(event["x_added_amount"]))
        y_withdrawn = int(str(event["y_withdrawn_amount"]))
        y_added = int(str(event["y_added_amount"]))
        fee_x = int(str(event["x_fee_amount"]))
        fee_y = int(str(event["y_fee_amount"]))
        base_x = x_withdrawn - x_added
        base_y = y_withdrawn - y_added
        base_residual_x = owner_x - base_x
        base_residual_y = owner_y - base_y
        fee_residual_x = owner_x - (base_x + fee_x)
        fee_residual_y = owner_y - (base_y + fee_y)
        base_exact = base_residual_x == 0 and base_residual_y == 0
        fee_exact = fee_residual_x == 0 and fee_residual_y == 0

        if base_exact and fee_exact:
            evidence_class = "BOTH_HYPOTHESES_EXACT"
        elif base_exact:
            evidence_class = "BASE_FLOW_ONLY_EXACT"
        elif fee_exact:
            evidence_class = "FEE_SEPARATE_ONLY_EXACT"
        else:
            evidence_class = "NEITHER_HYPOTHESIS_EXACT"

        samples.append(
            RotationFeeSemanticsSample(
                signature=signature,
                position_address=position_address,
                pool_address=pool_address,
                owner_address=owner_address,
                parent_ix_index=int(event["parent_ix_index"]),
                succeeded=succeeded,
                rebalance_events_in_transaction=event_count,
                should_claim_fee=should_claim_fee,
                x_mint=x_mint,
                y_mint=y_mint,
                owner_x_delta=owner_x,
                owner_y_delta=owner_y,
                x_withdrawn_amount=x_withdrawn,
                x_added_amount=x_added,
                y_withdrawn_amount=y_withdrawn,
                y_added_amount=y_added,
                reported_x_fee_amount=fee_x,
                reported_y_fee_amount=fee_y,
                base_flow_x=base_x,
                base_flow_y=base_y,
                base_residual_x=base_residual_x,
                base_residual_y=base_residual_y,
                fee_separate_residual_x=fee_residual_x,
                fee_separate_residual_y=fee_residual_y,
                base_flow_exact=base_exact,
                fee_separate_exact=fee_exact,
                evidence_class=evidence_class,
                eligible=True,
                exclusion_reason=None,
            )
        )

    eligible = [item for item in samples if item.eligible]
    return RotationFeeSemanticsReport(
        position_address=position_address,
        transactions_seen=len({
            item.signature for item in samples
        }),
        eligible_transactions=len(eligible),
        claim_fee_true_samples=sum(
            item.should_claim_fee is True for item in eligible
        ),
        claim_fee_false_samples=sum(
            item.should_claim_fee is False for item in eligible
        ),
        base_flow_only_matches=sum(
            item.evidence_class == "BASE_FLOW_ONLY_EXACT"
            for item in eligible
        ),
        fee_separate_only_matches=sum(
            item.evidence_class == "FEE_SEPARATE_ONLY_EXACT"
            for item in eligible
        ),
        both_hypotheses_match=sum(
            item.evidence_class == "BOTH_HYPOTHESES_EXACT"
            for item in eligible
        ),
        neither_hypothesis_matches=sum(
            item.evidence_class == "NEITHER_HYPOTHESIS_EXACT"
            for item in eligible
        ),
        semantics_resolved=False,
        conclusion="UNRESOLVED_OBSERVATIONAL_EVIDENCE",
        samples=tuple(samples),
    )
