from __future__ import annotations

from dataclasses import dataclass


SCALE_OFFSET = 64
Q64 = 1 << SCALE_OFFSET


@dataclass(frozen=True)
class DepositProjection:
    amount_x: int
    amount_y: int
    price_q64: int
    liquidity_in: int
    liquidity_share_minted: int
    post_amount_x: int
    post_amount_y: int
    post_liquidity_supply: int


def q64_liquidity(amount_x: int, amount_y: int, price_q64: int) -> int:
    """
    Meteora rebalance simulator liquidity primitive:
        L = price_q64 * X + (Y << 64)
    """
    if amount_x < 0 or amount_y < 0:
        raise ValueError("token amounts cannot be negative")
    if price_q64 <= 0:
        raise ValueError("price_q64 must be positive")
    return price_q64 * amount_x + (amount_y << SCALE_OFFSET)


def mint_liquidity_share(
    *,
    amount_x: int,
    amount_y: int,
    price_q64: int,
    bin_amount_x: int,
    bin_amount_y: int,
    liquidity_supply: int,
) -> int:
    """
    Project the Q64.64 liquidity share minted by a deposit.

    For an initialized bin, this mirrors Meteora's official rebalance simulator:
        share = deposit_liquidity * bin_supply / bin_liquidity

    For an empty bin, Meteora's current source-backed formula is:
        share = deposit_liquidity

    Both branches use integer round-down semantics.
    """
    if min(bin_amount_x, bin_amount_y, liquidity_supply) < 0:
        raise ValueError("bin state cannot be negative")

    deposit_liquidity = q64_liquidity(amount_x, amount_y, price_q64)
    if deposit_liquidity == 0:
        return 0

    bin_liquidity = q64_liquidity(bin_amount_x, bin_amount_y, price_q64)

    if liquidity_supply == 0:
        if bin_liquidity != 0:
            raise ValueError("non-zero bin liquidity with zero liquidity supply")
        return deposit_liquidity

    if bin_liquidity == 0:
        raise ValueError("non-zero liquidity supply with zero bin liquidity")

    return deposit_liquidity * liquidity_supply // bin_liquidity


def project_deposit(
    *,
    amount_x: int,
    amount_y: int,
    price_q64: int,
    bin_amount_x: int,
    bin_amount_y: int,
    liquidity_supply: int,
) -> DepositProjection:
    share = mint_liquidity_share(
        amount_x=amount_x,
        amount_y=amount_y,
        price_q64=price_q64,
        bin_amount_x=bin_amount_x,
        bin_amount_y=bin_amount_y,
        liquidity_supply=liquidity_supply,
    )
    return DepositProjection(
        amount_x=amount_x,
        amount_y=amount_y,
        price_q64=price_q64,
        liquidity_in=q64_liquidity(amount_x, amount_y, price_q64),
        liquidity_share_minted=share,
        post_amount_x=bin_amount_x + amount_x,
        post_amount_y=bin_amount_y + amount_y,
        post_liquidity_supply=liquidity_supply + share,
    )


def amounts_from_liquidity_share(
    *,
    liquidity_share: int,
    bin_amount_x: int,
    bin_amount_y: int,
    liquidity_supply: int,
) -> tuple[int, int]:
    """Mirror Meteora Bin::calculate_out_amount with round-down semantics."""
    if min(liquidity_share, bin_amount_x, bin_amount_y, liquidity_supply) < 0:
        raise ValueError("values cannot be negative")
    if liquidity_supply == 0 or liquidity_share == 0:
        return 0, 0
    return (
        liquidity_share * bin_amount_x // liquidity_supply,
        liquidity_share * bin_amount_y // liquidity_supply,
    )


def fee_from_checkpoint_delta(
    *,
    liquidity_share: int,
    fee_per_token_delta: int,
) -> int:
    """
    Mirror DynamicPosition fee accrual for one bin, excluding pre-existing pending fees.

    scaled_share = liquidity_share >> 64
    fee = (scaled_share * fee_per_token_delta) >> 64
    """
    if liquidity_share < 0 or fee_per_token_delta < 0:
        raise ValueError("fee inputs cannot be negative")
    scaled_share = liquidity_share >> SCALE_OFFSET
    return (scaled_share * fee_per_token_delta) >> SCALE_OFFSET
