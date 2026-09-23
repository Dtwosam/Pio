from __future__ import annotations

from dataclasses import dataclass

from .liquidity_math import amounts_from_liquidity_share, mint_liquidity_share


FEE_PRECISION = 1_000_000_000
BASIS_POINT_MAX = 10_000


@dataclass(frozen=True)
class CompositionFeeResult:
    liquidity_share: int
    amount_x_into_bin: int
    amount_y_into_bin: int
    credited_amount_x: int
    credited_amount_y: int
    composition_fee_x: int
    composition_fee_y: int
    protocol_fee_x: int
    protocol_fee_y: int
    lp_fee_x: int
    lp_fee_y: int
    fidelity: str


def composition_fee_amount(delta_amount: int, total_fee_rate: int) -> int:
    """
    Meteora composition fee formula.

    fee = floor(
        delta * rate * (FEE_PRECISION + rate) / FEE_PRECISION^2
    )
    """
    if delta_amount < 0:
        raise ValueError("delta_amount cannot be negative")
    if not 0 <= total_fee_rate <= 100_000_000:
        raise ValueError("total_fee_rate must be between 0 and MAX_FEE_RATE")
    return (
        delta_amount
        * total_fee_rate
        * (FEE_PRECISION + total_fee_rate)
        // (FEE_PRECISION * FEE_PRECISION)
    )


def protocol_fee_amount(composition_fee: int, protocol_share_bps: int) -> int:
    if composition_fee < 0:
        raise ValueError("composition_fee cannot be negative")
    if not 0 <= protocol_share_bps <= BASIS_POINT_MAX:
        raise ValueError("protocol_share_bps must be between 0 and 10000")
    return composition_fee * protocol_share_bps // BASIS_POINT_MAX


def simulate_active_bin_composition_fee(
    *,
    amount_x: int,
    amount_y: int,
    price_q64: int,
    bin_amount_x: int,
    bin_amount_y: int,
    liquidity_supply: int,
    total_fee_rate: int,
    protocol_share_bps: int,
) -> CompositionFeeResult:
    """
    Simulate the active-bin composition fee for a standard liquidity deposit.

    The target token amounts are the new position share's pro-rata claim on the
    updated bin, matching the SDK rebalance helper's intent and the program's
    withdrawal pro-rata invariant.

    No composition fee applies to an empty bin.
    """
    share = mint_liquidity_share(
        amount_x=amount_x,
        amount_y=amount_y,
        price_q64=price_q64,
        bin_amount_x=bin_amount_x,
        bin_amount_y=bin_amount_y,
        liquidity_supply=liquidity_supply,
    )

    if liquidity_supply == 0 or share == 0:
        return CompositionFeeResult(
            liquidity_share=share,
            amount_x_into_bin=amount_x,
            amount_y_into_bin=amount_y,
            credited_amount_x=amount_x,
            credited_amount_y=amount_y,
            composition_fee_x=0,
            composition_fee_y=0,
            protocol_fee_x=0,
            protocol_fee_y=0,
            lp_fee_x=0,
            lp_fee_y=0,
            fidelity="METEORA_COMPOSITION_FEE_FORMULA_V1",
        )

    post_supply = liquidity_supply + share
    post_x = bin_amount_x + amount_x
    post_y = bin_amount_y + amount_y

    amount_x_into_bin, amount_y_into_bin = amounts_from_liquidity_share(
        liquidity_share=share,
        bin_amount_x=post_x,
        bin_amount_y=post_y,
        liquidity_supply=post_supply,
    )

    fee_x = 0
    fee_y = 0

    # SDK computeCompositionFee:
    # if the pro-rata target needs more X than deposited, the deposit's excess Y
    # behaves like a Y->X swap and the composition fee is charged in Y.
    if amount_x_into_bin > amount_x:
        delta_y = max(0, amount_y - amount_y_into_bin)
        fee_y = composition_fee_amount(delta_y, total_fee_rate)

    # Symmetric path: target needs more Y than deposited, so excess X is the
    # swap-like amount and fee is charged in X.
    if amount_y_into_bin > amount_y:
        delta_x = max(0, amount_x - amount_x_into_bin)
        fee_x = composition_fee_amount(delta_x, total_fee_rate)

    protocol_x = protocol_fee_amount(fee_x, protocol_share_bps)
    protocol_y = protocol_fee_amount(fee_y, protocol_share_bps)

    return CompositionFeeResult(
        liquidity_share=share,
        amount_x_into_bin=amount_x_into_bin,
        amount_y_into_bin=amount_y_into_bin,
        credited_amount_x=amount_x_into_bin - fee_x,
        credited_amount_y=amount_y_into_bin - fee_y,
        composition_fee_x=fee_x,
        composition_fee_y=fee_y,
        protocol_fee_x=protocol_x,
        protocol_fee_y=protocol_y,
        lp_fee_x=fee_x - protocol_x,
        lp_fee_y=fee_y - protocol_y,
        fidelity="METEORA_COMPOSITION_FEE_FORMULA_V1",
    )
