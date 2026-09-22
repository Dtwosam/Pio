from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping, Sequence

from .liquidity_math import mint_liquidity_share
from .strategy import StrategyType, deposit_weights


@dataclass(frozen=True)
class AtomicBinDeposit:
    bin_id: int
    amount_x: int
    amount_y: int


@dataclass(frozen=True)
class AtomicDepositPlan:
    bins: tuple[AtomicBinDeposit, ...]
    requested_x: int
    requested_y: int
    deposited_x: int
    deposited_y: int
    idle_x: int
    idle_y: int
    transfer_fee_model: str = "NONE_STANDARD_SPL"


@dataclass(frozen=True)
class ProjectedBinShare:
    bin_id: int
    amount_x: int
    amount_y: int
    price_q64: int
    existing_liquidity_supply: int
    liquidity_share_minted: int


@dataclass(frozen=True)
class ProjectedDepositShares:
    bins: tuple[ProjectedBinShare, ...]
    total_liquidity_share_minted: int
    empty_bin_initializations: int
    fidelity: str


def _allocate_by_integer_weights(total: int, weights: Mapping[int, int]) -> dict[int, int]:
    if total < 0:
        raise ValueError("total cannot be negative")
    if not weights:
        return {}
    denominator = sum(weights.values())
    if denominator <= 0:
        raise ValueError("weight sum must be positive")
    return {
        bin_id: total * int(weight) // denominator
        for bin_id, weight in weights.items()
    }


def _allocate_x_by_weight_over_price(
    total: int,
    weights: Mapping[int, int],
    prices_q64: Mapping[int, int],
) -> dict[int, int]:
    if total < 0:
        raise ValueError("total cannot be negative")
    if not weights:
        return {}

    components: dict[int, Fraction] = {}
    for bin_id, weight in weights.items():
        price = int(prices_q64[bin_id])
        if price <= 0:
            raise ValueError(f"missing/invalid Q64 price for bin {bin_id}")
        components[bin_id] = Fraction(int(weight), price)

    denominator = sum(components.values(), Fraction(0, 1))
    if denominator <= 0:
        raise ValueError("ask-side weighted price denominator must be positive")

    return {
        bin_id: int(Fraction(total, 1) * component / denominator)
        for bin_id, component in components.items()
    }


def distribute_standard_spl_deposit(
    *,
    active_id: int,
    min_bin_id: int,
    max_bin_id: int,
    amount_x: int,
    amount_y: int,
    strategy: StrategyType | str,
    prices_q64: Mapping[int, int],
    favor_x_in_active_bin: bool = False,
) -> AtomicDepositPlan:
    """
    Mirror Meteora toAmountsBothSideByStrategy for standard SPL tokens.

    Transfer-fee tokens are intentionally excluded because the official SDK
    applies mint/epoch-specific transfer fee adjustments before distribution.
    """
    if min_bin_id > max_bin_id:
        raise ValueError("min_bin_id cannot exceed max_bin_id")
    if amount_x < 0 or amount_y < 0:
        raise ValueError("amounts cannot be negative")

    weights = deposit_weights(
        min_bin_id,
        max_bin_id,
        active_id,
        strategy,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )

    y_bins = [
        bin_id
        for bin_id in range(min_bin_id, max_bin_id + 1)
        if bin_id < active_id or (bin_id == active_id and not favor_x_in_active_bin)
    ]
    x_bins = [
        bin_id
        for bin_id in range(min_bin_id, max_bin_id + 1)
        if bin_id > active_id or (bin_id == active_id and favor_x_in_active_bin)
    ]

    y_alloc = _allocate_by_integer_weights(
        amount_y,
        {bin_id: weights[bin_id] for bin_id in y_bins},
    )
    x_alloc = _allocate_x_by_weight_over_price(
        amount_x,
        {bin_id: weights[bin_id] for bin_id in x_bins},
        prices_q64,
    )

    bins = tuple(
        AtomicBinDeposit(
            bin_id=bin_id,
            amount_x=x_alloc.get(bin_id, 0),
            amount_y=y_alloc.get(bin_id, 0),
        )
        for bin_id in range(min_bin_id, max_bin_id + 1)
        if x_alloc.get(bin_id, 0) or y_alloc.get(bin_id, 0)
    )
    deposited_x = sum(item.amount_x for item in bins)
    deposited_y = sum(item.amount_y for item in bins)

    return AtomicDepositPlan(
        bins=bins,
        requested_x=amount_x,
        requested_y=amount_y,
        deposited_x=deposited_x,
        deposited_y=deposited_y,
        idle_x=amount_x - deposited_x,
        idle_y=amount_y - deposited_y,
    )


def project_deposit_shares(
    plan: AtomicDepositPlan,
    chain_bins: Sequence[dict[str, object]],
) -> ProjectedDepositShares:
    chain_by_id = {int(row["bin_id"]): row for row in chain_bins}
    projected: list[ProjectedBinShare] = []
    empty = 0

    for item in plan.bins:
        row = chain_by_id.get(item.bin_id)
        if row is None:
            raise ValueError(f"chain snapshot does not cover bin {item.bin_id}")

        price_q64 = int(str(row["price"]))
        supply = int(str(row["liquidity_supply"]))
        bin_x = int(str(row["amount_x"]))
        bin_y = int(str(row["amount_y"]))

        share = mint_liquidity_share(
            amount_x=item.amount_x,
            amount_y=item.amount_y,
            price_q64=price_q64,
            bin_amount_x=bin_x,
            bin_amount_y=bin_y,
            liquidity_supply=supply,
        )
        if supply == 0 and share > 0:
            empty += 1

        projected.append(
            ProjectedBinShare(
                bin_id=item.bin_id,
                amount_x=item.amount_x,
                amount_y=item.amount_y,
                price_q64=price_q64,
                existing_liquidity_supply=supply,
                liquidity_share_minted=share,
            )
        )

    fidelity = (
        "SDK_EXISTING_BIN_SHARE_V1"
        if empty == 0
        else "SDK_EXISTING_PLUS_INVARIANT_EMPTY_BIN_V1"
    )
    return ProjectedDepositShares(
        bins=tuple(projected),
        total_liquidity_share_minted=sum(
            item.liquidity_share_minted for item in projected
        ),
        empty_bin_initializations=empty,
        fidelity=fidelity,
    )
