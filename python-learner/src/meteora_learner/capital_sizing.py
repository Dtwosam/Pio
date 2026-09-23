from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


BPS = Decimal(10_000)


@dataclass(frozen=True)
class CapitalSizingConfig:
    max_position_bps: int = 1_000
    max_total_deployed_bps: int = 7_000
    min_cash_reserve_bps: int = 3_000
    soft_drawdown_bps: int = 500
    hard_drawdown_bps: int = 1_500
    soft_drawdown_size_multiplier_bps: int = 5_000
    min_position_quote: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "max_position_bps",
            "max_total_deployed_bps",
            "min_cash_reserve_bps",
            "soft_drawdown_bps",
            "hard_drawdown_bps",
            "soft_drawdown_size_multiplier_bps",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 10_000:
                raise ValueError(f"{name} must be between 0 and 10000")
        if self.max_total_deployed_bps + self.min_cash_reserve_bps > 10_000:
            raise ValueError(
                "max_total_deployed_bps + min_cash_reserve_bps cannot exceed 10000"
            )
        if self.soft_drawdown_bps > self.hard_drawdown_bps:
            raise ValueError("soft_drawdown_bps cannot exceed hard_drawdown_bps")
        if self.min_position_quote < 0:
            raise ValueError("min_position_quote cannot be negative")


@dataclass(frozen=True)
class CapitalSizingResult:
    account_equity_quote: float
    cash_quote: float
    current_deployed_quote: float
    portfolio_drawdown_bps: int
    requested_quote: float | None
    max_position_quote: float
    portfolio_room_quote: float
    reserve_room_quote: float
    drawdown_multiplier: float
    sized_quote: float
    blocked: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _d(value: float | int) -> Decimal:
    return Decimal(str(value))


def size_position(
    *,
    account_equity_quote: float,
    cash_quote: float,
    current_deployed_quote: float,
    portfolio_drawdown_bps: int,
    requested_quote: float | None = None,
    config: CapitalSizingConfig = CapitalSizingConfig(),
) -> CapitalSizingResult:
    """
    Deterministic capital cap for a proposed new LP position.

    The sizing engine never increases risk to hit a return target. It preserves
    the configured cash reserve, respects total/per-position exposure caps, and
    reduces size after the soft drawdown threshold.
    """
    if account_equity_quote <= 0:
        raise ValueError("account_equity_quote must be positive")
    if cash_quote < 0 or current_deployed_quote < 0:
        raise ValueError("cash_quote and current_deployed_quote cannot be negative")
    if portfolio_drawdown_bps < 0:
        raise ValueError("portfolio_drawdown_bps cannot be negative")
    if requested_quote is not None and requested_quote < 0:
        raise ValueError("requested_quote cannot be negative")

    equity = _d(account_equity_quote)
    cash = _d(cash_quote)
    deployed = _d(current_deployed_quote)

    max_position = equity * _d(config.max_position_bps) / BPS
    max_total_deployed = equity * _d(config.max_total_deployed_bps) / BPS
    reserve = equity * _d(config.min_cash_reserve_bps) / BPS

    portfolio_room = max(Decimal(0), max_total_deployed - deployed)
    reserve_room = max(Decimal(0), cash - reserve)

    reasons: list[str] = []
    if portfolio_drawdown_bps >= config.hard_drawdown_bps:
        multiplier = Decimal(0)
        reasons.append(
            f"drawdown {portfolio_drawdown_bps} bps reached hard limit "
            f"{config.hard_drawdown_bps} bps"
        )
    elif portfolio_drawdown_bps >= config.soft_drawdown_bps:
        multiplier = _d(config.soft_drawdown_size_multiplier_bps) / BPS
        reasons.append(
            f"drawdown throttle active at {portfolio_drawdown_bps} bps"
        )
    else:
        multiplier = Decimal(1)

    raw_cap = min(max_position, portfolio_room, reserve_room)
    sized = raw_cap * multiplier

    if requested_quote is not None:
        sized = min(sized, _d(requested_quote))

    if portfolio_room <= 0:
        reasons.append("total deployed-capital limit leaves no room")
    if reserve_room <= 0:
        reasons.append("cash reserve requirement leaves no room")

    min_position = _d(config.min_position_quote)
    if sized > 0 and sized < min_position:
        reasons.append(
            f"sized position {sized} is below minimum {min_position}"
        )
        sized = Decimal(0)

    blocked = sized <= 0
    if blocked and not reasons:
        reasons.append("sizing constraints produced zero deployable capital")

    return CapitalSizingResult(
        account_equity_quote=float(equity),
        cash_quote=float(cash),
        current_deployed_quote=float(deployed),
        portfolio_drawdown_bps=portfolio_drawdown_bps,
        requested_quote=requested_quote,
        max_position_quote=float(max_position),
        portfolio_room_quote=float(portfolio_room),
        reserve_room_quote=float(reserve_room),
        drawdown_multiplier=float(multiplier),
        sized_quote=float(sized),
        blocked=blocked,
        reasons=tuple(reasons),
    )
