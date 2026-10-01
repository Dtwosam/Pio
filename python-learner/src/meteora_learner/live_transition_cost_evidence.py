from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from statistics import fmean, median
from typing import Any

from .live_action_cost_evidence import (
    LiveActionCostEvidenceReport,
    LiveActionCostSample,
    build_live_action_cost_evidence,
)
from .research_store import ResearchStore


BPS = Decimal("10000")
EXPLICIT_SOURCE = "EXPLICIT_RESEARCH_REVIEW"


def _d(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError(
            "live transition cost evidence contains a non-finite value"
        )
    return parsed


def _fmt(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


@dataclass(frozen=True)
class LiveTransitionCostSample:
    transition_id: str
    previous_position_address: str
    next_position_address: str
    previous_pool_address: str
    next_pool_address: str
    transition_kind: str
    previous_exit_decision_id: str
    next_enter_decision_id: str
    previous_exit_at: str
    next_enter_at: str
    transition_gap_seconds: float
    quote_unit: str
    previous_entry_outflow_quote: str
    next_entry_outflow_quote: str
    exit_direct_cost_quote: str
    enter_direct_cost_quote: str
    direct_transition_cost_quote: str
    exit_direct_cost_bps_on_previous_entry: float
    enter_direct_cost_bps_on_reentry_capital: float
    direct_transition_cost_bps_on_reentry_capital: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveTransitionCostGap:
    transition_id: str
    previous_position_address: str
    next_position_address: str
    transition_kind: str
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveTransitionCostEvidenceReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    transition_pairs_inferred: bool
    explicit_transition_links_required: bool
    direct_costs_quote_backed: bool
    full_transition_economics_included: bool
    links_seen: int
    samples_seen: int
    gaps_seen: int
    pool_switch_samples: int
    same_pool_reentry_samples: int
    mean_direct_transition_cost_bps_on_reentry_capital: float | None
    median_direct_transition_cost_bps_on_reentry_capital: float | None
    samples: tuple[LiveTransitionCostSample, ...]
    gaps: tuple[LiveTransitionCostGap, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _actions_by_decision(
    report: LiveActionCostEvidenceReport,
) -> dict[str, LiveActionCostSample]:
    result: dict[str, LiveActionCostSample] = {}
    for sample in report.samples:
        if sample.decision_id in result:
            raise ValueError(
                "live action cost evidence has duplicate decision ids"
            )
        result[sample.decision_id] = sample
    return result


def _gap(
    row: dict[str, Any],
    reason: str,
) -> LiveTransitionCostGap:
    return LiveTransitionCostGap(
        transition_id=str(row["transition_id"]),
        previous_position_address=str(
            row["previous_position_address"]
        ),
        next_position_address=str(row["next_position_address"]),
        transition_kind=str(row["transition_kind"]),
        reason=reason,
    )


def _sample(
    row: dict[str, Any],
    *,
    exit_action: LiveActionCostSample,
    enter_action: LiveActionCostSample,
) -> LiveTransitionCostSample:
    transition_id = str(row["transition_id"])
    previous_position = str(row["previous_position_address"])
    next_position = str(row["next_position_address"])
    previous_pool = str(row["previous_pool_address"])
    next_pool = str(row["next_pool_address"])
    transition_kind = str(row["transition_kind"])

    expected_kind = (
        "SAME_POOL_REENTRY"
        if previous_pool == next_pool
        else "POOL_SWITCH"
    )
    if transition_kind != expected_kind:
        raise ValueError(
            "live transition kind differs from persisted pool relationship"
        )
    if str(row["annotation_source"]) != EXPLICIT_SOURCE:
        raise ValueError(
            "live transition cost evidence requires explicit research review"
        )

    if (
        exit_action.position_address != previous_position
        or exit_action.action != "EXIT"
        or exit_action.decision_id
        != str(row["previous_exit_decision_id"])
    ):
        raise ValueError(
            "live transition previous EXIT action binding is invalid"
        )
    if (
        enter_action.position_address != next_position
        or enter_action.action != "ENTER"
        or enter_action.decision_id
        != str(row["next_enter_decision_id"])
    ):
        raise ValueError(
            "live transition next ENTER action binding is invalid"
        )
    if exit_action.pool_address != previous_pool:
        raise ValueError(
            "live transition previous pool differs from EXIT evidence"
        )
    if enter_action.pool_address != next_pool:
        raise ValueError(
            "live transition next pool differs from ENTER evidence"
        )
    if exit_action.quote_unit != enter_action.quote_unit:
        raise ValueError(
            "live transition quote units must match before aggregation"
        )

    previous_exit_at = str(row["previous_exit_at"])
    next_enter_at = str(row["next_enter_at"])
    exit_time = _timestamp(
        previous_exit_at,
        label="live transition EXIT time",
    )
    enter_time = _timestamp(
        next_enter_at,
        label="live transition ENTER time",
    )
    gap_seconds = (enter_time - exit_time).total_seconds()
    if gap_seconds <= 0:
        raise ValueError(
            "live transition ENTER must occur after previous EXIT"
        )

    previous_entry = _d(exit_action.entry_outflow_quote)
    next_entry = _d(enter_action.entry_outflow_quote)
    if previous_entry <= 0 or next_entry <= 0:
        raise ValueError(
            "live transition cost evidence requires positive entry outflows"
        )
    exit_cost = _d(exit_action.direct_cost_quote)
    enter_cost = _d(enter_action.direct_cost_quote)
    if exit_cost < 0 or enter_cost < 0:
        raise ValueError("live transition direct costs cannot be negative")
    total_cost = exit_cost + enter_cost

    return LiveTransitionCostSample(
        transition_id=transition_id,
        previous_position_address=previous_position,
        next_position_address=next_position,
        previous_pool_address=previous_pool,
        next_pool_address=next_pool,
        transition_kind=transition_kind,
        previous_exit_decision_id=str(
            row["previous_exit_decision_id"]
        ),
        next_enter_decision_id=str(row["next_enter_decision_id"]),
        previous_exit_at=previous_exit_at,
        next_enter_at=next_enter_at,
        transition_gap_seconds=float(gap_seconds),
        quote_unit=exit_action.quote_unit,
        previous_entry_outflow_quote=_fmt(previous_entry),
        next_entry_outflow_quote=_fmt(next_entry),
        exit_direct_cost_quote=_fmt(exit_cost),
        enter_direct_cost_quote=_fmt(enter_cost),
        direct_transition_cost_quote=_fmt(total_cost),
        exit_direct_cost_bps_on_previous_entry=float(
            exit_cost * BPS / previous_entry
        ),
        enter_direct_cost_bps_on_reentry_capital=float(
            enter_cost * BPS / next_entry
        ),
        direct_transition_cost_bps_on_reentry_capital=float(
            total_cost * BPS / next_entry
        ),
    )


def build_live_transition_cost_evidence(
    database_path: str,
    *,
    action_cost_evidence: LiveActionCostEvidenceReport | None = None,
) -> LiveTransitionCostEvidenceReport:
    """
    Build comparable direct EXIT + ENTER transition-cost evidence.

    Transition pairs must have been recorded explicitly in
    live_position_transitions. Missing valued action evidence stays visible as a
    categorical gap. Temporal adjacency is never used to infer a relationship.

    The resulting cost covers only quote-backed composition and network costs.
    It does not claim to include complete slippage, market impact, opportunity
    cost, or any learned switching threshold.
    """
    action_report = (
        action_cost_evidence
        if action_cost_evidence is not None
        else build_live_action_cost_evidence(database_path)
    )
    if action_report.transition_pairs_inferred is not False:
        raise ValueError(
            "transition cost evidence refuses inferred action-cost pairs"
        )

    actions = _actions_by_decision(action_report)
    rows = ResearchStore(database_path).live_position_transition_rows()
    samples: list[LiveTransitionCostSample] = []
    gaps: list[LiveTransitionCostGap] = []

    for row in rows:
        exit_action = actions.get(
            str(row["previous_exit_decision_id"])
        )
        if exit_action is None:
            gaps.append(
                _gap(row, "PREVIOUS_EXIT_ACTION_NOT_VALUED")
            )
            continue

        enter_action = actions.get(
            str(row["next_enter_decision_id"])
        )
        if enter_action is None:
            gaps.append(
                _gap(row, "NEXT_ENTER_ACTION_NOT_VALUED")
            )
            continue

        if exit_action.quote_unit != enter_action.quote_unit:
            gaps.append(_gap(row, "QUOTE_UNIT_MISMATCH"))
            continue

        samples.append(
            _sample(
                row,
                exit_action=exit_action,
                enter_action=enter_action,
            )
        )

    costs = [
        item.direct_transition_cost_bps_on_reentry_capital
        for item in samples
    ]
    return LiveTransitionCostEvidenceReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        transition_pairs_inferred=False,
        explicit_transition_links_required=True,
        direct_costs_quote_backed=True,
        full_transition_economics_included=False,
        links_seen=len(rows),
        samples_seen=len(samples),
        gaps_seen=len(gaps),
        pool_switch_samples=sum(
            item.transition_kind == "POOL_SWITCH"
            for item in samples
        ),
        same_pool_reentry_samples=sum(
            item.transition_kind == "SAME_POOL_REENTRY"
            for item in samples
        ),
        mean_direct_transition_cost_bps_on_reentry_capital=(
            float(fmean(costs)) if costs else None
        ),
        median_direct_transition_cost_bps_on_reentry_capital=(
            float(median(costs)) if costs else None
        ),
        samples=tuple(samples),
        gaps=tuple(gaps),
    )
