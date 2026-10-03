from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from statistics import fmean, median
from typing import Any

from .research_store import ResearchStore


BPS = Decimal("10000")
EXPLICIT_SOURCE = "EXPLICIT_RESEARCH_REVIEW"


def _d(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError(
            "live transition entry shortfall contains a non-finite value"
        )
    return parsed


def _fmt(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _atomic(value: Any, *, label: str) -> int:
    parsed = int(str(value))
    if parsed < 0:
        raise ValueError(f"{label} cannot be negative")
    return parsed


@dataclass(frozen=True)
class LiveTransitionEntryShortfallSample:
    transition_id: str
    previous_position_address: str
    next_position_address: str
    previous_pool_address: str
    next_pool_address: str
    transition_kind: str
    next_enter_decision_id: str
    next_enter_signature: str
    quote_unit: str
    requested_x_atomic: int
    requested_y_atomic: int
    actual_x_atomic: int
    actual_y_atomic: int
    requested_value_quote: str
    actual_value_quote: str
    entry_execution_shortfall_quote: str
    entry_execution_shortfall_bps: float
    token_x_quote_per_atomic: str | None
    token_y_quote_per_atomic: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveTransitionEntryShortfallGap:
    transition_id: str
    previous_position_address: str
    next_position_address: str
    transition_kind: str
    reason: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveTransitionEntryShortfallEvidenceReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    transition_pairs_inferred: bool
    explicit_transition_links_required: bool
    successor_enter_only: bool
    request_event_match_required: bool
    quote_backed: bool
    exit_execution_shortfall_included: bool
    market_impact_included: bool
    opportunity_cost_included: bool
    full_transition_economics_included: bool
    links_seen: int
    samples_seen: int
    gaps_seen: int
    mean_entry_execution_shortfall_bps: float | None
    median_entry_execution_shortfall_bps: float | None
    samples: tuple[LiveTransitionEntryShortfallSample, ...]
    gaps: tuple[LiveTransitionEntryShortfallGap, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _gap(
    row: dict[str, Any],
    reason: str,
) -> LiveTransitionEntryShortfallGap:
    return LiveTransitionEntryShortfallGap(
        transition_id=str(row["transition_id"]),
        previous_position_address=str(
            row["previous_position_address"]
        ),
        next_position_address=str(row["next_position_address"]),
        transition_kind=str(row["transition_kind"]),
        reason=reason,
    )


def _quote_evidence_for_decision(
    raw: Any,
    decision_id: str,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(
            "live transition entry shortfall quote evidence is invalid JSON"
        ) from exc
    if not isinstance(payload, list):
        raise ValueError(
            "live transition entry shortfall quote evidence must be a list"
        )
    matches = [
        item
        for item in payload
        if isinstance(item, dict)
        and str(item.get("decision_id", "")) == decision_id
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _side_quote(
    evidence: dict[str, Any],
    *,
    side: str,
    quote_unit: str,
    amount_required: bool,
) -> Decimal | None:
    quotes = evidence.get("quotes")
    if not isinstance(quotes, dict):
        return None
    item = quotes.get(f"principal_{side}")
    if item is None:
        return None if amount_required else Decimal("0")
    if not isinstance(item, dict):
        return None
    if str(item.get("quote_unit", "")) != quote_unit:
        return None
    raw = item.get("quote_per_atomic")
    if raw is None:
        return None
    value = _d(raw)
    if value <= 0:
        return None
    return value


def _sample(
    row: dict[str, Any],
    *,
    enter_row: dict[str, Any],
    event: dict[str, Any],
    request: dict[str, Any],
) -> LiveTransitionEntryShortfallSample | str:
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
            "live transition entry shortfall requires explicit research review"
        )

    next_position = str(row["next_position_address"])
    decision_id = str(row["next_enter_decision_id"])
    if (
        str(enter_row["decision_id"]) != decision_id
        or str(enter_row["position_address"]) != next_position
        or str(enter_row["pool_address"]) != next_pool
        or str(enter_row["action"]) != "ENTER"
    ):
        raise ValueError(
            "live transition successor ENTER binding is invalid"
        )

    requested_x_raw = request.get("requested_amount_x")
    requested_y_raw = request.get("requested_amount_y")
    if requested_x_raw is None or requested_y_raw is None:
        return "ENTER_REQUEST_AMOUNTS_MISSING"

    requested_x = _atomic(
        requested_x_raw,
        label="requested token X amount",
    )
    requested_y = _atomic(
        requested_y_raw,
        label="requested token Y amount",
    )
    actual_x = _atomic(
        event.get("amount_x") or 0,
        label="actual token X amount",
    )
    actual_y = _atomic(
        event.get("amount_y") or 0,
        label="actual token Y amount",
    )
    if actual_x > requested_x or actual_y > requested_y:
        return "ENTER_ACTUAL_EXCEEDS_REQUESTED"
    if requested_x == 0 and requested_y == 0:
        return "ENTER_REQUEST_VALUE_ZERO"

    quote_unit = str(enter_row["quote_unit"])
    quote_evidence = _quote_evidence_for_decision(
        enter_row["quote_evidence_json"],
        decision_id,
    )
    if quote_evidence is None:
        return "ENTER_QUOTE_EVIDENCE_MISSING"

    quote_x = _side_quote(
        quote_evidence,
        side="x",
        quote_unit=quote_unit,
        amount_required=requested_x > 0,
    )
    quote_y = _side_quote(
        quote_evidence,
        side="y",
        quote_unit=quote_unit,
        amount_required=requested_y > 0,
    )
    if requested_x > 0 and quote_x is None:
        return "ENTER_TOKEN_X_QUOTE_MISSING"
    if requested_y > 0 and quote_y is None:
        return "ENTER_TOKEN_Y_QUOTE_MISSING"

    qx = quote_x or Decimal("0")
    qy = quote_y or Decimal("0")
    requested_value = (
        Decimal(requested_x) * qx
        + Decimal(requested_y) * qy
    )
    actual_value = (
        Decimal(actual_x) * qx
        + Decimal(actual_y) * qy
    )
    if requested_value <= 0:
        return "ENTER_REQUEST_VALUE_ZERO"
    if actual_value < 0 or actual_value > requested_value:
        return "ENTER_VALUE_RELATION_INVALID"

    shortfall = requested_value - actual_value
    return LiveTransitionEntryShortfallSample(
        transition_id=str(row["transition_id"]),
        previous_position_address=str(
            row["previous_position_address"]
        ),
        next_position_address=next_position,
        previous_pool_address=previous_pool,
        next_pool_address=next_pool,
        transition_kind=transition_kind,
        next_enter_decision_id=decision_id,
        next_enter_signature=str(enter_row["signature"]),
        quote_unit=quote_unit,
        requested_x_atomic=requested_x,
        requested_y_atomic=requested_y,
        actual_x_atomic=actual_x,
        actual_y_atomic=actual_y,
        requested_value_quote=_fmt(requested_value),
        actual_value_quote=_fmt(actual_value),
        entry_execution_shortfall_quote=_fmt(shortfall),
        entry_execution_shortfall_bps=float(
            shortfall * BPS / requested_value
        ),
        token_x_quote_per_atomic=(
            _fmt(qx) if requested_x > 0 else None
        ),
        token_y_quote_per_atomic=(
            _fmt(qy) if requested_y > 0 else None
        ),
    )


def build_live_transition_entry_shortfall_evidence(
    database_path: str,
) -> LiveTransitionEntryShortfallEvidenceReport:
    """
    Build quote-backed successor-ENTER request-vs-actual shortfall evidence.

    Links are explicit research annotations. Requested liquidity comes from the
    decoded add-liquidity request; actual liquidity comes from the uniquely
    matching decoded AddLiquidity event. Both are valued with the exact quote
    observations already persisted for the successor ENTER valuation.

    This is execution-underfill evidence, not a claim of complete slippage or
    market impact. EXIT execution shortfall, market impact, opportunity cost,
    and any learned switch threshold remain outside this report.
    """
    store = ResearchStore(database_path)
    transitions = store.live_position_transition_rows()
    enter_rows = {
        str(item["decision_id"]): item
        for item in store.live_valued_action_rows()
        if str(item["action"]) == "ENTER"
    }

    samples: list[LiveTransitionEntryShortfallSample] = []
    gaps: list[LiveTransitionEntryShortfallGap] = []

    for row in transitions:
        decision_id = str(row["next_enter_decision_id"])
        enter_row = enter_rows.get(decision_id)
        if enter_row is None:
            gaps.append(_gap(row, "NEXT_ENTER_ACTION_NOT_VALUED"))
            continue

        signature = str(enter_row["signature"])
        next_position = str(row["next_position_address"])
        next_pool = str(row["next_pool_address"])
        events = [
            item
            for item in store.load_transaction_events(signature)
            if str(item.get("event_type", "")) == "AddLiquidity"
            and str(item.get("position_address") or "") == next_position
            and str(item.get("lb_pair") or "") == next_pool
        ]
        if not events:
            gaps.append(_gap(row, "ENTER_ADD_EVENT_MISSING"))
            continue
        if len(events) != 1:
            gaps.append(_gap(row, "ENTER_ADD_EVENT_AMBIGUOUS"))
            continue

        event = events[0]
        parent_ix_raw = event.get("parent_ix_index")
        if parent_ix_raw is None:
            gaps.append(_gap(row, "ENTER_PARENT_INSTRUCTION_MISSING"))
            continue
        request = store.add_liquidity_request(
            signature,
            int(parent_ix_raw),
        )
        if request is None:
            gaps.append(_gap(row, "ENTER_ADD_REQUEST_MISSING"))
            continue

        value = _sample(
            row,
            enter_row=enter_row,
            event=event,
            request=request,
        )
        if isinstance(value, str):
            gaps.append(_gap(row, value))
            continue
        samples.append(value)

    values = [
        item.entry_execution_shortfall_bps
        for item in samples
    ]
    return LiveTransitionEntryShortfallEvidenceReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        transition_pairs_inferred=False,
        explicit_transition_links_required=True,
        successor_enter_only=True,
        request_event_match_required=True,
        quote_backed=True,
        exit_execution_shortfall_included=False,
        market_impact_included=False,
        opportunity_cost_included=False,
        full_transition_economics_included=False,
        links_seen=len(transitions),
        samples_seen=len(samples),
        gaps_seen=len(gaps),
        mean_entry_execution_shortfall_bps=(
            float(fmean(values)) if values else None
        ),
        median_entry_execution_shortfall_bps=(
            float(median(values)) if values else None
        ),
        samples=tuple(samples),
        gaps=tuple(gaps),
    )
