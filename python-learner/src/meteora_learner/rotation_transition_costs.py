from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from .quote_registry import DEFAULT_QUOTE_UNIT, token_quote_status
from .research_store import ResearchStore
from .storage import Storage


WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass(frozen=True)
class RotationOwnerTokenFlow:
    mint: str
    account_count: int
    atomic_delta: int
    attribution_eligible: bool
    quote_per_atomic: float | None
    quote_value: float | None
    quote_source: str | None
    quote_observed_at: str | None
    quote_age_seconds: int | None
    quote_eligible: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class RotationTransitionCostSample:
    signature: str
    block_time: int | None
    events_in_transaction: int
    old_min_id: int
    old_max_id: int
    new_min_id: int
    new_max_id: int
    succeeded: bool | None
    network_fee_lamports: int | None
    network_fee_quote: float | None
    sol_quote_per_atomic: float | None
    sol_quote_source: str | None
    sol_quote_observed_at: str | None
    sol_quote_age_seconds: int | None
    quote_eligible: bool
    exclusion_reason: str | None
    reported_x_fee_amount: int
    reported_y_fee_amount: int
    reported_reward_one: int
    reported_reward_two: int
    owner_address: str | None
    owner_token_flows: tuple[RotationOwnerTokenFlow, ...]


@dataclass(frozen=True)
class RotationTransitionCostReport:
    position_address: str
    quote_unit: str
    rotation_transactions: int
    quote_eligible_transactions: int
    quote_coverage_rate: float
    total_network_fee_lamports: int
    total_network_fee_quote: float
    mean_network_fee_quote: float | None
    owner_token_flow_mints: int
    owner_token_flow_quote_eligible_mints: int
    owner_token_flow_quote_coverage_rate: float | None
    quoted_owner_token_flow_net: float
    cost_components_complete: bool
    included_cost_components: tuple[str, ...]
    excluded_unclassified_components: tuple[str, ...]
    samples: tuple[RotationTransitionCostSample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _block_time_iso(block_time: int) -> str:
    return datetime.fromtimestamp(
        block_time,
        tz=timezone.utc,
    ).isoformat()


def _owner_token_flows(
    storage: Storage,
    store: ResearchStore,
    *,
    signature: str,
    owner_address: str | None,
    block_time: int | None,
    max_quote_age_seconds: int,
) -> tuple[RotationOwnerTokenFlow, ...]:
    if owner_address is None:
        return ()

    rows = store.transaction_token_balance_deltas(
        signature,
        owner_address=owner_address,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["mint"]), []).append(row)

    flows: list[RotationOwnerTokenFlow] = []
    for mint in sorted(grouped):
        mint_rows = grouped[mint]
        atomic_delta = sum(
            int(str(row["delta_amount"])) for row in mint_rows
        )
        ambiguous_owner_change = any(
            row.get("pre_owner") is not None
            and row.get("post_owner") is not None
            and str(row["pre_owner"]) != str(row["post_owner"])
            for row in mint_rows
        )
        attribution_eligible = not ambiguous_owner_change

        quote_per_atomic = None
        quote_value = None
        quote_source = None
        quote_observed_at = None
        quote_age = None
        reason = None

        if not attribution_eligible:
            reason = "token account owner changed during transaction"
        elif block_time is None:
            reason = "transaction block time missing"
        else:
            status = token_quote_status(
                storage,
                token_mint=mint,
                max_age_seconds=max_quote_age_seconds,
                as_of=_block_time_iso(block_time),
            )
            quote_per_atomic = status.quote_per_atomic
            quote_source = status.source
            quote_observed_at = status.observed_at
            quote_age = status.age_seconds
            if not status.available:
                reason = "token quote missing at transaction time"
            elif not status.fresh or status.quote_per_atomic is None:
                reason = "token quote stale at transaction time"
            else:
                quote_value = atomic_delta * status.quote_per_atomic

        flows.append(
            RotationOwnerTokenFlow(
                mint=mint,
                account_count=len(mint_rows),
                atomic_delta=atomic_delta,
                attribution_eligible=attribution_eligible,
                quote_per_atomic=quote_per_atomic,
                quote_value=quote_value,
                quote_source=quote_source,
                quote_observed_at=quote_observed_at,
                quote_age_seconds=quote_age,
                quote_eligible=quote_value is not None,
                exclusion_reason=reason,
            )
        )
    return tuple(flows)


def build_quote_normalized_rotation_cost_report(
    storage: Storage,
    *,
    position_address: str,
    max_quote_age_seconds: int = 300,
) -> RotationTransitionCostReport:
    """
    Quote-normalize the part of a rebalance transition cost that is unambiguous.

    The only component counted as transition cost is the Solana transaction
    fee. Owner token-account deltas are captured and quote-normalized separately
    as signed observed flows; they are not assigned an economic meaning here.
    Rebalancing event x_fee_amount/y_fee_amount and reward fields remain visible
    but excluded from cost totals until their direction is explicitly validated.
    Network fees are deduplicated by transaction signature.
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

    by_signature: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_signature.setdefault(str(event["signature"]), []).append(event)

    samples: list[RotationTransitionCostSample] = []
    for signature in sorted(
        by_signature,
        key=lambda value: (
            min(
                int(item["block_time"])
                for item in by_signature[value]
                if item.get("block_time") is not None
            )
            if any(
                item.get("block_time") is not None
                for item in by_signature[value]
            )
            else 0,
            value,
        ),
    ):
        grouped = by_signature[signature]
        first = grouped[0]
        receipt = store.transaction_snapshot(signature)

        block_time = (
            int(first["block_time"])
            if first.get("block_time") is not None
            else None
        )
        network_fee = (
            int(receipt["network_fee_lamports"])
            if receipt is not None
            and receipt.get("network_fee_lamports") is not None
            else None
        )
        succeeded = (
            bool(receipt["succeeded"])
            if receipt is not None
            and receipt.get("succeeded") is not None
            else None
        )

        quote_value = None
        quote_per_atomic = None
        quote_source = None
        quote_observed_at = None
        quote_age = None
        reason = None

        if receipt is None:
            reason = "transaction receipt missing"
        elif succeeded is not True:
            reason = "transaction is not confirmed successful"
        elif network_fee is None:
            reason = "network fee missing from receipt"
        elif block_time is None:
            reason = "transaction block time missing"
        else:
            status = token_quote_status(
                storage,
                token_mint=WRAPPED_SOL_MINT,
                max_age_seconds=max_quote_age_seconds,
                as_of=_block_time_iso(block_time),
            )
            quote_per_atomic = status.quote_per_atomic
            quote_source = status.source
            quote_observed_at = status.observed_at
            quote_age = status.age_seconds
            if not status.available:
                reason = "SOL quote missing at transaction time"
            elif not status.fresh or status.quote_per_atomic is None:
                reason = "SOL quote stale at transaction time"
            else:
                quote_value = network_fee * status.quote_per_atomic

        owner_address = (
            str(first["owner_address"])
            if first.get("owner_address") is not None
            else None
        )
        owner_token_flows = _owner_token_flows(
            storage,
            store,
            signature=signature,
            owner_address=owner_address,
            block_time=block_time,
            max_quote_age_seconds=max_quote_age_seconds,
        )

        samples.append(
            RotationTransitionCostSample(
                signature=signature,
                block_time=block_time,
                events_in_transaction=len(grouped),
                old_min_id=int(first["old_min_id"]),
                old_max_id=int(first["old_max_id"]),
                new_min_id=int(first["new_min_id"]),
                new_max_id=int(first["new_max_id"]),
                succeeded=succeeded,
                network_fee_lamports=network_fee,
                network_fee_quote=quote_value,
                sol_quote_per_atomic=quote_per_atomic,
                sol_quote_source=quote_source,
                sol_quote_observed_at=quote_observed_at,
                sol_quote_age_seconds=quote_age,
                quote_eligible=quote_value is not None,
                exclusion_reason=reason,
                reported_x_fee_amount=sum(
                    int(str(item["x_fee_amount"])) for item in grouped
                ),
                reported_y_fee_amount=sum(
                    int(str(item["y_fee_amount"])) for item in grouped
                ),
                reported_reward_one=sum(
                    int(str(item["reward_one"])) for item in grouped
                ),
                reported_reward_two=sum(
                    int(str(item["reward_two"])) for item in grouped
                ),
                owner_address=owner_address,
                owner_token_flows=owner_token_flows,
            )
        )

    quoted = [
        item.network_fee_quote
        for item in samples
        if item.network_fee_quote is not None
    ]
    fee_lamports = [
        item.network_fee_lamports
        for item in samples
        if item.network_fee_lamports is not None
    ]
    owner_flows = [
        flow
        for sample in samples
        for flow in sample.owner_token_flows
    ]
    quoted_owner_flows = [
        flow.quote_value
        for flow in owner_flows
        if flow.quote_value is not None
    ]
    owner_flow_coverage = (
        len(quoted_owner_flows) / len(owner_flows)
        if owner_flows
        else None
    )
    return RotationTransitionCostReport(
        position_address=position_address,
        quote_unit=DEFAULT_QUOTE_UNIT,
        rotation_transactions=len(samples),
        quote_eligible_transactions=len(quoted),
        quote_coverage_rate=len(quoted) / len(samples),
        total_network_fee_lamports=sum(fee_lamports),
        total_network_fee_quote=float(sum(quoted)),
        mean_network_fee_quote=(
            float(mean(quoted)) if quoted else None
        ),
        owner_token_flow_mints=len(owner_flows),
        owner_token_flow_quote_eligible_mints=len(quoted_owner_flows),
        owner_token_flow_quote_coverage_rate=owner_flow_coverage,
        quoted_owner_token_flow_net=float(sum(quoted_owner_flows)),
        cost_components_complete=False,
        included_cost_components=("SOLANA_NETWORK_FEE",),
        excluded_unclassified_components=(
            "REBALANCING_X_FEE_AMOUNT",
            "REBALANCING_Y_FEE_AMOUNT",
            "REBALANCING_REWARD_ONE",
            "REBALANCING_REWARD_TWO",
        ),
        samples=tuple(samples),
    )
