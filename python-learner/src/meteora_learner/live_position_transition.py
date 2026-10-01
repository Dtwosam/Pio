from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from typing import Any

from .storage import Storage, utc_now_iso


ANNOTATION_SOURCE = "EXPLICIT_RESEARCH_REVIEW"


@dataclass(frozen=True)
class LivePositionTransition:
    transition_id: str
    previous_position_address: str
    next_position_address: str
    previous_pool_address: str
    next_pool_address: str
    previous_exit_decision_id: str
    next_enter_decision_id: str
    previous_exit_at: str
    next_enter_at: str
    transition_kind: str
    annotation_source: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LivePositionTransitionRecordResult:
    transition: LivePositionTransition
    reused_existing: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "transition": self.transition.to_record(),
            "reused_existing": self.reused_existing,
        }


def _timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def _position_row(
    conn: Any,
    position_address: str,
    *,
    label: str,
) -> Any:
    row = conn.execute(
        """
        SELECT pool_address, status, opened_decision_id, opened_at,
               closed_decision_id
        FROM live_positions
        WHERE position_address = ?
        """,
        (position_address,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown {label} live position")
    return row


def _single_event(
    conn: Any,
    position_address: str,
    action: str,
) -> tuple[str, str]:
    rows = conn.execute(
        """
        SELECT decision_id, event_time
        FROM live_position_events
        WHERE position_address = ? AND action = ?
        ORDER BY event_time ASC, decision_id ASC
        """,
        (position_address, action),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"live transition requires exactly one {action} event "
            f"for {position_address}"
        )
    return str(rows[0][0]), str(rows[0][1])


def _transition_id(
    previous_position_address: str,
    next_position_address: str,
) -> str:
    payload = json.dumps(
        {
            "previous_position_address": previous_position_address,
            "next_position_address": next_position_address,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_live_position_transition(
    storage: Storage,
    *,
    previous_position_address: str,
    next_position_address: str,
) -> LivePositionTransitionRecordResult:
    """
    Persist an explicit research-reviewed relationship between two LIVE positions.

    This function never infers a transition from temporal adjacency. Both
    position addresses must be supplied explicitly, and their persisted
    lifecycle must prove that the predecessor EXIT happened strictly before the
    successor ENTER.
    """
    previous = previous_position_address.strip()
    next_position = next_position_address.strip()
    if not previous or not next_position:
        raise ValueError("both live transition position addresses are required")
    if previous == next_position:
        raise ValueError("live transition positions must be different")

    with storage.connect() as conn:
        previous_row = _position_row(
            conn,
            previous,
            label="previous",
        )
        next_row = _position_row(
            conn,
            next_position,
            label="next",
        )

        previous_pool = str(previous_row[0])
        previous_status = str(previous_row[1])
        previous_closed_decision = previous_row[4]
        if previous_status != "CLOSED":
            raise ValueError(
                "previous live transition position must be CLOSED"
            )
        if previous_closed_decision is None:
            raise ValueError(
                "previous live transition position is missing closed_decision_id"
            )

        next_pool = str(next_row[0])
        next_status = str(next_row[1])
        if next_status not in {"OPEN", "LIQUIDITY_REMOVED", "CLOSED"}:
            raise ValueError("next live transition position status is invalid")

        previous_exit_decision, previous_exit_at = _single_event(
            conn,
            previous,
            "EXIT",
        )
        next_enter_decision, next_enter_at = _single_event(
            conn,
            next_position,
            "ENTER",
        )

        if previous_exit_decision != str(previous_closed_decision):
            raise ValueError(
                "previous transition EXIT decision differs from closed position"
            )
        if next_enter_decision != str(next_row[2]):
            raise ValueError(
                "next transition ENTER decision differs from opened position"
            )
        if str(next_row[3]) != next_enter_at:
            raise ValueError(
                "next transition ENTER timestamp differs from opened position"
            )

        previous_time = _timestamp(
            previous_exit_at,
            label="previous transition EXIT time",
        )
        next_time = _timestamp(
            next_enter_at,
            label="next transition ENTER time",
        )
        if next_time <= previous_time:
            raise ValueError(
                "next transition ENTER must occur after previous EXIT"
            )

        kind = (
            "SAME_POOL_REENTRY"
            if previous_pool == next_pool
            else "POOL_SWITCH"
        )
        transition = LivePositionTransition(
            transition_id=_transition_id(previous, next_position),
            previous_position_address=previous,
            next_position_address=next_position,
            previous_pool_address=previous_pool,
            next_pool_address=next_pool,
            previous_exit_decision_id=previous_exit_decision,
            next_enter_decision_id=next_enter_decision,
            previous_exit_at=previous_exit_at,
            next_enter_at=next_enter_at,
            transition_kind=kind,
            annotation_source=ANNOTATION_SOURCE,
        )
        canonical = json.dumps(
            transition.to_record(),
            sort_keys=True,
            separators=(",", ":"),
        )

        existing = conn.execute(
            """
            SELECT transition_id, raw_json
            FROM live_position_transitions
            WHERE previous_position_address = ?
               OR next_position_address = ?
            ORDER BY transition_id ASC
            """,
            (previous, next_position),
        ).fetchall()
        if existing:
            if (
                len(existing) == 1
                and str(existing[0][0]) == transition.transition_id
                and str(existing[0][1]) == canonical
            ):
                return LivePositionTransitionRecordResult(
                    transition=transition,
                    reused_existing=True,
                )
            raise ValueError(
                "live position is already linked to a different transition"
            )

        conn.execute(
            """
            INSERT INTO live_position_transitions(
                transition_id,
                previous_position_address,
                next_position_address,
                previous_pool_address,
                next_pool_address,
                previous_exit_decision_id,
                next_enter_decision_id,
                previous_exit_at,
                next_enter_at,
                transition_kind,
                annotation_source,
                created_at,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transition.transition_id,
                transition.previous_position_address,
                transition.next_position_address,
                transition.previous_pool_address,
                transition.next_pool_address,
                transition.previous_exit_decision_id,
                transition.next_enter_decision_id,
                transition.previous_exit_at,
                transition.next_enter_at,
                transition.transition_kind,
                transition.annotation_source,
                utc_now_iso(),
                canonical,
            ),
        )

    return LivePositionTransitionRecordResult(
        transition=transition,
        reused_existing=False,
    )
