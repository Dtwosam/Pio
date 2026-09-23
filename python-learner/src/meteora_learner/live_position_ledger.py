from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class LivePositionMutation:
    decision_id: str
    signature: str
    action: str
    position_address: str
    prior_status: str | None
    next_status: str
    min_bin_id: int | None
    max_bin_id: int | None
    reused_existing: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _load_effect(conn: Any, decision_id: str) -> Any:
    row = conn.execute(
        """
        SELECT signature, observed_at, action, pool_address,
               position_address, raw_json
        FROM live_execution_effects
        WHERE decision_id = ?
        """,
        (decision_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            "live position mutation requires an applied live execution effect"
        )
    if row[4] is None:
        raise ValueError(
            "failed/no-position execution effect cannot mutate live positions"
        )
    return row


def _enter_range(conn: Any, signature: str) -> tuple[int, int]:
    rows = conn.execute(
        """
        SELECT min_bin_id, max_bin_id
        FROM chain_add_liquidity_requests
        WHERE signature = ?
        ORDER BY instruction_index ASC
        """,
        (signature,),
    ).fetchall()
    qualified = [
        (int(row[0]), int(row[1]))
        for row in rows
        if row[0] is not None and row[1] is not None
    ]
    if len(qualified) != 1:
        raise ValueError(
            "ENTER live position mutation requires exactly one decoded "
            "add-liquidity request with min/max bin range"
        )
    return qualified[0]


def _rebalance_range(
    conn: Any,
    signature: str,
    position_address: str,
) -> tuple[int, int]:
    rows = conn.execute(
        """
        SELECT new_min_id, new_max_id
        FROM chain_transaction_events
        WHERE signature = ?
          AND event_type = 'Rebalancing'
          AND position_address = ?
        ORDER BY event_index ASC
        """,
        (signature, position_address),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            "REBALANCE live position mutation requires exactly one "
            "Rebalancing event for the position"
        )
    if rows[0][0] is None or rows[0][1] is None:
        raise ValueError("Rebalancing event is missing new range")
    return int(rows[0][0]), int(rows[0][1])


def apply_live_position_effect(
    storage: Storage,
    decision_id: str,
) -> LivePositionMutation:
    if not decision_id.strip():
        raise ValueError("decision_id is required")

    with storage.connect() as conn:
        effect = _load_effect(conn, decision_id)
        signature = str(effect[0])
        observed_at = str(effect[1])
        action = str(effect[2])
        pool_address = str(effect[3])
        position_address = str(effect[4])

        existing_event = conn.execute(
            """
            SELECT action, prior_status, next_status,
                   min_bin_id, max_bin_id, raw_json
            FROM live_position_events
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        if existing_event is not None:
            return LivePositionMutation(
                decision_id=decision_id,
                signature=signature,
                action=str(existing_event[0]),
                position_address=position_address,
                prior_status=(
                    str(existing_event[1])
                    if existing_event[1] is not None
                    else None
                ),
                next_status=str(existing_event[2]),
                min_bin_id=(
                    int(existing_event[3])
                    if existing_event[3] is not None
                    else None
                ),
                max_bin_id=(
                    int(existing_event[4])
                    if existing_event[4] is not None
                    else None
                ),
                reused_existing=True,
            )

        current = conn.execute(
            """
            SELECT pool_address, status, min_bin_id, max_bin_id,
                   opened_decision_id
            FROM live_positions
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()

        prior_status: str | None = (
            str(current[1]) if current is not None else None
        )
        min_bin: int | None
        max_bin: int | None

        if action == "ENTER":
            if current is not None:
                raise ValueError(
                    "ENTER cannot reuse an existing live position address"
                )
            min_bin, max_bin = _enter_range(conn, signature)
            next_status = "OPEN"
        elif action == "REBALANCE":
            if current is None or prior_status != "OPEN":
                raise ValueError(
                    "REBALANCE requires an existing OPEN live position"
                )
            if str(current[0]) != pool_address:
                raise ValueError(
                    "REBALANCE receipt pool differs from live position pool"
                )
            min_bin, max_bin = _rebalance_range(
                conn,
                signature,
                position_address,
            )
            next_status = "OPEN"
        elif action == "EXIT":
            if current is None or prior_status != "OPEN":
                raise ValueError(
                    "EXIT requires an existing OPEN live position"
                )
            if str(current[0]) != pool_address:
                raise ValueError(
                    "EXIT receipt pool differs from live position pool"
                )
            min_bin = int(current[2]) if current[2] is not None else None
            max_bin = int(current[3]) if current[3] is not None else None
            next_status = "LIQUIDITY_REMOVED"
        else:
            raise ValueError(
                f"unsupported live position mutation action: {action}"
            )

        event_record = {
            "decision_id": decision_id,
            "signature": signature,
            "action": action,
            "position_address": position_address,
            "pool_address": pool_address,
            "prior_status": prior_status,
            "next_status": next_status,
            "min_bin_id": min_bin,
            "max_bin_id": max_bin,
        }
        raw = json.dumps(
            event_record,
            sort_keys=True,
            separators=(",", ":"),
        )

        if action == "ENTER":
            conn.execute(
                """
                INSERT INTO live_positions(
                    position_address, pool_address, status,
                    opened_decision_id, opened_signature, opened_at,
                    min_bin_id, max_bin_id,
                    last_decision_id, last_signature, last_observed_at,
                    rebalances, raw_json
                ) VALUES (?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    position_address,
                    pool_address,
                    decision_id,
                    signature,
                    observed_at,
                    min_bin,
                    max_bin,
                    decision_id,
                    signature,
                    observed_at,
                    raw,
                ),
            )
        elif action == "REBALANCE":
            conn.execute(
                """
                UPDATE live_positions
                SET min_bin_id = ?, max_bin_id = ?,
                    last_decision_id = ?, last_signature = ?,
                    last_observed_at = ?,
                    rebalances = rebalances + 1,
                    raw_json = ?
                WHERE position_address = ? AND status = 'OPEN'
                """,
                (
                    min_bin,
                    max_bin,
                    decision_id,
                    signature,
                    observed_at,
                    raw,
                    position_address,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE live_positions
                SET status = 'LIQUIDITY_REMOVED',
                    last_decision_id = ?, last_signature = ?,
                    last_observed_at = ?, raw_json = ?
                WHERE position_address = ? AND status = 'OPEN'
                """,
                (
                    decision_id,
                    signature,
                    observed_at,
                    raw,
                    position_address,
                ),
            )

        conn.execute(
            """
            INSERT INTO live_position_events(
                decision_id, signature, position_address, event_time,
                action, prior_status, next_status,
                min_bin_id, max_bin_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                signature,
                position_address,
                observed_at,
                action,
                prior_status,
                next_status,
                min_bin,
                max_bin,
                raw,
            ),
        )

    return LivePositionMutation(
        decision_id=decision_id,
        signature=signature,
        action=action,
        position_address=position_address,
        prior_status=prior_status,
        next_status=next_status,
        min_bin_id=min_bin,
        max_bin_id=max_bin,
        reused_existing=False,
    )
