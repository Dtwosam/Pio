from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from typing import Any

from .baseline_policy import q64_value_in_y_atomic
from .chain_replay import replay_small_lp_history
from .paper_account import paper_position_snapshot
from .paper_cycle import apply_paper_observation
from .position_policy import PositionManagementConfig
from .research_store import ResearchStore
from .storage import Storage
from .strategy import StrategyType


BPS = Decimal("10000")


def _d(value: float | int | str | Decimal) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("value must be finite")
    return result


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


def _binding_from_row(row: Any) -> PaperChainBinding:
    return PaperChainBinding(
        position_id=str(row[0]),
        pool_address=str(row[1]),
        opened_observed_at=str(row[2]),
        anchor_observed_at=str(row[3]),
        amount_x=int(str(row[4])),
        amount_y=int(str(row[5])),
        min_bin_id=int(row[6]),
        max_bin_id=int(row[7]),
        strategy=str(row[8]),
        max_share_bps=int(row[9]),
        favor_x_active=bool(row[10]),
        last_observed_at=str(row[11]) if row[11] is not None else None,
        last_fee_x=int(str(row[12])),
        last_fee_y=int(str(row[13])),
        status=str(row[14]),
    )


def paper_chain_binding(
    storage: Storage,
    *,
    position_id: str,
) -> PaperChainBinding:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT position_id, pool_address, opened_observed_at,
                   anchor_observed_at, amount_x, amount_y,
                   min_bin_id, max_bin_id, strategy, max_share_bps,
                   favor_x_active, last_observed_at,
                   last_fee_x, last_fee_y, status
            FROM paper_chain_bindings
            WHERE position_id = ?
            LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"paper position has no chain binding: {position_id}")
    return _binding_from_row(row)


def _active_price(
    store: ResearchStore,
    *,
    pool_address: str,
    observed_at: str,
    active_bin_id: int,
) -> int:
    row = store.bin_liquidity_at(
        pool_address,
        observed_at=observed_at,
        bin_id=active_bin_id,
    )
    if row is None:
        raise ValueError("active-bin price is missing at chain observation")
    price = int(str(row["price"]))
    if price <= 0:
        raise ValueError("active-bin price must be positive")
    return price


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
    if token_y_quote_per_atomic <= 0:
        raise ValueError("token_y_quote_per_atomic must be positive")
    if not 0 <= max_notional_error_bps <= 10_000:
        raise ValueError("max_notional_error_bps must be between 0 and 10000")
    if not 1 <= max_share_bps <= 10_000:
        raise ValueError("max_share_bps must be between 1 and 10000")

    position = paper_position_snapshot(storage, position_id=position_id)
    if position.status != "OPEN":
        raise ValueError("paper position must be open")

    store = ResearchStore(storage.path)
    pool = store.chain_pool_snapshot_at(position.pool_address, observed_at)
    if pool is None:
        raise ValueError("chain pool snapshot is missing at binding observation")
    active_id = int(pool["active_bin_id"])
    price_q64 = _active_price(
        store,
        pool_address=position.pool_address,
        observed_at=observed_at,
        active_bin_id=active_id,
    )
    initial_y_atomic = q64_value_in_y_atomic(
        amount_x=amount_x,
        amount_y=amount_y,
        price_q64=price_q64,
    )
    implied_quote = _d(initial_y_atomic) * _d(token_y_quote_per_atomic)
    target_quote = _d(position.entry_capital_quote)
    if target_quote <= 0:
        raise ValueError("paper entry capital must be positive")
    error_bps = int(abs(implied_quote - target_quote) * BPS / target_quote)
    if error_bps > max_notional_error_bps:
        raise ValueError(
            f"atomic binding notional error {error_bps} bps exceeds "
            f"{max_notional_error_bps} bps"
        )

    with storage.connect() as conn:
        if conn.execute(
            "SELECT 1 FROM paper_chain_bindings WHERE position_id = ?",
            (position_id,),
        ).fetchone() is not None:
            raise ValueError(f"paper chain binding already exists: {position_id}")
        conn.execute(
            """
            INSERT INTO paper_chain_bindings(
                position_id, pool_address, opened_observed_at,
                anchor_observed_at, amount_x, amount_y,
                min_bin_id, max_bin_id, strategy, max_share_bps,
                favor_x_active, status, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
            """,
            (
                position_id,
                position.pool_address,
                observed_at,
                observed_at,
                str(amount_x),
                str(amount_y),
                position.min_bin_id,
                position.max_bin_id,
                position.strategy,
                max_share_bps,
                int(favor_x_in_active_bin),
                json.dumps(
                    {
                        "entry_active_bin_id": active_id,
                        "entry_price_q64": str(price_q64),
                        "entry_token_y_quote_per_atomic": str(
                            token_y_quote_per_atomic
                        ),
                        "notional_error_bps": error_bps,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
    return paper_chain_binding(storage, position_id=position_id)


def value_paper_position_from_chain(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    token_y_quote_per_atomic: float,
) -> PaperChainValuation:
    if token_y_quote_per_atomic <= 0:
        raise ValueError("token_y_quote_per_atomic must be positive")

    binding = paper_chain_binding(storage, position_id=position_id)
    if binding.status != "ACTIVE":
        raise ValueError("paper chain binding is not active")
    if observed_at <= binding.anchor_observed_at:
        raise ValueError("valuation observation must be after replay anchor")

    store = ResearchStore(storage.path)
    times = store.chain_observation_times(
        binding.pool_address,
        limit=None,
        ascending=True,
    )
    path = [
        item
        for item in times
        if binding.anchor_observed_at <= item <= observed_at
    ]
    if not path or path[0] != binding.anchor_observed_at:
        raise ValueError("replay anchor is missing from chain observation history")
    if path[-1] != observed_at:
        raise ValueError("target chain observation is missing")
    if len(path) < 2:
        raise ValueError("need at least two observations for chain valuation")

    replay = replay_small_lp_history(
        str(storage.path),
        pool_address=binding.pool_address,
        amount_x=binding.amount_x,
        amount_y=binding.amount_y,
        min_bin_id=binding.min_bin_id,
        max_bin_id=binding.max_bin_id,
        strategy=StrategyType(binding.strategy),
        observation_limit=len(path),
        observation_times=path,
        max_share_bps=binding.max_share_bps,
        favor_x_in_active_bin=binding.favor_x_active,
    )
    pool = store.chain_pool_snapshot_at(binding.pool_address, observed_at)
    if pool is None:
        raise ValueError("target chain pool snapshot is missing")
    active_id = int(pool["active_bin_id"])
    price_q64 = _active_price(
        store,
        pool_address=binding.pool_address,
        observed_at=observed_at,
        active_bin_id=active_id,
    )

    principal_x = replay.ending_x + replay.idle_x
    principal_y = replay.ending_y + replay.idle_y
    principal_y_atomic = q64_value_in_y_atomic(
        amount_x=principal_x,
        amount_y=principal_y,
        price_q64=price_q64,
    )

    if replay.fee_x < binding.last_fee_x or replay.fee_y < binding.last_fee_y:
        raise ValueError("replay cumulative fees moved backwards")
    fee_delta_x = replay.fee_x - binding.last_fee_x
    fee_delta_y = replay.fee_y - binding.last_fee_y
    fee_delta_y_atomic = q64_value_in_y_atomic(
        amount_x=fee_delta_x,
        amount_y=fee_delta_y,
        price_q64=price_q64,
    )
    rate = _d(token_y_quote_per_atomic)

    return PaperChainValuation(
        position_id=position_id,
        pool_address=binding.pool_address,
        anchor_observed_at=binding.anchor_observed_at,
        observed_at=observed_at,
        active_bin_id=active_id,
        current_price_q64=price_q64,
        principal_x=principal_x,
        principal_y=principal_y,
        principal_value_y_atomic=principal_y_atomic,
        mark_quote=float(_d(principal_y_atomic) * rate),
        cumulative_fee_x=replay.fee_x,
        cumulative_fee_y=replay.fee_y,
        fee_delta_x=fee_delta_x,
        fee_delta_y=fee_delta_y,
        fee_delta_value_y_atomic=fee_delta_y_atomic,
        fee_delta_quote=float(_d(fee_delta_y_atomic) * rate),
        reward_one=replay.reward_one,
        reward_two=replay.reward_two,
        rewards_valued=(replay.reward_one == 0 and replay.reward_two == 0),
        max_observed_share_bps=replay.max_observed_share_bps,
        replay_fidelity=replay.replay_fidelity,
        quote_fidelity="EXPLICIT_TOKEN_Y_QUOTE_V1",
    )


def _holding_observations(
    storage: Storage,
    *,
    binding: PaperChainBinding,
    observed_at: str,
) -> int:
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
    if not covered or covered[0] != binding.opened_observed_at:
        raise ValueError("paper opening observation is missing from chain history")
    if covered[-1] != observed_at:
        raise ValueError("target paper observation is missing from chain history")
    return len(covered)


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
    valuation = value_paper_position_from_chain(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        token_y_quote_per_atomic=token_y_quote_per_atomic,
    )
    if not valuation.rewards_valued:
        raise ValueError(
            "chain replay produced reward income but reward-token quote is unavailable"
        )

    binding = paper_chain_binding(storage, position_id=position_id)
    result = apply_paper_observation(
        storage,
        event_key_prefix=f"paper-chain:{position_id}:{observed_at}",
        position_id=position_id,
        active_bin_id=valuation.active_bin_id,
        holding_observations=_holding_observations(
            storage,
            binding=binding,
            observed_at=observed_at,
        ),
        mark_quote=valuation.mark_quote,
        fee_delta_quote=valuation.fee_delta_quote,
        reward_delta_quote=0.0,
        pool_safe=pool_safe,
        emergency_exit=emergency_exit,
        estimated_exit_cost_quote=estimated_exit_cost_quote,
        rebalance_cost_quote=rebalance_cost_quote,
        event_time=observed_at,
        config=config,
    )

    with storage.connect() as conn:
        if result.executed_action == "REBALANCE":
            conn.execute(
                """
                UPDATE paper_chain_bindings
                SET anchor_observed_at = ?, amount_x = ?, amount_y = ?,
                    min_bin_id = ?, max_bin_id = ?,
                    last_observed_at = ?, last_fee_x = '0', last_fee_y = '0'
                WHERE position_id = ?
                """,
                (
                    observed_at,
                    str(valuation.principal_x),
                    str(valuation.principal_y),
                    result.position_after.min_bin_id,
                    result.position_after.max_bin_id,
                    observed_at,
                    position_id,
                ),
            )
        elif result.executed_action == "EXIT":
            conn.execute(
                """
                UPDATE paper_chain_bindings
                SET last_observed_at = ?, last_fee_x = ?, last_fee_y = ?,
                    status = 'CLOSED'
                WHERE position_id = ?
                """,
                (
                    observed_at,
                    str(valuation.cumulative_fee_x),
                    str(valuation.cumulative_fee_y),
                    position_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE paper_chain_bindings
                SET last_observed_at = ?, last_fee_x = ?, last_fee_y = ?
                WHERE position_id = ?
                """,
                (
                    observed_at,
                    str(valuation.cumulative_fee_x),
                    str(valuation.cumulative_fee_y),
                    position_id,
                ),
            )

    return PaperChainObservationResult(
        valuation=valuation,
        executed_action=result.executed_action,
        detail=result.to_record(),
    )
