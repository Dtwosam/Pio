from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .research_store import ResearchStore


@dataclass(frozen=True)
class LiveTransitionAnnotationCandidate:
    position_address: str
    pool_address: str
    status: str
    decision_id: str
    event_time: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveTransitionAnnotationBacklogReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    transition_pairs_inferred: bool
    pair_suggestions_emitted: bool
    economic_ranking_applied: bool
    existing_links: int
    predecessor_candidates_seen: int
    successor_candidates_seen: int
    predecessor_candidates: tuple[LiveTransitionAnnotationCandidate, ...]
    successor_candidates: tuple[LiveTransitionAnnotationCandidate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def _candidates(
    rows: list[dict[str, Any]],
    *,
    role: str,
) -> tuple[LiveTransitionAnnotationCandidate, ...]:
    output: list[LiveTransitionAnnotationCandidate] = []
    seen: set[str] = set()
    for row in rows:
        position = str(row["position_address"]).strip()
        pool = str(row["pool_address"]).strip()
        decision = str(row["decision_id"]).strip()
        event_time = str(row["event_time"]).strip()
        status = str(row["status"]).strip()
        if not position or not pool or not decision or not event_time:
            raise ValueError(
                f"live transition {role} backlog row is incomplete"
            )
        if position in seen:
            raise ValueError(
                f"live transition {role} backlog has duplicate position"
            )
        seen.add(position)
        _timestamp(
            event_time,
            label=f"live transition {role} event time",
        )

        if role == "predecessor" and status != "CLOSED":
            raise ValueError(
                "live transition predecessor backlog requires CLOSED positions"
            )
        if role == "successor" and status not in {
            "OPEN",
            "LIQUIDITY_REMOVED",
            "CLOSED",
        }:
            raise ValueError(
                "live transition successor backlog position status is invalid"
            )

        output.append(
            LiveTransitionAnnotationCandidate(
                position_address=position,
                pool_address=pool,
                status=status,
                decision_id=decision,
                event_time=event_time,
            )
        )
    return tuple(output)


def build_live_transition_annotation_backlog(
    database_path: str,
) -> LiveTransitionAnnotationBacklogReport:
    """
    Report unlinked LIVE transition annotation candidates without pairing them.

    The two lists are deliberately independent. Chronological ordering is only
    for deterministic review; it is not a transition inference, pair
    suggestion, opportunity score, or switching recommendation.
    """
    store = ResearchStore(database_path)
    predecessors = _candidates(
        store.live_unlinked_transition_predecessor_rows(),
        role="predecessor",
    )
    successors = _candidates(
        store.live_unlinked_transition_successor_rows(),
        role="successor",
    )
    return LiveTransitionAnnotationBacklogReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        transition_pairs_inferred=False,
        pair_suggestions_emitted=False,
        economic_ranking_applied=False,
        existing_links=store.live_position_transition_count(),
        predecessor_candidates_seen=len(predecessors),
        successor_candidates_seen=len(successors),
        predecessor_candidates=predecessors,
        successor_candidates=successors,
    )
