from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class LiveExecutionEffect:
    decision_id: str
    signature: str
    action: str
    pool_address: str
    position_address: str | None
    token_x_wallet_delta_atomic: int
    token_y_wallet_delta_atomic: int
    composition_fee_x_atomic: int
    composition_fee_y_atomic: int
    earned_fee_x_atomic: int
    earned_fee_y_atomic: int
    reward_one_atomic: int
    reward_two_atomic: int
    network_fee_lamports: int | None
    chain_event_count: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveExecutionEffectApplyResult:
    effect: LiveExecutionEffect
    reused_existing: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "effect": self.effect.to_record(),
            "reused_existing": self.reused_existing,
        }


def _int(value: Any) -> int:
    if value is None:
        return 0
    result = int(str(value))
    if result < 0:
        raise ValueError("chain execution amount cannot be negative")
    return result


def _consistent_position(
    current: str | None,
    candidate: Any,
) -> str | None:
    if candidate is None:
        return current
    candidate = str(candidate)
    if not candidate:
        return current
    if current is not None and current != candidate:
        raise ValueError(
            "one execution receipt contains effects for multiple positions"
        )
    return candidate


def _load_reconciled_inputs(
    storage: Storage,
    decision_id: str,
) -> tuple[Any, Any, list[Any], int, int]:
    with storage.connect() as conn:
        receipt = conn.execute(
            """
            SELECT signature, observed_at, action, pool_address,
                   intent_status, slot, network_fee_lamports,
                   compute_units_consumed, succeeded,
                   event_count, add_request_count,
                   rebalance_request_count
            FROM live_execution_receipts
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        if receipt is None:
            raise ValueError(
                f"unknown live execution receipt decision_id: {decision_id}"
            )

        snapshot = conn.execute(
            """
            SELECT slot, network_fee_lamports,
                   compute_units_consumed, succeeded
            FROM chain_transaction_snapshots
            WHERE signature = ?
            """,
            (str(receipt[0]),),
        ).fetchone()
        if snapshot is None:
            raise ValueError(
                "execution effect requires a stored chain transaction snapshot"
            )

        events = conn.execute(
            """
            SELECT event_type, lb_pair, position_address,
                   amount_x, amount_y,
                   token_x_fee_amount, token_y_fee_amount,
                   x_withdrawn_amount, x_added_amount,
                   y_withdrawn_amount, y_added_amount,
                   x_fee_amount, y_fee_amount,
                   reward_one, reward_two
            FROM chain_transaction_events
            WHERE signature = ?
            ORDER BY event_index ASC
            """,
            (str(receipt[0]),),
        ).fetchall()
        add_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM chain_add_liquidity_requests
                WHERE signature = ?
                """,
                (str(receipt[0]),),
            ).fetchone()[0]
        )
        rebalance_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM chain_rebalance_requests
                WHERE signature = ?
                """,
                (str(receipt[0]),),
            ).fetchone()[0]
        )

    (
        _signature,
        _observed_at,
        _action,
        _pool,
        _status,
        receipt_slot,
        receipt_fee,
        receipt_compute,
        receipt_succeeded,
        expected_events,
        expected_adds,
        expected_rebalances,
    ) = receipt
    (
        snapshot_slot,
        snapshot_fee,
        snapshot_compute,
        snapshot_succeeded,
    ) = snapshot

    if int(snapshot_slot) != int(receipt_slot):
        raise ValueError("execution receipt slot does not match chain snapshot")
    if snapshot_succeeded is None or int(snapshot_succeeded) != int(
        receipt_succeeded
    ):
        raise ValueError(
            "execution receipt outcome does not match chain snapshot"
        )
    if receipt_fee is not None and (
        snapshot_fee is None or int(snapshot_fee) != int(receipt_fee)
    ):
        raise ValueError(
            "execution receipt network fee does not match chain snapshot"
        )
    if receipt_compute is not None and (
        snapshot_compute is None
        or int(snapshot_compute) != int(receipt_compute)
    ):
        raise ValueError(
            "execution receipt compute usage does not match chain snapshot"
        )
    if len(events) != int(expected_events):
        raise ValueError(
            "decoded chain event count does not match execution receipt"
        )
    if add_count != int(expected_adds):
        raise ValueError(
            "add-liquidity request count does not match execution receipt"
        )
    if rebalance_count != int(expected_rebalances):
        raise ValueError(
            "rebalance request count does not match execution receipt"
        )

    return receipt, snapshot, events, add_count, rebalance_count


def derive_live_execution_effect(
    storage: Storage,
    decision_id: str,
) -> LiveExecutionEffect:
    if not decision_id.strip():
        raise ValueError("decision_id is required")

    receipt, _snapshot, events, _add_count, _rebalance_count = (
        _load_reconciled_inputs(storage, decision_id)
    )
    (
        signature,
        _observed_at,
        action,
        pool_address,
        intent_status,
        _slot,
        network_fee_lamports,
        _compute,
        succeeded,
        _event_count,
        _add_requests,
        _rebalance_requests,
    ) = receipt
    action = str(action)
    pool_address = str(pool_address)

    if str(intent_status) == "FAILED":
        if int(succeeded) != 0:
            raise ValueError("FAILED receipt unexpectedly succeeded")
        if events:
            raise ValueError(
                "failed execution cannot mutate live balances through decoded events"
            )
        return LiveExecutionEffect(
            decision_id=decision_id,
            signature=str(signature),
            action=action,
            pool_address=pool_address,
            position_address=None,
            token_x_wallet_delta_atomic=0,
            token_y_wallet_delta_atomic=0,
            composition_fee_x_atomic=0,
            composition_fee_y_atomic=0,
            earned_fee_x_atomic=0,
            earned_fee_y_atomic=0,
            reward_one_atomic=0,
            reward_two_atomic=0,
            network_fee_lamports=(
                int(network_fee_lamports)
                if network_fee_lamports is not None
                else None
            ),
            chain_event_count=0,
        )

    if str(intent_status) != "CONFIRMED" or int(succeeded) != 1:
        raise ValueError(
            "live execution effect requires CONFIRMED succeeded receipt"
        )

    position: str | None = None
    wallet_x = 0
    wallet_y = 0
    composition_x = 0
    composition_y = 0
    earned_fee_x = 0
    earned_fee_y = 0
    reward_one = 0
    reward_two = 0
    action_events = 0

    for event in events:
        (
            event_type,
            event_pool,
            event_position,
            amount_x,
            amount_y,
            token_x_fee,
            token_y_fee,
            x_withdrawn,
            x_added,
            y_withdrawn,
            y_added,
            x_fee,
            y_fee,
            reward_1,
            reward_2,
        ) = event

        if event_type == "CompositionFee":
            composition_x += _int(token_x_fee)
            composition_y += _int(token_y_fee)
            continue

        if event_pool is not None and str(event_pool) != pool_address:
            raise ValueError(
                "decoded execution event pool does not match receipt pool"
            )
        position = _consistent_position(position, event_position)

        if action == "ENTER" and event_type == "AddLiquidity":
            wallet_x -= _int(amount_x)
            wallet_y -= _int(amount_y)
            action_events += 1
        elif action == "REBALANCE" and event_type == "Rebalancing":
            wallet_x += _int(x_withdrawn) - _int(x_added)
            wallet_y += _int(y_withdrawn) - _int(y_added)
            earned_fee_x += _int(x_fee)
            earned_fee_y += _int(y_fee)
            reward_one += _int(reward_1)
            reward_two += _int(reward_2)
            action_events += 1
        elif action == "EXIT" and event_type == "RemoveLiquidity":
            wallet_x += _int(amount_x)
            wallet_y += _int(amount_y)
            action_events += 1
        else:
            raise ValueError(
                f"unexpected decoded event {event_type} for LIVE action {action}"
            )

    if action not in {"ENTER", "REBALANCE", "EXIT"}:
        raise ValueError(f"unsupported LIVE execution action: {action}")
    if action_events == 0:
        raise ValueError(
            f"confirmed {action} receipt has no matching decoded chain event"
        )
    if position is None:
        raise ValueError("confirmed live effect is missing a position address")

    return LiveExecutionEffect(
        decision_id=decision_id,
        signature=str(signature),
        action=action,
        pool_address=pool_address,
        position_address=position,
        token_x_wallet_delta_atomic=wallet_x,
        token_y_wallet_delta_atomic=wallet_y,
        composition_fee_x_atomic=composition_x,
        composition_fee_y_atomic=composition_y,
        earned_fee_x_atomic=earned_fee_x,
        earned_fee_y_atomic=earned_fee_y,
        reward_one_atomic=reward_one,
        reward_two_atomic=reward_two,
        network_fee_lamports=(
            int(network_fee_lamports)
            if network_fee_lamports is not None
            else None
        ),
        chain_event_count=len(events),
    )


def apply_live_execution_effect(
    storage: Storage,
    decision_id: str,
) -> LiveExecutionEffectApplyResult:
    effect = derive_live_execution_effect(storage, decision_id)
    with storage.connect() as conn:
        receipt = conn.execute(
            """
            SELECT observed_at
            FROM live_execution_receipts
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        if receipt is None:
            raise ValueError("execution receipt disappeared during effect apply")
        observed_at = str(receipt[0])
        canonical = json.dumps(
            effect.to_record(),
            sort_keys=True,
            separators=(",", ":"),
        )

        existing = conn.execute(
            """
            SELECT raw_json
            FROM live_execution_effects
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != canonical:
                raise ValueError(
                    "decision_id already has a different live execution effect"
                )
            return LiveExecutionEffectApplyResult(
                effect=effect,
                reused_existing=True,
            )

        owner = conn.execute(
            """
            SELECT decision_id
            FROM live_execution_effects
            WHERE signature = ?
            """,
            (effect.signature,),
        ).fetchone()
        if owner is not None:
            raise ValueError(
                "transaction signature is already linked to another live effect"
            )

        conn.execute(
            """
            INSERT INTO live_execution_effects(
                decision_id, signature, observed_at, action,
                pool_address, position_address,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic,
                composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, chain_event_count, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                effect.decision_id,
                effect.signature,
                observed_at,
                effect.action,
                effect.pool_address,
                effect.position_address,
                str(effect.token_x_wallet_delta_atomic),
                str(effect.token_y_wallet_delta_atomic),
                str(effect.composition_fee_x_atomic),
                str(effect.composition_fee_y_atomic),
                str(effect.earned_fee_x_atomic),
                str(effect.earned_fee_y_atomic),
                str(effect.reward_one_atomic),
                str(effect.reward_two_atomic),
                effect.network_fee_lamports,
                effect.chain_event_count,
                canonical,
            ),
        )

    return LiveExecutionEffectApplyResult(
        effect=effect,
        reused_existing=False,
    )
