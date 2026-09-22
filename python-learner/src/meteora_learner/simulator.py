from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from .dlmm_math import bin_price, price_ratio_to_bin_delta, price_to_bin_id, relative_bin_price
from .strategy import StrategyType, strategy_weights

SIMULATOR_FIDELITY = "DISCRETE_COMPLETED_BIN_V1"


@dataclass
class BinInventory:
    bin_id: int
    price: float
    x_amount: float = 0.0
    y_amount: float = 0.0


@dataclass
class PositionState:
    min_bin_id: int
    max_bin_id: int
    bin_step: int
    token_x_decimals: int
    token_y_decimals: int
    strategy: StrategyType
    entry_active_id: int
    current_active_id: int
    entry_price_anchor: float | None
    initial_x_amount: float
    initial_y_amount: float
    idle_x_amount: float
    idle_y_amount: float
    bins: dict[int, BinInventory] = field(default_factory=dict)
    accrued_fee_quote: float = 0.0
    entry_cost_quote: float = 0.0
    rebalance_cost_quote: float = 0.0
    exit_cost_quote: float = 0.0


@dataclass(frozen=True)
class SimulationResult:
    simulator_fidelity: str
    initial_value_quote: float
    final_inventory_value_quote: float
    hold_value_quote: float
    fees_quote: float
    costs_quote: float
    net_value_quote: float
    net_pnl_quote: float
    net_return_pct: float
    impermanent_loss_quote: float
    impermanent_loss_pct: float
    ending_x_amount: float
    ending_y_amount: float
    ending_active_id: int
    close_in_range_ratio: float
    crossed_bin_conversions: int
    fee_model: str


def _price(bin_id: int, state: PositionState) -> float:
    if state.entry_price_anchor is not None:
        return float(
            relative_bin_price(
                state.entry_price_anchor,
                bin_id - state.entry_active_id,
                state.bin_step,
            )
        )

    value: Decimal = bin_price(
        bin_id,
        state.bin_step,
        token_x_decimals=state.token_x_decimals,
        token_y_decimals=state.token_y_decimals,
    )
    return float(value)


def _active_id_for_price(state: PositionState, price: float) -> int:
    if state.entry_price_anchor is not None:
        delta = price_ratio_to_bin_delta(
            price,
            state.entry_price_anchor,
            state.bin_step,
            round_down=True,
        )
        return state.entry_active_id + delta

    return price_to_bin_id(
        price,
        state.bin_step,
        round_down=True,
        token_x_decimals=state.token_x_decimals,
        token_y_decimals=state.token_y_decimals,
    )


def create_position(
    *,
    min_bin_id: int,
    max_bin_id: int,
    active_id: int,
    bin_step: int,
    amount_x: float,
    amount_y: float,
    strategy: StrategyType | str = StrategyType.SPOT,
    token_x_decimals: int = 0,
    token_y_decimals: int = 0,
    entry_price: float | None = None,
    favor_x_in_active_bin: bool = False,
    entry_cost_quote: float = 0.0,
) -> PositionState:
    if min_bin_id > max_bin_id:
        raise ValueError("min_bin_id cannot exceed max_bin_id")
    if amount_x < 0 or amount_y < 0:
        raise ValueError("token amounts cannot be negative")
    if entry_price is not None and entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if entry_cost_quote < 0:
        raise ValueError("entry_cost_quote cannot be negative")

    strategy = StrategyType(strategy)
    state = PositionState(
        min_bin_id=min_bin_id,
        max_bin_id=max_bin_id,
        bin_step=bin_step,
        token_x_decimals=token_x_decimals,
        token_y_decimals=token_y_decimals,
        strategy=strategy,
        entry_active_id=active_id,
        current_active_id=active_id,
        entry_price_anchor=float(entry_price) if entry_price is not None else None,
        initial_x_amount=float(amount_x),
        initial_y_amount=float(amount_y),
        idle_x_amount=0.0,
        idle_y_amount=0.0,
        entry_cost_quote=float(entry_cost_quote),
    )

    weights = strategy_weights(min_bin_id, max_bin_id, active_id, strategy)
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

    for bin_id in range(min_bin_id, max_bin_id + 1):
        state.bins[bin_id] = BinInventory(bin_id=bin_id, price=_price(bin_id, state))

    if y_bins and amount_y > 0:
        denominator = sum(weights[bin_id] for bin_id in y_bins)
        for bin_id in y_bins:
            state.bins[bin_id].y_amount = amount_y * weights[bin_id] / denominator
    else:
        state.idle_y_amount = float(amount_y)

    if x_bins and amount_x > 0:
        weighted = {bin_id: weights[bin_id] / state.bins[bin_id].price for bin_id in x_bins}
        denominator = sum(weighted.values())
        for bin_id in x_bins:
            state.bins[bin_id].x_amount = amount_x * weighted[bin_id] / denominator
    else:
        state.idle_x_amount = float(amount_x)

    return state


def _synchronize_completed_bins(state: PositionState, new_active_id: int) -> int:
    """
    V1 approximation:
    bins that are no longer active are treated as fully converted at their bin price.
    The current active bin remains untouched because OHLC alone cannot reveal partial fill.
    """
    conversions = 0
    for bin_id, inventory in state.bins.items():
        if bin_id < new_active_id and inventory.x_amount > 0:
            inventory.y_amount += inventory.x_amount * inventory.price
            inventory.x_amount = 0.0
            conversions += 1
        elif bin_id > new_active_id and inventory.y_amount > 0:
            inventory.x_amount += inventory.y_amount / inventory.price
            inventory.y_amount = 0.0
            conversions += 1

    state.current_active_id = new_active_id
    return conversions


def token_totals(state: PositionState) -> tuple[float, float]:
    x = state.idle_x_amount + sum(item.x_amount for item in state.bins.values())
    y = state.idle_y_amount + sum(item.y_amount for item in state.bins.values())
    return x, y


def mark_value_quote(state: PositionState, mark_price: float) -> float:
    if mark_price <= 0:
        raise ValueError("mark_price must be positive")
    x, y = token_totals(state)
    return y + x * mark_price


def simulate_price_path(
    state: PositionState,
    prices: Sequence[float],
    *,
    attributable_fee_quote_by_step: Sequence[float] | None = None,
    rebalance_cost_quote: float = 0.0,
    exit_cost_quote: float = 0.0,
) -> SimulationResult:
    if not prices:
        raise ValueError("prices cannot be empty")
    if any(price <= 0 for price in prices):
        raise ValueError("all prices must be positive")
    if rebalance_cost_quote < 0 or exit_cost_quote < 0:
        raise ValueError("costs cannot be negative")
    if attributable_fee_quote_by_step is not None and len(attributable_fee_quote_by_step) != len(prices):
        raise ValueError("fee sequence length must match prices")

    entry_price = float(prices[0])
    initial_value = state.initial_y_amount + state.initial_x_amount * entry_price
    crossed_conversions = 0
    in_range = 0

    for index, price in enumerate(prices):
        active_id = _active_id_for_price(state, float(price))
        crossed_conversions += _synchronize_completed_bins(state, active_id)

        if state.min_bin_id <= active_id <= state.max_bin_id:
            in_range += 1
            if attributable_fee_quote_by_step is not None:
                fee = float(attributable_fee_quote_by_step[index])
                if fee < 0:
                    raise ValueError("fees cannot be negative")
                state.accrued_fee_quote += fee

    state.rebalance_cost_quote += rebalance_cost_quote
    state.exit_cost_quote += exit_cost_quote

    final_price = float(prices[-1])
    inventory_value = mark_value_quote(state, final_price)
    hold_value = state.initial_y_amount + state.initial_x_amount * final_price
    costs = state.entry_cost_quote + state.rebalance_cost_quote + state.exit_cost_quote
    net_value = inventory_value + state.accrued_fee_quote - costs
    net_pnl = net_value - initial_value
    il_quote = inventory_value - hold_value
    il_pct = (il_quote / hold_value * 100.0) if hold_value else 0.0
    x, y = token_totals(state)

    return SimulationResult(
        simulator_fidelity=SIMULATOR_FIDELITY,
        initial_value_quote=initial_value,
        final_inventory_value_quote=inventory_value,
        hold_value_quote=hold_value,
        fees_quote=state.accrued_fee_quote,
        costs_quote=costs,
        net_value_quote=net_value,
        net_pnl_quote=net_pnl,
        net_return_pct=(net_pnl / initial_value * 100.0) if initial_value else 0.0,
        impermanent_loss_quote=il_quote,
        impermanent_loss_pct=il_pct,
        ending_x_amount=x,
        ending_y_amount=y,
        ending_active_id=state.current_active_id,
        close_in_range_ratio=in_range / len(prices),
        crossed_bin_conversions=crossed_conversions,
        fee_model="EXOGENOUS_POSITION_ATTRIBUTABLE" if attributable_fee_quote_by_step is not None else "ZERO",
    )
