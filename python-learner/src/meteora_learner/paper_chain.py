from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from .baseline_policy import BaselineProposal, q64_value_in_y_atomic
from .paper_account import paper_position_snapshot, rebalance_paper_position
from .paper_chain_valuation import (
    apply_paper_chain_valuation,
    initialize_paper_counterfactual,
    prepare_paper_chain_valuation,
)
from .position_policy import PositionManagementConfig
from .research_store import ResearchStore
from .storage import Storage


BPS = Decimal("10000")


def _d(value: float | int | str | Decimal) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("value must be finite")
    return result


@dataclass(frozen=True)
class CounterfactualBinding:
    position_id: str
    pool_address: str
    entry_observed_at: str
    amount_x: int
    amount_y: int
    max_share_bps: int
    favor_x_active: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperChainBinding:
    position_id: str
    pool_address: str
    opened_observed_at: str
    anchor_observed_at: str
    amount_x: int
    amount_y: int
    min_bin_id: int
    max_bin_id: int
    strategy: str
    max_share_bps: int
    favor_x_active: bool
    last_observed_at: str | None
    last_fee_x: int
    last_fee_y: int
    status: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperChainValuation:
    position_id: str
    pool_address: str
    anchor_observed_at: str
    observed_at: str
    active_bin_id: int
    current_price_q64: int
    principal_x: int
    principal_y: int
    principal_value_y_atomic: int
    mark_quote: float
    cumulative_fee_x: int
    cumulative_fee_y: int
    fee_delta_x: int
    fee_delta_y: int
    fee_delta_value_y_atomic: int
    fee_delta_quote: float
    reward_one: int
    reward_two: int
    rewards_valued: bool
    max_observed_share_bps: int
    replay_fidelity: str
    quote_fidelity: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperChainObservationResult:
    valuation: PaperChainValuation
    executed_action: str
    detail: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _base_row(storage: Storage, position_id: str) -> dict[str, Any]:
    with storage.connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            """
            SELECT *
            FROM paper_counterfactual_positions
            WHERE position_id = ?
            LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"paper position has no chain binding: {position_id}")
    return dict(row)


def _last_applied(storage: Storage, position_id: str) -> tuple[str | None, int, int]:
    with storage.connect() as conn:
        last = conn.execute(
            """
            SELECT observed_at
            FROM paper_chain_valuations
            WHERE position_id = ? AND status = 'APPLIED'
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            (position_id,),
        ).fetchone()
        totals = conn.execute(
            """
            SELECT COALESCE(SUM(CAST(fee_x_atomic AS INTEGER)), 0),
                   COALESCE(SUM(CAST(fee_y_atomic AS INTEGER)), 0)
            FROM paper_chain_valuations
            WHERE position_id = ? AND status = 'APPLIED'
            """,
            (position_id,),
        ).fetchone()
    return (
        str(last[0]) if last is not None else None,
        int(totals[0] or 0),
        int(totals[1] or 0),
    )


def counterfactual_binding(
    storage: Storage,
    *,
    position_id: str,
) -> CounterfactualBinding:
    base = _base_row(storage, position_id)
    return CounterfactualBinding(
        position_id=position_id,
        pool_address=str(base["pool_address"]),
        entry_observed_at=str(base["entry_observed_at"]),
        amount_x=int(str(base["amount_x_atomic"])),
        amount_y=int(str(base["amount_y_atomic"])),
        max_share_bps=int(base["max_share_bps"]),
        favor_x_active=bool(base["favor_x_active"]),
    )


def paper_chain_binding(
    storage: Storage,
    *,
    position_id: str,
) -> PaperChainBinding:
    base = _base_row(storage, position_id)
    position = paper_position_snapshot(storage, position_id=position_id)
    last_at, fee_x, fee_y = _last_applied(storage, position_id)
    return PaperChainBinding(
        position_id=position_id,
        pool_address=str(base["pool_address"]),
        opened_observed_at=str(base["entry_observed_at"]),
        anchor_observed_at=str(base["entry_observed_at"]),
        amount_x=int(str(base["amount_x_atomic"])),
        amount_y=int(str(base["amount_y_atomic"])),
        min_bin_id=position.min_bin_id,
        max_bin_id=position.max_bin_id,
        strategy=position.strategy,
        max_share_bps=int(base["max_share_bps"]),
        favor_x_active=bool(base["favor_x_active"]),
        last_observed_at=last_at,
        last_fee_x=fee_x,
        last_fee_y=fee_y,
        status="ACTIVE" if position.status == "OPEN" else "CLOSED",
    )


def bind_paper_position_to_chain(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    amount_x: int,
    amount_y: int,
    token_y_quote_per_atomic: float,
    max_notional_error_bps: int = 50,
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> PaperChainBinding:
    if amount_x < 0 or amount_y < 0 or amount_x + amount_y <= 0:
        raise ValueError("at least one non-negative atomic token amount is required")
    rate = _d(token_y_quote_per_atomic)
    if rate <= 0:
        raise ValueError("token_y_quote_per_atomic must be positive")
    if not 0 <= max_notional_error_bps <= 10_000:
        raise ValueError("max_notional_error_bps must be between 0 and 10000")

    position = paper_position_snapshot(storage, position_id=position_id)
    store = ResearchStore(storage.path)
    pool = store.chain_pool_snapshot_at(position.pool_address, observed_at)
    if pool is None:
        raise ValueError("chain pool snapshot is missing at binding observation")
    active_id = int(pool["active_bin_id"])
    active = store.bin_liquidity_at(
        position.pool_address,
        observed_at=observed_at,
        bin_id=active_id,
    )
    if active is None:
        raise ValueError("active-bin price is missing at binding observation")
    price_q64 = int(str(active["price"]))
    initial_y_atomic = q64_value_in_y_atomic(
        amount_x=amount_x,
        amount_y=amount_y,
        price_q64=price_q64,
    )
    target = _d(position.entry_capital_quote)
    implied = _d(initial_y_atomic) * rate
    error_bps = int(abs(implied - target) * BPS / target)
    if error_bps > max_notional_error_bps:
        raise ValueError(
            f"atomic binding notional error {error_bps} bps exceeds "
            f"{max_notional_error_bps} bps"
        )

    proposal = BaselineProposal(
        strategy=position.strategy,
        half_width=(position.max_bin_id - position.min_bin_id) // 2,
        center_offset=0,
        decision_active_bin_id=active_id,
        min_bin_id=position.min_bin_id,
        max_bin_id=position.max_bin_id,
    )
    synthetic_plan = SimpleNamespace(
        pool_address=position.pool_address,
        amount_x=amount_x,
        amount_y=amount_y,
        decision_observed_at=observed_at,
        max_share_bps=max_share_bps,
        favor_x_in_active_bin=favor_x_in_active_bin,
        entry_gate=SimpleNamespace(proposal=proposal),
    )
    initialize_paper_counterfactual(
        storage,
        position_id=position_id,
        plan=synthetic_plan,
    )
    return paper_chain_binding(storage, position_id=position_id)


def _legacy_valuation(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    token_y_quote_per_atomic: float,
) -> PaperChainValuation:
    value = prepare_paper_chain_valuation(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        token_y_quote_per_atomic=token_y_quote_per_atomic,
    )
    binding = paper_chain_binding(storage, position_id=position_id)
    store = ResearchStore(storage.path)
    active = store.bin_liquidity_at(
        binding.pool_address,
        observed_at=observed_at,
        bin_id=value.active_bin_id,
    )
    if active is None:
        raise ValueError("active-bin price is missing at chain observation")
    price_q64 = int(str(active["price"]))

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT valuation_json, status
            FROM paper_chain_valuations
            WHERE position_id = ? AND observed_at = ?
            """,
            (position_id, observed_at),
        ).fetchone()
        prior = conn.execute(
            """
            SELECT COALESCE(SUM(CAST(fee_x_atomic AS INTEGER)), 0),
                   COALESCE(SUM(CAST(fee_y_atomic AS INTEGER)), 0)
            FROM paper_chain_valuations
            WHERE position_id = ? AND status = 'APPLIED'
              AND observed_at < ?
            """,
            (position_id, observed_at),
        ).fetchone()
    meta = __import__("json").loads(str(row[0]))
    cumulative_x = int(prior[0] or 0) + value.fee_x_atomic
    cumulative_y = int(prior[1] or 0) + value.fee_y_atomic
    return PaperChainValuation(
        position_id=position_id,
        pool_address=binding.pool_address,
        anchor_observed_at=binding.anchor_observed_at,
        observed_at=observed_at,
        active_bin_id=value.active_bin_id,
        current_price_q64=price_q64,
        principal_x=value.inventory_x_atomic,
        principal_y=value.inventory_y_atomic,
        principal_value_y_atomic=int(meta["mark_y_atomic"]),
        mark_quote=value.mark_quote,
        cumulative_fee_x=cumulative_x,
        cumulative_fee_y=cumulative_y,
        fee_delta_x=value.fee_x_atomic,
        fee_delta_y=value.fee_y_atomic,
        fee_delta_value_y_atomic=int(meta["fee_value_y_atomic"]),
        fee_delta_quote=value.fee_delta_quote,
        reward_one=value.reward_one_atomic,
        reward_two=value.reward_two_atomic,
        rewards_valued=True,
        max_observed_share_bps=value.max_observed_share_bps,
        replay_fidelity="COUNTERFACTUAL_INCREMENTAL_CHAIN_V1",
        quote_fidelity=value.quote_fidelity,
    )


def value_paper_position_from_chain(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    token_y_quote_per_atomic: float,
) -> PaperChainValuation:
    return _legacy_valuation(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        token_y_quote_per_atomic=token_y_quote_per_atomic,
    )


def prepare_chain_valuation(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    token_y_quote_per_atomic: float,
) -> PaperChainValuation:
    return value_paper_position_from_chain(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        token_y_quote_per_atomic=token_y_quote_per_atomic,
    )


def apply_chain_paper_observation(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    token_y_quote_per_atomic: float,
    pool_safe: bool,
    estimated_exit_cost_quote: float = 0.0,
    rebalance_cost_quote: float | None = None,
    emergency_exit: bool = False,
    config: PositionManagementConfig = PositionManagementConfig(),
) -> PaperChainObservationResult:
    valuation = _legacy_valuation(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        token_y_quote_per_atomic=token_y_quote_per_atomic,
    )
    binding = paper_chain_binding(storage, position_id=position_id)
    times = ResearchStore(storage.path).chain_observation_times(
        binding.pool_address,
        limit=None,
        ascending=True,
    )
    covered = [
        item
        for item in times
        if binding.opened_observed_at <= item <= observed_at
    ]
    if not covered or covered[-1] != observed_at:
        raise ValueError("target paper observation is missing from chain history")

    applied = apply_paper_chain_valuation(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        holding_observations=len(covered),
        token_y_quote_per_atomic=token_y_quote_per_atomic,
        pool_safe=pool_safe,
        emergency_exit=emergency_exit,
        estimated_exit_cost_quote=estimated_exit_cost_quote,
        rebalance_cost_quote=rebalance_cost_quote,
        config=config,
    )

    return PaperChainObservationResult(
        valuation=valuation,
        executed_action=applied.executed_action,
        detail=applied.to_record(),
    )



def apply_prepared_chain_valuation(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    token_y_quote_per_atomic: float,
    pool_safe: bool,
    estimated_exit_cost_quote: float = 0.0,
    rebalance_cost_quote: float | None = None,
    emergency_exit: bool = False,
    config: PositionManagementConfig = PositionManagementConfig(),
) -> PaperChainObservationResult:
    return apply_chain_paper_observation(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        token_y_quote_per_atomic=token_y_quote_per_atomic,
        pool_safe=pool_safe,
        estimated_exit_cost_quote=estimated_exit_cost_quote,
        rebalance_cost_quote=rebalance_cost_quote,
        emergency_exit=emergency_exit,
        config=config,
    )
