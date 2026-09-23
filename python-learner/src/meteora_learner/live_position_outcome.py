from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class LivePositionOutcome:
    position_address: str
    pool_address: str
    opened_decision_id: str
    closed_decision_id: str
    execution_count: int
    token_x_wallet_delta_atomic: int
    token_y_wallet_delta_atomic: int
    composition_fee_x_atomic: int
    composition_fee_y_atomic: int
    earned_fee_x_atomic: int
    earned_fee_y_atomic: int
    reward_one_atomic: int
    reward_two_atomic: int
    network_fee_lamports: int
    label_status: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LivePositionOutcomeBuildResult:
    outcome: LivePositionOutcome
    reused_existing: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.to_record(),
            "reused_existing": self.reused_existing,
        }


def _sum_int(rows: list[Any], index: int) -> int:
    return sum(int(str(row[index])) for row in rows)


def build_live_position_outcome(
    storage: Storage,
    *,
    position_address: str,
) -> LivePositionOutcomeBuildResult:
    if not position_address.strip():
        raise ValueError("position_address is required")

    with storage.connect() as conn:
        position = conn.execute(
            """
            SELECT pool_address, status, opened_decision_id,
                   closed_decision_id, last_observed_at
            FROM live_positions
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if position is None:
            raise ValueError(f"unknown live position: {position_address}")
        (
            pool_address,
            status,
            opened_decision_id,
            closed_decision_id,
            closed_observed_at,
        ) = position
        if str(status) != "CLOSED":
            raise ValueError(
                "live position outcome requires CLOSED position state"
            )
        if closed_decision_id is None:
            raise ValueError(
                "closed live position is missing settlement decision"
            )

        effects = conn.execute(
            """
            SELECT decision_id,
                   token_x_wallet_delta_atomic,
                   token_y_wallet_delta_atomic,
                   composition_fee_x_atomic,
                   composition_fee_y_atomic,
                   earned_fee_x_atomic,
                   earned_fee_y_atomic,
                   reward_one_atomic,
                   reward_two_atomic,
                   network_fee_lamports
            FROM live_execution_effects
            WHERE position_address = ?
            ORDER BY observed_at ASC, decision_id ASC
            """,
            (position_address,),
        ).fetchall()
        if not effects:
            raise ValueError(
                "closed live position has no execution effects"
            )
        decisions = {str(row[0]) for row in effects}
        if str(opened_decision_id) not in decisions:
            raise ValueError(
                "closed live position outcome is missing ENTER execution effect"
            )

        effect_fees: list[int] = []
        for row in effects:
            if row[9] is None:
                raise ValueError(
                    "live position outcome requires network fee on every execution effect"
                )
            effect_fees.append(int(row[9]))

        closure = conn.execute(
            """
            SELECT signature
            FROM live_position_closure_proofs
            WHERE decision_id = ? AND position_address = ? AND closed = 1
            """,
            (str(closed_decision_id), position_address),
        ).fetchone()
        if closure is None:
            raise ValueError(
                "closed live position is missing persisted closure proof"
            )
        closure_receipt = conn.execute(
            """
            SELECT network_fee_lamports
            FROM live_execution_receipts
            WHERE decision_id = ?
              AND intent_status = 'CONFIRMED'
              AND succeeded = 1
            """,
            (str(closed_decision_id),),
        ).fetchone()
        if closure_receipt is None or closure_receipt[0] is None:
            raise ValueError(
                "settlement receipt is missing exact network fee"
            )

        settlement_is_effect = str(closed_decision_id) in decisions
        network_fee_lamports = sum(effect_fees)
        execution_count = len(effects)
        if not settlement_is_effect:
            network_fee_lamports += int(closure_receipt[0])
            execution_count += 1

        outcome = LivePositionOutcome(
            position_address=position_address,
            pool_address=str(pool_address),
            opened_decision_id=str(opened_decision_id),
            closed_decision_id=str(closed_decision_id),
            execution_count=execution_count,
            token_x_wallet_delta_atomic=_sum_int(effects, 1),
            token_y_wallet_delta_atomic=_sum_int(effects, 2),
            composition_fee_x_atomic=_sum_int(effects, 3),
            composition_fee_y_atomic=_sum_int(effects, 4),
            earned_fee_x_atomic=_sum_int(effects, 5),
            earned_fee_y_atomic=_sum_int(effects, 6),
            reward_one_atomic=_sum_int(effects, 7),
            reward_two_atomic=_sum_int(effects, 8),
            network_fee_lamports=network_fee_lamports,
            label_status="ATOMIC_ONLY",
        )
        canonical = json.dumps(
            outcome.to_record(),
            sort_keys=True,
            separators=(",", ":"),
        )

        existing = conn.execute(
            """
            SELECT raw_json
            FROM live_position_outcomes
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != canonical:
                raise ValueError(
                    "position already has a different immutable live outcome"
                )
            return LivePositionOutcomeBuildResult(
                outcome=outcome,
                reused_existing=True,
            )

        conn.execute(
            """
            INSERT INTO live_position_outcomes(
                position_address, pool_address,
                opened_decision_id, closed_decision_id,
                execution_count,
                token_x_wallet_delta_atomic,
                token_y_wallet_delta_atomic,
                composition_fee_x_atomic,
                composition_fee_y_atomic,
                earned_fee_x_atomic, earned_fee_y_atomic,
                reward_one_atomic, reward_two_atomic,
                network_fee_lamports, label_status,
                created_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.position_address,
                outcome.pool_address,
                outcome.opened_decision_id,
                outcome.closed_decision_id,
                outcome.execution_count,
                str(outcome.token_x_wallet_delta_atomic),
                str(outcome.token_y_wallet_delta_atomic),
                str(outcome.composition_fee_x_atomic),
                str(outcome.composition_fee_y_atomic),
                str(outcome.earned_fee_x_atomic),
                str(outcome.earned_fee_y_atomic),
                str(outcome.reward_one_atomic),
                str(outcome.reward_two_atomic),
                outcome.network_fee_lamports,
                outcome.label_status,
                str(closed_observed_at or utc_now_iso()),
                canonical,
            ),
        )

    return LivePositionOutcomeBuildResult(
        outcome=outcome,
        reused_existing=False,
    )
