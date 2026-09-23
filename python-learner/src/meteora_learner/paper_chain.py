from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
from typing import Any

from .baseline_policy import q64_value_in_y_atomic
from .chain_replay import STANDARD_SPL_TOKEN_PROGRAM, replay_small_lp_history
from .deposit_plan import distribute_standard_spl_deposit, project_deposit_shares
from .paper_account import paper_position_snapshot
from .paper_runner import PaperBatchObservation, run_paper_observation_batch
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
class PaperCounterfactualBinding:
    position_id: str
    pool_address: str
    entry_observed_at: str
    amount_x_atomic: int
    amount_y_atomic: int
    idle_x_atomic: int
    idle_y_atomic: int
    entry_price_q64: int
    entry_value_y_atomic: int
    capital_quote: float
    max_share_bps: int
    favor_x_active: bool
    token_x_mint: str
    token_y_mint: str
    reward_mint_0: str | None
    reward_mint_1: str | None
    state: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperChainValuation:
    position_id: str
    pool_address: str
    observed_at: str
    active_bin_id: int
    mark_quote: float
    fee_delta_quote: float
    reward_delta_quote: float
    inventory_x_atomic: int
    inventory_y_atomic: int
    cumulative_fee_x_atomic: int
    cumulative_fee_y_atomic: int
    reward_one_atomic: int
    reward_two_atomic: int
    rewards_valued: bool
    token_y_quote_per_atomic: float
    max_observed_share_bps: int
    replay_fidelity: str
    quote_fidelity: str
    next_state: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperChainApplyResult:
    valuation: PaperChainValuation
    batch_status: str
    executed_action: str | None
    recovered: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


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


def counterfactual_binding(
    storage: Storage,
    *,
    position_id: str,
) -> PaperCounterfactualBinding:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT position_id, pool_address, entry_observed_at,
                   amount_x_atomic, amount_y_atomic,
                   idle_x_atomic, idle_y_atomic,
                   entry_price_q64, entry_value_y_atomic,
                   capital_quote, max_share_bps, favor_x_active,
                   token_x_mint, token_y_mint,
                   reward_mint_0, reward_mint_1, initial_state_json
            FROM paper_counterfactual_positions
            WHERE position_id = ?
            LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"paper position has no counterfactual binding: {position_id}")
    return PaperCounterfactualBinding(
        position_id=str(row[0]),
        pool_address=str(row[1]),
        entry_observed_at=str(row[2]),
        amount_x_atomic=int(str(row[3])),
        amount_y_atomic=int(str(row[4])),
        idle_x_atomic=int(str(row[5])),
        idle_y_atomic=int(str(row[6])),
        entry_price_q64=int(str(row[7])),
        entry_value_y_atomic=int(str(row[8])),
        capital_quote=float(row[9]),
        max_share_bps=int(row[10]),
        favor_x_active=bool(row[11]),
        token_x_mint=str(row[12]),
        token_y_mint=str(row[13]),
        reward_mint_0=str(row[14]) if row[14] is not None else None,
        reward_mint_1=str(row[15]) if row[15] is not None else None,
        state=json.loads(str(row[16])),
    )


def _latest_state(storage: Storage, binding: PaperCounterfactualBinding) -> dict[str, Any]:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT next_state_json
            FROM paper_chain_valuations
            WHERE position_id = ? AND status = 'APPLIED'
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            (binding.position_id,),
        ).fetchone()
    return json.loads(str(row[0])) if row is not None else dict(binding.state)


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
) -> PaperCounterfactualBinding:
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
    for side in ("x", "y"):
        program = pool.get(f"token_{side}_program")
        if str(program) != STANDARD_SPL_TOKEN_PROGRAM:
            raise ValueError("counterfactual paper binding requires standard SPL tokens")

    active_id = int(pool["active_bin_id"])
    bins = store.load_bin_liquidity(position.pool_address, observed_at=observed_at)
    prices = {int(row["bin_id"]): int(str(row["price"])) for row in bins}
    plan = distribute_standard_spl_deposit(
        active_id=active_id,
        min_bin_id=position.min_bin_id,
        max_bin_id=position.max_bin_id,
        amount_x=amount_x,
        amount_y=amount_y,
        strategy=StrategyType(position.strategy),
        prices_q64=prices,
        favor_x_in_active_bin=favor_x_in_active_bin,
    )
    projected = project_deposit_shares(plan, bins)
    for item in projected.bins:
        supply = item.existing_liquidity_supply
        if supply <= 0:
            raise ValueError("counterfactual binding cannot use an empty historical bin")
        if item.liquidity_share_minted * 10_000 > supply * max_share_bps:
            raise ValueError("counterfactual binding exceeds configured small-LP share")

    price_q64 = _active_price(
        store,
        pool_address=position.pool_address,
        observed_at=observed_at,
        active_bin_id=active_id,
    )
    entry_value_y_atomic = q64_value_in_y_atomic(
        amount_x=amount_x,
        amount_y=amount_y,
        price_q64=price_q64,
    )
    implied_quote = _d(entry_value_y_atomic) * _d(token_y_quote_per_atomic)
    target_quote = _d(position.entry_capital_quote)
    error_bps = int(abs(implied_quote - target_quote) * BPS / target_quote)
    if error_bps > max_notional_error_bps:
        raise ValueError(
            f"atomic binding notional error {error_bps} bps exceeds "
            f"{max_notional_error_bps} bps"
        )

    state = {
        "anchor_observed_at": observed_at,
        "amount_x": amount_x,
        "amount_y": amount_y,
        "min_bin_id": position.min_bin_id,
        "max_bin_id": position.max_bin_id,
        "strategy": position.strategy,
        "last_fee_x": 0,
        "last_fee_y": 0,
        "last_observed_at": observed_at,
        "max_share_bps": max_share_bps,
        "favor_x_active": bool(favor_x_in_active_bin),
    }

    with storage.connect() as conn:
        if conn.execute(
            "SELECT 1 FROM paper_counterfactual_positions WHERE position_id = ?",
            (position_id,),
        ).fetchone() is not None:
            raise ValueError(f"counterfactual paper binding already exists: {position_id}")
        conn.execute(
            """
            INSERT INTO paper_counterfactual_positions(
                position_id, pool_address, entry_observed_at,
                amount_x_atomic, amount_y_atomic,
                idle_x_atomic, idle_y_atomic,
                entry_price_q64, entry_value_y_atomic, capital_quote,
                max_share_bps, favor_x_active,
                token_x_mint, token_y_mint,
                reward_mint_0, reward_mint_1, initial_state_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                position.pool_address,
                observed_at,
                str(amount_x),
                str(amount_y),
                str(plan.idle_x),
                str(plan.idle_y),
                str(price_q64),
                str(entry_value_y_atomic),
                str(position.entry_capital_quote),
                max_share_bps,
                int(favor_x_in_active_bin),
                str(pool["token_x_mint"]),
                str(pool["token_y_mint"]),
                str(pool["reward_mint_0"]) if pool.get("reward_mint_0") is not None else None,
                str(pool["reward_mint_1"]) if pool.get("reward_mint_1") is not None else None,
                json.dumps(state, separators=(",", ":")),
            ),
        )
    return counterfactual_binding(storage, position_id=position_id)


def prepare_chain_valuation(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    token_y_quote_per_atomic: float,
) -> PaperChainValuation:
    if token_y_quote_per_atomic <= 0:
        raise ValueError("token_y_quote_per_atomic must be positive")
    binding = counterfactual_binding(storage, position_id=position_id)

    with storage.connect() as conn:
        existing = conn.execute(
            """
            SELECT valuation_json
            FROM paper_chain_valuations
            WHERE position_id = ? AND observed_at = ?
            LIMIT 1
            """,
            (position_id, observed_at),
        ).fetchone()
    if existing is not None:
        raw = json.loads(str(existing[0]))
        stored_rate = float(raw["token_y_quote_per_atomic"])
        if abs(stored_rate - token_y_quote_per_atomic) > max(1e-18, abs(stored_rate) * 1e-12):
            raise ValueError("existing valuation used a different token-Y quote")
        return PaperChainValuation(**raw)

    state = _latest_state(storage, binding)
    anchor = str(state["anchor_observed_at"])
    if observed_at <= anchor:
        raise ValueError("valuation observation must be after replay anchor")

    store = ResearchStore(storage.path)
    times = store.chain_observation_times(
        binding.pool_address,
        limit=None,
        ascending=True,
    )
    path = [item for item in times if anchor <= item <= observed_at]
    if not path or path[0] != anchor or path[-1] != observed_at:
        raise ValueError("valuation path does not contain anchor and target observations")
    if len(path) < 2:
        raise ValueError("need at least two observations for chain valuation")

    replay = replay_small_lp_history(
        str(storage.path),
        pool_address=binding.pool_address,
        amount_x=int(state["amount_x"]),
        amount_y=int(state["amount_y"]),
        min_bin_id=int(state["min_bin_id"]),
        max_bin_id=int(state["max_bin_id"]),
        strategy=StrategyType(str(state["strategy"])),
        observation_limit=len(path),
        observation_times=path,
        max_share_bps=int(state["max_share_bps"]),
        favor_x_in_active_bin=bool(state["favor_x_active"]),
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

    inventory_x = replay.ending_x + replay.idle_x
    inventory_y = replay.ending_y + replay.idle_y
    mark_y_atomic = q64_value_in_y_atomic(
        amount_x=inventory_x,
        amount_y=inventory_y,
        price_q64=price_q64,
    )
    last_fee_x = int(state.get("last_fee_x", 0))
    last_fee_y = int(state.get("last_fee_y", 0))
    if replay.fee_x < last_fee_x or replay.fee_y < last_fee_y:
        raise ValueError("replay cumulative fees moved backwards")
    fee_delta_x = replay.fee_x - last_fee_x
    fee_delta_y = replay.fee_y - last_fee_y
    fee_delta_y_atomic = q64_value_in_y_atomic(
        amount_x=fee_delta_x,
        amount_y=fee_delta_y,
        price_q64=price_q64,
    )
    rate = _d(token_y_quote_per_atomic)
    rewards_valued = replay.reward_one == 0 and replay.reward_two == 0

    next_state = dict(state)
    next_state["last_observed_at"] = observed_at
    next_state["last_fee_x"] = replay.fee_x
    next_state["last_fee_y"] = replay.fee_y

    valuation = PaperChainValuation(
        position_id=position_id,
        pool_address=binding.pool_address,
        observed_at=observed_at,
        active_bin_id=active_id,
        mark_quote=float(_d(mark_y_atomic) * rate),
        fee_delta_quote=float(_d(fee_delta_y_atomic) * rate),
        reward_delta_quote=0.0,
        inventory_x_atomic=inventory_x,
        inventory_y_atomic=inventory_y,
        cumulative_fee_x_atomic=replay.fee_x,
        cumulative_fee_y_atomic=replay.fee_y,
        reward_one_atomic=replay.reward_one,
        reward_two_atomic=replay.reward_two,
        rewards_valued=rewards_valued,
        token_y_quote_per_atomic=float(rate),
        max_observed_share_bps=replay.max_observed_share_bps,
        replay_fidelity=replay.replay_fidelity,
        quote_fidelity="EXPLICIT_TOKEN_Y_QUOTE_V1",
        next_state=next_state,
    )
    event_hash = hashlib.sha256(
        f"{position_id}|{observed_at}".encode("utf-8")
    ).hexdigest()[:24]
    prefix = f"paper-chain:{event_hash}:{position_id}"
    raw = valuation.to_record()

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_chain_valuations(
                position_id, observed_at, event_key_prefix, status,
                active_bin_id, mark_quote, fee_delta_quote, reward_delta_quote,
                inventory_x_atomic, inventory_y_atomic,
                fee_x_atomic, fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                next_state_json, valuation_json
            ) VALUES (?, ?, ?, 'PREPARED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                observed_at,
                prefix,
                active_id,
                str(valuation.mark_quote),
                str(valuation.fee_delta_quote),
                "0",
                str(inventory_x),
                str(inventory_y),
                str(replay.fee_x),
                str(replay.fee_y),
                str(replay.reward_one),
                str(replay.reward_two),
                json.dumps(next_state, separators=(",", ":")),
                json.dumps(raw, separators=(",", ":")),
            ),
        )
    return valuation


def _holding_observations(
    storage: Storage,
    *,
    binding: PaperCounterfactualBinding,
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
        if binding.entry_observed_at <= item <= observed_at
    ]
    if not covered or covered[0] != binding.entry_observed_at or covered[-1] != observed_at:
        raise ValueError("paper holding window is incomplete in chain history")
    return len(covered)


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
) -> PaperChainApplyResult:
    valuation = prepare_chain_valuation(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        token_y_quote_per_atomic=token_y_quote_per_atomic,
    )
    if not valuation.rewards_valued:
        raise ValueError(
            "chain replay produced reward income but reward-token quote is unavailable"
        )
    binding = counterfactual_binding(storage, position_id=position_id)

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT event_key_prefix, status, next_state_json
            FROM paper_chain_valuations
            WHERE position_id = ? AND observed_at = ?
            """,
            (position_id, observed_at),
        ).fetchone()
    if row is None:
        raise RuntimeError("prepared paper chain valuation disappeared")
    prefix = str(row[0])
    if str(row[1]) == "APPLIED":
        return PaperChainApplyResult(
            valuation=valuation,
            batch_status="COMPLETE",
            executed_action=None,
            recovered=True,
        )

    run_id = prefix.split(":")[1]
    report = run_paper_observation_batch(
        storage,
        run_id=run_id,
        observed_at=observed_at,
        observations=(
            PaperBatchObservation(
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
            ),
        ),
        config=config,
        retry_failed=True,
    )
    item = report.items[0]
    if item.status != "APPLIED":
        raise ValueError(item.error or "paper chain observation did not apply")
    action = item.executed_action or "UNKNOWN"

    next_state = dict(valuation.next_state)
    position = paper_position_snapshot(storage, position_id=position_id)
    if action.startswith("REBALANCE") and action != "REBALANCE_PENDING_COST":
        next_state.update(
            {
                "anchor_observed_at": observed_at,
                "amount_x": valuation.inventory_x_atomic,
                "amount_y": valuation.inventory_y_atomic,
                "min_bin_id": position.min_bin_id,
                "max_bin_id": position.max_bin_id,
                "last_fee_x": 0,
                "last_fee_y": 0,
            }
        )

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE paper_chain_valuations
            SET status = 'APPLIED', next_state_json = ?
            WHERE position_id = ? AND observed_at = ?
            """,
            (
                json.dumps(next_state, separators=(",", ":")),
                position_id,
                observed_at,
            ),
        )

    return PaperChainApplyResult(
        valuation=valuation,
        batch_status=report.status,
        executed_action=action,
        recovered=item.recovered,
    )
