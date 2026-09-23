from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from typing import Any

from .baseline_policy import q64_value_in_y_atomic
from .chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from .deposit_plan import distribute_standard_spl_deposit, project_deposit_shares
from .liquidity_math import (
    amounts_from_liquidity_share,
    fee_from_checkpoint_delta,
    reward_from_checkpoint_delta,
)
from .paper_account import (
    close_paper_position,
    paper_account_snapshot,
    paper_position_snapshot,
)
from .paper_policy import evaluate_paper_position_policy
from .paper_cycle import apply_paper_observation
from .phase3_plan import Phase3ResearchPlan
from .position_policy import PositionManagementConfig
from .research_store import ResearchStore
from .storage import Storage


@dataclass(frozen=True)
class PaperCounterfactualState:
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
    bins: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperChainValuation:
    position_id: str
    observed_at: str
    status: str
    active_bin_id: int
    mark_quote: float
    fee_delta_quote: float
    reward_delta_quote: float
    inventory_x_atomic: int
    inventory_y_atomic: int
    fee_x_atomic: int
    fee_y_atomic: int
    reward_one_atomic: int
    reward_two_atomic: int
    max_observed_share_bps: int
    token_y_quote_per_atomic: float
    quote_fidelity: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AppliedPaperChainValuation:
    valuation: PaperChainValuation
    executed_action: str
    recovered: bool
    position_status: str
    account_equity_quote: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _share_bps(share: int, supply: int) -> int:
    if supply <= 0:
        raise ValueError("real bin supply must be positive")
    return share * 10_000 // supply


def _validate_pool(pool: dict[str, Any]) -> None:
    for side in ("x", "y"):
        program = pool.get(f"token_{side}_program")
        if program is None:
            raise ValueError("token program metadata is missing")
        if str(program) != STANDARD_SPL_TOKEN_PROGRAM:
            raise ValueError("paper counterfactual valuation supports standard SPL only")


def _state_row(
    *,
    row: dict[str, Any],
    share: int,
) -> dict[str, Any]:
    supply = int(str(row["liquidity_supply"]))
    if supply <= 0:
        raise ValueError(
            f"counterfactual bin {row['bin_id']} has non-positive real supply"
        )
    return {
        "bin_id": int(row["bin_id"]),
        "liquidity_share": int(share),
        "real_supply": supply,
        "fee_x_checkpoint": int(str(row["fee_amount_x_per_token_stored"])),
        "fee_y_checkpoint": int(str(row["fee_amount_y_per_token_stored"])),
        "reward_0_checkpoint": int(str(row["reward_per_token_stored_0"])),
        "reward_1_checkpoint": int(str(row["reward_per_token_stored_1"])),
    }


def initialize_paper_counterfactual(
    storage: Storage,
    *,
    position_id: str,
    plan: Phase3ResearchPlan,
) -> PaperCounterfactualState:
    position = paper_position_snapshot(storage, position_id=position_id)
    if position.status != "OPEN":
        raise ValueError("paper position must be open")
    if position.rebalances != 0:
        raise ValueError("counterfactual state must be initialized before rebalancing")
    if plan.entry_gate is None or plan.entry_gate.proposal is None:
        raise ValueError("Phase 3 plan has no proposal")
    if plan.decision_observed_at is None:
        raise ValueError("Phase 3 plan has no decision_observed_at")
    if plan.amount_x < 0 or plan.amount_y < 0:
        raise ValueError("Phase 3 plan token amounts cannot be negative")
    if plan.amount_x == 0 and plan.amount_y == 0:
        raise ValueError("Phase 3 plan has no atomic entry amount")
    if position.pool_address != plan.pool_address:
        raise ValueError("paper position and Phase 3 plan use different pools")

    proposal = plan.entry_gate.proposal
    if (
        position.strategy != proposal.strategy
        or position.min_bin_id != proposal.min_bin_id
        or position.max_bin_id != proposal.max_bin_id
    ):
        raise ValueError("paper position does not match Phase 3 proposal")

    store = ResearchStore(storage.path)
    pool = store.chain_pool_snapshot_at(
        plan.pool_address,
        plan.decision_observed_at,
    )
    if pool is None:
        raise ValueError("decision-time chain pool snapshot is missing")
    _validate_pool(pool)

    rows = store.load_bin_liquidity(
        plan.pool_address,
        observed_at=plan.decision_observed_at,
    )
    if not rows:
        raise ValueError("decision-time bin state is missing")
    by_id = {int(row["bin_id"]): row for row in rows}

    active_id = int(pool["active_bin_id"])
    active_row = by_id.get(active_id)
    if active_row is None:
        raise ValueError("decision active bin is not covered")
    entry_price_q64 = int(str(active_row["price"]))

    prices = {
        int(row["bin_id"]): int(str(row["price"]))
        for row in rows
    }
    deposit = distribute_standard_spl_deposit(
        active_id=active_id,
        min_bin_id=proposal.min_bin_id,
        max_bin_id=proposal.max_bin_id,
        amount_x=plan.amount_x,
        amount_y=plan.amount_y,
        strategy=proposal.strategy,
        prices_q64=prices,
        favor_x_in_active_bin=plan.favor_x_in_active_bin,
    )
    projected = project_deposit_shares(deposit, rows)
    if not projected.bins:
        raise ValueError("Phase 3 proposal creates no paper liquidity")

    state_bins: list[dict[str, Any]] = []
    max_share = 0
    for item in projected.bins:
        row = by_id[item.bin_id]
        supply = int(str(row["liquidity_supply"]))
        if supply <= 0:
            raise ValueError(
                f"counterfactual entry bin {item.bin_id} has zero real supply"
            )
        share_bps = _share_bps(item.liquidity_share_minted, supply)
        max_share = max(max_share, share_bps)
        if share_bps > plan.max_share_bps:
            raise ValueError(
                f"counterfactual share {share_bps} bps exceeds "
                f"{plan.max_share_bps} bps limit"
            )
        state_bins.append(
            _state_row(
                row=row,
                share=item.liquidity_share_minted,
            )
        )

    entry_value = q64_value_in_y_atomic(
        amount_x=plan.amount_x,
        amount_y=plan.amount_y,
        price_q64=entry_price_q64,
    )
    if entry_value <= 0:
        raise ValueError("entry atomic value must be positive")

    payload = {
        "bins": state_bins,
        "max_observed_share_bps": max_share,
    }
    with storage.connect() as conn:
        existing = conn.execute(
            """
            SELECT position_id
            FROM paper_counterfactual_positions
            WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        if existing is not None:
            raise ValueError("paper counterfactual state already exists")
        conn.execute(
            """
            INSERT INTO paper_counterfactual_positions(
                position_id, pool_address, entry_observed_at,
                amount_x_atomic, amount_y_atomic, idle_x_atomic, idle_y_atomic,
                entry_price_q64, entry_value_y_atomic, capital_quote,
                max_share_bps, favor_x_active, token_x_mint, token_y_mint,
                reward_mint_0, reward_mint_1, initial_state_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                plan.pool_address,
                plan.decision_observed_at,
                str(plan.amount_x),
                str(plan.amount_y),
                str(deposit.idle_x),
                str(deposit.idle_y),
                str(entry_price_q64),
                str(entry_value),
                str(position.entry_capital_quote),
                int(plan.max_share_bps),
                int(plan.favor_x_in_active_bin),
                str(pool["token_x_mint"]),
                str(pool["token_y_mint"]),
                (
                    str(pool["reward_mint_0"])
                    if pool.get("reward_mint_0") is not None
                    else None
                ),
                (
                    str(pool["reward_mint_1"])
                    if pool.get("reward_mint_1") is not None
                    else None
                ),
                json.dumps(payload, separators=(",", ":")),
            ),
        )

    return PaperCounterfactualState(
        position_id=position_id,
        pool_address=plan.pool_address,
        entry_observed_at=plan.decision_observed_at,
        amount_x_atomic=plan.amount_x,
        amount_y_atomic=plan.amount_y,
        idle_x_atomic=deposit.idle_x,
        idle_y_atomic=deposit.idle_y,
        entry_price_q64=entry_price_q64,
        entry_value_y_atomic=entry_value,
        capital_quote=position.entry_capital_quote,
        max_share_bps=plan.max_share_bps,
        favor_x_active=plan.favor_x_in_active_bin,
        bins=len(state_bins),
    )


def _counterfactual_row(
    storage: Storage,
    *,
    position_id: str,
) -> dict[str, Any]:
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
        raise ValueError("paper counterfactual state is not initialized")
    return dict(row)


def _previous_state(
    storage: Storage,
    *,
    position_id: str,
    base: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT observed_at, next_state_json
            FROM paper_chain_valuations
            WHERE position_id = ? AND status = 'APPLIED'
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    if row is None:
        return str(base["entry_observed_at"]), json.loads(
            str(base["initial_state_json"])
        )
    return str(row[0]), json.loads(str(row[1]))


def _value_reward(
    *,
    amount: int,
    mint: str | None,
    token_x_mint: str,
    token_y_mint: str,
    price_q64: int,
) -> int:
    if amount == 0:
        return 0
    if mint is None:
        raise ValueError("reward growth exists but reward mint is unavailable")
    if mint == token_x_mint:
        return q64_value_in_y_atomic(
            amount_x=amount,
            amount_y=0,
            price_q64=price_q64,
        )
    if mint == token_y_mint:
        return amount
    raise ValueError(
        f"reward mint {mint} has no token-Y valuation in paper v1"
    )


def _valuation_from_row(row: Any) -> PaperChainValuation:
    return PaperChainValuation(
        position_id=str(row["position_id"]),
        observed_at=str(row["observed_at"]),
        status=str(row["status"]),
        active_bin_id=int(row["active_bin_id"]),
        mark_quote=float(_d(row["mark_quote"])),
        fee_delta_quote=float(_d(row["fee_delta_quote"])),
        reward_delta_quote=float(_d(row["reward_delta_quote"])),
        inventory_x_atomic=int(str(row["inventory_x_atomic"])),
        inventory_y_atomic=int(str(row["inventory_y_atomic"])),
        fee_x_atomic=int(str(row["fee_x_atomic"])),
        fee_y_atomic=int(str(row["fee_y_atomic"])),
        reward_one_atomic=int(str(row["reward_one_atomic"])),
        reward_two_atomic=int(str(row["reward_two_atomic"])),
        max_observed_share_bps=int(
            json.loads(str(row["valuation_json"]))["max_observed_share_bps"]
        ),
        token_y_quote_per_atomic=float(
            json.loads(str(row["valuation_json"]))["token_y_quote_per_atomic"]
        ),
        quote_fidelity=str(
            json.loads(str(row["valuation_json"]))["quote_fidelity"]
        ),
    )


def prepare_paper_chain_valuation(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    token_y_quote_per_atomic: float | None = None,
) -> PaperChainValuation:
    if not observed_at.strip():
        raise ValueError("observed_at is required")
    position = paper_position_snapshot(storage, position_id=position_id)
    if position.status != "OPEN":
        raise ValueError("paper position must be open")
    if position.rebalances != 0:
        raise ValueError(
            "paper v1 chain valuation fails closed after a rebalance"
        )

    base = _counterfactual_row(storage, position_id=position_id)
    if token_y_quote_per_atomic is None:
        quote_rate = _d(base["capital_quote"]) / _d(base["entry_value_y_atomic"])
        quote_fidelity = "ENTRY_IMPLIED_TOKEN_Y_QUOTE_V1"
    else:
        quote_rate = _d(token_y_quote_per_atomic)
        if quote_rate <= 0:
            raise ValueError("token_y_quote_per_atomic must be positive")
        quote_fidelity = "EXPLICIT_TOKEN_Y_QUOTE_V1"

    with storage.connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        existing = conn.execute(
            """
            SELECT *
            FROM paper_chain_valuations
            WHERE position_id = ? AND observed_at = ?
            """,
            (position_id, observed_at),
        ).fetchone()
    if existing is not None:
        existing_meta = json.loads(str(existing["valuation_json"]))
        if _d(existing_meta["token_y_quote_per_atomic"]) != quote_rate:
            raise ValueError(
                "prepared paper valuation uses a different token-Y quote rate"
            )
        return _valuation_from_row(existing)

    previous_at, previous = _previous_state(
        storage,
        position_id=position_id,
        base=base,
    )
    if observed_at <= previous_at:
        raise ValueError(
            "paper chain valuation must advance beyond the last applied observation"
        )

    store = ResearchStore(storage.path)
    pool = store.chain_pool_snapshot_at(position.pool_address, observed_at)
    if pool is None:
        raise ValueError("chain pool snapshot is missing at valuation time")
    _validate_pool(pool)
    if str(pool["token_x_mint"]) != str(base["token_x_mint"]):
        raise ValueError("token X mint changed")
    if str(pool["token_y_mint"]) != str(base["token_y_mint"]):
        raise ValueError("token Y mint changed")

    rows = store.load_bin_liquidity(
        position.pool_address,
        observed_at=observed_at,
    )
    by_id = {int(row["bin_id"]): row for row in rows}
    if not by_id:
        raise ValueError("bin state is missing at valuation time")

    active_id = int(pool["active_bin_id"])
    active_row = by_id.get(active_id)
    if active_row is None:
        raise ValueError("active bin is not covered at valuation time")
    price_q64 = int(str(active_row["price"]))

    inventory_x = int(str(base["idle_x_atomic"]))
    inventory_y = int(str(base["idle_y_atomic"]))
    fee_x = 0
    fee_y = 0
    reward_one = 0
    reward_two = 0
    next_bins: list[dict[str, Any]] = []
    max_share = 0

    for state in previous["bins"]:
        bin_id = int(state["bin_id"])
        row = by_id.get(bin_id)
        if row is None:
            raise ValueError(f"valuation snapshot does not cover bin {bin_id}")

        share = int(state["liquidity_share"])
        previous_supply = int(state["real_supply"])
        current_supply = int(str(row["liquidity_supply"]))
        if previous_supply <= 0 or current_supply <= 0:
            raise ValueError("paper counterfactual requires positive real supply")

        share_bps = _share_bps(share, current_supply)
        max_share = max(max_share, share_bps)
        if share_bps > int(base["max_share_bps"]):
            raise ValueError(
                f"counterfactual share {share_bps} bps exceeds "
                f"{base['max_share_bps']} bps limit"
            )

        current_fee_x = int(str(row["fee_amount_x_per_token_stored"]))
        current_fee_y = int(str(row["fee_amount_y_per_token_stored"]))
        current_reward_0 = int(str(row["reward_per_token_stored_0"]))
        current_reward_1 = int(str(row["reward_per_token_stored_1"]))

        deltas = (
            current_fee_x - int(state["fee_x_checkpoint"]),
            current_fee_y - int(state["fee_y_checkpoint"]),
            current_reward_0 - int(state["reward_0_checkpoint"]),
            current_reward_1 - int(state["reward_1_checkpoint"]),
        )
        if any(value < 0 for value in deltas):
            raise ValueError(
                f"checkpoint regressed for counterfactual bin {bin_id}"
            )

        diluted = tuple(
            value * previous_supply // (previous_supply + share)
            for value in deltas
        )
        fee_x += fee_from_checkpoint_delta(
            liquidity_share=share,
            fee_per_token_delta=diluted[0],
        )
        fee_y += fee_from_checkpoint_delta(
            liquidity_share=share,
            fee_per_token_delta=diluted[1],
        )
        reward_one += reward_from_checkpoint_delta(
            liquidity_share=share,
            reward_per_token_delta=diluted[2],
        )
        reward_two += reward_from_checkpoint_delta(
            liquidity_share=share,
            reward_per_token_delta=diluted[3],
        )

        amount_x, amount_y = amounts_from_liquidity_share(
            liquidity_share=share,
            bin_amount_x=int(str(row["amount_x"])),
            bin_amount_y=int(str(row["amount_y"])),
            liquidity_supply=current_supply,
        )
        inventory_x += amount_x
        inventory_y += amount_y
        next_bins.append(
            _state_row(row=row, share=share)
        )

    mark_y_atomic = q64_value_in_y_atomic(
        amount_x=inventory_x,
        amount_y=inventory_y,
        price_q64=price_q64,
    )
    fee_y_atomic = q64_value_in_y_atomic(
        amount_x=fee_x,
        amount_y=fee_y,
        price_q64=price_q64,
    )
    reward_y_atomic = _value_reward(
        amount=reward_one,
        mint=(
            str(base["reward_mint_0"])
            if base.get("reward_mint_0") is not None
            else None
        ),
        token_x_mint=str(base["token_x_mint"]),
        token_y_mint=str(base["token_y_mint"]),
        price_q64=price_q64,
    ) + _value_reward(
        amount=reward_two,
        mint=(
            str(base["reward_mint_1"])
            if base.get("reward_mint_1") is not None
            else None
        ),
        token_x_mint=str(base["token_x_mint"]),
        token_y_mint=str(base["token_y_mint"]),
        price_q64=price_q64,
    )

    mark_quote = _d(mark_y_atomic) * quote_rate
    fee_quote = _d(fee_y_atomic) * quote_rate
    reward_quote = _d(reward_y_atomic) * quote_rate

    next_state = {
        "bins": next_bins,
        "max_observed_share_bps": max_share,
    }
    valuation = {
        "previous_observed_at": previous_at,
        "max_observed_share_bps": max_share,
        "mark_y_atomic": mark_y_atomic,
        "fee_value_y_atomic": fee_y_atomic,
        "reward_value_y_atomic": reward_y_atomic,
        "token_y_quote_per_atomic": str(quote_rate),
        "quote_fidelity": quote_fidelity,
    }
    prefix = f"paper-chain:{position_id}:{observed_at}"
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
                str(mark_quote),
                str(fee_quote),
                str(reward_quote),
                str(inventory_x),
                str(inventory_y),
                str(fee_x),
                str(fee_y),
                str(reward_one),
                str(reward_two),
                json.dumps(next_state, separators=(",", ":")),
                json.dumps(valuation, separators=(",", ":")),
            ),
        )
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            """
            SELECT *
            FROM paper_chain_valuations
            WHERE position_id = ? AND observed_at = ?
            """,
            (position_id, observed_at),
        ).fetchone()
    return _valuation_from_row(row)


def _mark_event_exists(storage: Storage, prefix: str) -> bool:
    with storage.connect() as conn:
        return conn.execute(
            """
            SELECT 1 FROM paper_events
            WHERE event_key = ?
            LIMIT 1
            """,
            (f"{prefix}:mark",),
        ).fetchone() is not None


def apply_paper_chain_valuation(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    holding_observations: int,
    token_y_quote_per_atomic: float | None = None,
    pool_safe: bool = True,
    emergency_exit: bool = False,
    estimated_exit_cost_quote: float = 0.0,
    config: PositionManagementConfig = PositionManagementConfig(),
) -> AppliedPaperChainValuation:
    valuation = prepare_paper_chain_valuation(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        token_y_quote_per_atomic=token_y_quote_per_atomic,
    )
    prefix = f"paper-chain:{position_id}:{observed_at}"

    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT status
            FROM paper_chain_valuations
            WHERE position_id = ? AND observed_at = ?
            """,
            (position_id, observed_at),
        ).fetchone()
    recovered = _mark_event_exists(storage, prefix)

    if str(row[0]) == "APPLIED":
        position = paper_position_snapshot(storage, position_id=position_id)
        account = paper_account_snapshot(storage, account_id=position.account_id)
        return AppliedPaperChainValuation(
            valuation=valuation,
            executed_action="ALREADY_APPLIED",
            recovered=True,
            position_status=position.status,
            account_equity_quote=account.account_equity_quote,
        )

    if recovered:
        position = paper_position_snapshot(storage, position_id=position_id)
        if position.status == "OPEN":
            policy = evaluate_paper_position_policy(
                storage,
                position_id=position_id,
                active_bin_id=valuation.active_bin_id,
                holding_observations=holding_observations,
                pool_safe=pool_safe,
                estimated_exit_cost_quote=estimated_exit_cost_quote,
                emergency_exit=emergency_exit,
                config=config,
            )
            if policy.decision.action == "EXIT":
                close_paper_position(
                    storage,
                    event_key=f"{prefix}:exit",
                    position_id=position_id,
                    final_mark_quote=valuation.mark_quote,
                    exit_cost_quote=estimated_exit_cost_quote,
                    event_time=observed_at,
                )
                action = "EXIT_RECOVERED"
            elif policy.decision.action == "REBALANCE":
                action = "REBALANCE_PENDING_COST"
            else:
                action = "HOLD_RECOVERED"
        else:
            action = "EXIT_RECOVERED"
    else:
        result = apply_paper_observation(
            storage,
            event_key_prefix=prefix,
            position_id=position_id,
            active_bin_id=valuation.active_bin_id,
            holding_observations=holding_observations,
            mark_quote=valuation.mark_quote,
            fee_delta_quote=valuation.fee_delta_quote,
            reward_delta_quote=valuation.reward_delta_quote,
            pool_safe=pool_safe,
            emergency_exit=emergency_exit,
            estimated_exit_cost_quote=estimated_exit_cost_quote,
            rebalance_cost_quote=None,
            event_time=observed_at,
            config=config,
        )
        action = result.executed_action

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE paper_chain_valuations
            SET status = 'APPLIED'
            WHERE position_id = ? AND observed_at = ?
            """,
            (position_id, observed_at),
        )

    position = paper_position_snapshot(storage, position_id=position_id)
    account = paper_account_snapshot(storage, account_id=position.account_id)
    return AppliedPaperChainValuation(
        valuation=valuation,
        executed_action=action,
        recovered=recovered,
        position_status=position.status,
        account_equity_quote=account.account_equity_quote,
    )
