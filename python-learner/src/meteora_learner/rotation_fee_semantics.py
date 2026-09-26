from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .quote_registry import DEFAULT_QUOTE_UNIT, token_quote_status
from .research_store import ResearchStore
from .storage import Storage


ROTATION_FEE_SEMANTICS_EVIDENCE_TYPE = (
    "ROTATION_FEE_SEMANTICS_OBSERVATION_V1"
)


@dataclass(frozen=True)
class RotationFeeSemanticsSample:
    signature: str
    position_address: str
    pool_address: str | None
    owner_address: str | None
    parent_ix_index: int
    block_time: int | None
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
    x_quote_per_atomic: float | None
    x_quote_source: str | None
    x_quote_observed_at: str | None
    x_quote_age_seconds: int | None
    y_quote_per_atomic: float | None
    y_quote_source: str | None
    y_quote_observed_at: str | None
    y_quote_age_seconds: int | None
    base_residual_quote: float | None
    fee_separate_residual_quote: float | None
    quote_eligible: bool
    quote_exclusion_reason: str | None
    base_flow_exact: bool | None
    fee_separate_exact: bool | None
    evidence_class: str
    eligible: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class RotationFeeSemanticsReport:
    position_address: str
    quote_unit: str
    transactions_seen: int
    eligible_transactions: int
    quote_eligible_transactions: int
    quote_coverage_rate: float
    claim_fee_true_samples: int
    claim_fee_false_samples: int
    base_flow_only_matches: int
    fee_separate_only_matches: int
    both_hypotheses_match: int
    neither_hypothesis_matches: int
    quoted_base_residual_net: float
    quoted_fee_separate_residual_net: float
    semantics_resolved: bool
    conclusion: str
    samples: tuple[RotationFeeSemanticsSample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _block_time_iso(block_time: int) -> str:
    return datetime.fromtimestamp(
        block_time,
        tz=timezone.utc,
    ).isoformat()


def _quote_status_for_mint(
    storage: Storage,
    *,
    mint: str,
    block_time: int | None,
    max_quote_age_seconds: int,
):
    if block_time is None:
        return None
    return token_quote_status(
        storage,
        token_mint=mint,
        max_age_seconds=max_quote_age_seconds,
        as_of=_block_time_iso(block_time),
    )


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
        block_time=(
            int(event["block_time"])
            if event.get("block_time") is not None
            else None
        ),
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
        x_quote_per_atomic=None,
        x_quote_source=None,
        x_quote_observed_at=None,
        x_quote_age_seconds=None,
        y_quote_per_atomic=None,
        y_quote_source=None,
        y_quote_observed_at=None,
        y_quote_age_seconds=None,
        base_residual_quote=None,
        fee_separate_residual_quote=None,
        quote_eligible=False,
        quote_exclusion_reason=reason,
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
    max_quote_age_seconds: int = 300,
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
    if max_quote_age_seconds < 0:
        raise ValueError("max_quote_age_seconds cannot be negative")

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

        block_time = (
            int(event["block_time"])
            if event.get("block_time") is not None
            else None
        )
        x_quote = _quote_status_for_mint(
            storage,
            mint=x_mint,
            block_time=block_time,
            max_quote_age_seconds=max_quote_age_seconds,
        )
        y_quote = _quote_status_for_mint(
            storage,
            mint=y_mint,
            block_time=block_time,
            max_quote_age_seconds=max_quote_age_seconds,
        )
        quote_reason = None
        if block_time is None:
            quote_reason = "transaction block time missing"
        elif x_quote is None or not x_quote.available:
            quote_reason = "token X quote missing at transaction time"
        elif not x_quote.fresh or x_quote.quote_per_atomic is None:
            quote_reason = "token X quote stale at transaction time"
        elif y_quote is None or not y_quote.available:
            quote_reason = "token Y quote missing at transaction time"
        elif not y_quote.fresh or y_quote.quote_per_atomic is None:
            quote_reason = "token Y quote stale at transaction time"

        quote_eligible = quote_reason is None
        base_residual_quote = None
        fee_residual_quote = None
        if quote_eligible:
            assert x_quote is not None
            assert y_quote is not None
            assert x_quote.quote_per_atomic is not None
            assert y_quote.quote_per_atomic is not None
            base_residual_quote = (
                base_residual_x * x_quote.quote_per_atomic
                + base_residual_y * y_quote.quote_per_atomic
            )
            fee_residual_quote = (
                fee_residual_x * x_quote.quote_per_atomic
                + fee_residual_y * y_quote.quote_per_atomic
            )

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
                block_time=block_time,
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
                x_quote_per_atomic=(
                    x_quote.quote_per_atomic if x_quote is not None else None
                ),
                x_quote_source=(
                    x_quote.source if x_quote is not None else None
                ),
                x_quote_observed_at=(
                    x_quote.observed_at if x_quote is not None else None
                ),
                x_quote_age_seconds=(
                    x_quote.age_seconds if x_quote is not None else None
                ),
                y_quote_per_atomic=(
                    y_quote.quote_per_atomic if y_quote is not None else None
                ),
                y_quote_source=(
                    y_quote.source if y_quote is not None else None
                ),
                y_quote_observed_at=(
                    y_quote.observed_at if y_quote is not None else None
                ),
                y_quote_age_seconds=(
                    y_quote.age_seconds if y_quote is not None else None
                ),
                base_residual_quote=base_residual_quote,
                fee_separate_residual_quote=fee_residual_quote,
                quote_eligible=quote_eligible,
                quote_exclusion_reason=quote_reason,
                base_flow_exact=base_exact,
                fee_separate_exact=fee_exact,
                evidence_class=evidence_class,
                eligible=True,
                exclusion_reason=None,
            )
        )

    eligible = [item for item in samples if item.eligible]
    quote_eligible = [
        item for item in eligible if item.quote_eligible
    ]
    return RotationFeeSemanticsReport(
        position_address=position_address,
        quote_unit=DEFAULT_QUOTE_UNIT,
        transactions_seen=len({
            item.signature for item in samples
        }),
        eligible_transactions=len(eligible),
        quote_eligible_transactions=len(quote_eligible),
        quote_coverage_rate=(
            len(quote_eligible) / len(eligible)
            if eligible
            else 0.0
        ),
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
        quoted_base_residual_net=float(sum(
            item.base_residual_quote
            for item in quote_eligible
            if item.base_residual_quote is not None
        )),
        quoted_fee_separate_residual_net=float(sum(
            item.fee_separate_residual_quote
            for item in quote_eligible
            if item.fee_separate_residual_quote is not None
        )),
        semantics_resolved=False,
        conclusion="UNRESOLVED_OBSERVATIONAL_EVIDENCE",
        samples=tuple(samples),
    )



def persist_rotation_fee_semantics_report(
    storage: Storage,
    *,
    report: RotationFeeSemanticsReport,
) -> int:
    if report.semantics_resolved:
        raise ValueError(
            "observational fee-semantics report cannot self-resolve semantics"
        )
    if report.conclusion != "UNRESOLVED_OBSERVATIONAL_EVIDENCE":
        raise ValueError(
            "unexpected fee-semantics conclusion for observational evidence"
        )
    pool_addresses = {
        str(item.pool_address)
        for item in report.samples
        if item.pool_address
    }
    if len(pool_addresses) != 1:
        raise ValueError(
            "fee-semantics evidence must resolve to exactly one pool"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=ROTATION_FEE_SEMANTICS_EVIDENCE_TYPE,
        pool_address=next(iter(pool_addresses)),
        status=report.conclusion,
        qualified=False,
        evidence=report.to_record(),
    )
