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


def _round_log_bin(raw: Decimal, *, round_down: bool) -> int:
    nearest_integer = raw.to_integral_value(rounding=ROUND_HALF_EVEN)
    if abs(raw - nearest_integer) <= INTEGER_SNAP_TOLERANCE:
        return int(nearest_integer)
    rounding = ROUND_FLOOR if round_down else ROUND_CEILING
    return int(raw.to_integral_value(rounding=rounding))


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


def relative_bin_price(
    reference_price: float | Decimal,
    delta_bins: int,
    bin_step: int,
) -> Decimal:
    """Move a known UI price by a DLMM bin delta without needing token decimals."""
    value = Decimal(str(reference_price))
    if value <= 0:
        raise ValueError("reference_price must be positive")
    return value * (_base(bin_step) ** int(delta_bins))


def price_ratio_to_bin_delta(
    price: float | Decimal,
    reference_price: float | Decimal,
    bin_step: int,
    *,
    round_down: bool,
) -> int:
    value = Decimal(str(price))
    reference = Decimal(str(reference_price))
    if value <= 0 or reference <= 0:
        raise ValueError("prices must be positive")
    raw = (value / reference).ln() / _base(bin_step).ln()
    return _round_log_bin(raw, round_down=round_down)


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
    return _round_log_bin(raw, round_down=round_down)


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
