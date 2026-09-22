from __future__ import annotations

from decimal import (
    Decimal,
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    getcontext,
)

getcontext().prec = 60

BASIS_POINT_MAX = Decimal(10_000)
INTEGER_SNAP_TOLERANCE = Decimal("1e-12")


def _base(bin_step: int) -> Decimal:
    if bin_step <= 0:
        raise ValueError("bin_step must be positive")
    return Decimal(1) + (Decimal(bin_step) / BASIS_POINT_MAX)


def bin_price(
    bin_id: int,
    bin_step: int,
    *,
    token_x_decimals: int | None = None,
    token_y_decimals: int | None = None,
) -> Decimal:
    """Meteora DLMM bin price. Optional decimals convert lamport price to token UI price."""
    price = _base(bin_step) ** int(bin_id)
    if token_x_decimals is not None or token_y_decimals is not None:
        if token_x_decimals is None or token_y_decimals is None:
            raise ValueError("both token decimal values are required")
        price *= Decimal(10) ** (token_x_decimals - token_y_decimals)
    return price


def price_to_bin_id(
    price: float | Decimal,
    bin_step: int,
    *,
    round_down: bool,
    token_x_decimals: int | None = None,
    token_y_decimals: int | None = None,
) -> int:
    value = Decimal(str(price))
    if value <= 0:
        raise ValueError("price must be positive")

    if token_x_decimals is not None or token_y_decimals is not None:
        if token_x_decimals is None or token_y_decimals is None:
            raise ValueError("both token decimal values are required")
        value /= Decimal(10) ** (token_x_decimals - token_y_decimals)

    raw = value.ln() / _base(bin_step).ln()
    nearest_integer = raw.to_integral_value(rounding=ROUND_HALF_EVEN)

    # Decimal ln can land infinitesimally to one side of an exact bin boundary.
    # Snap only when it is extremely close to an integer so exact bin prices
    # round-trip without changing the intended floor/ceiling behavior elsewhere.
    if abs(raw - nearest_integer) <= INTEGER_SNAP_TOLERANCE:
        return int(nearest_integer)

    rounding = ROUND_FLOOR if round_down else ROUND_CEILING
    return int(raw.to_integral_value(rounding=rounding))


def range_prices(
    min_bin_id: int,
    max_bin_id: int,
    bin_step: int,
    *,
    token_x_decimals: int | None = None,
    token_y_decimals: int | None = None,
) -> tuple[Decimal, Decimal]:
    if min_bin_id > max_bin_id:
        raise ValueError("min_bin_id cannot exceed max_bin_id")
    return (
        bin_price(
            min_bin_id,
            bin_step,
            token_x_decimals=token_x_decimals,
            token_y_decimals=token_y_decimals,
        ),
        bin_price(
            max_bin_id,
            bin_step,
            token_x_decimals=token_x_decimals,
            token_y_decimals=token_y_decimals,
        ),
    )
