from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from statistics import fmean, median
from typing import Any

from .research_store import ResearchStore


BPS = Decimal("10000")
SUPPORTED_ACTIONS = ("ENTER", "REBALANCE", "EXIT", "CLOSE")


def _d(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("live action cost evidence contains a non-finite value")
    return parsed


def _fmt(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class LiveActionCostSample:
    position_address: str
    decision_id: str
    signature: str
    pool_address: str
    action: str
    event_time: str
    quote_unit: str
    entry_outflow_quote: str
    composition_cost_quote: str
    network_cost_quote: str
    direct_cost_quote: str
    composition_cost_bps: float
    network_cost_bps: float
    direct_cost_bps: float
    fee_income_quote: str
    reward_income_quote: str
    net_cashflow_quote: str
    valuation_max_age_seconds: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveActionCostSummary:
    action: str
    samples: int
    positions: int
    pools: int
    mean_direct_cost_bps: float
    median_direct_cost_bps: float
    mean_composition_cost_bps: float
    mean_network_cost_bps: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LivePoolActionCostSummary:
    pool_address: str
    action: str
    samples: int
    mean_direct_cost_bps: float
    median_direct_cost_bps: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveActionCostEvidenceReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    transition_pairs_inferred: bool
    samples_seen: int
    positions_seen: int
    pools_seen: int
    actions_seen: tuple[str, ...]
    action_summaries: tuple[LiveActionCostSummary, ...]
    pool_action_summaries: tuple[LivePoolActionCostSummary, ...]
    samples: tuple[LiveActionCostSample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _evidence_for_decision(
    raw: Any,
    decision_id: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(
            "live position valuation quote_evidence_json is invalid"
        ) from exc
    if not isinstance(payload, list):
        raise ValueError(
            "live position valuation quote evidence must be a list"
        )
    matches = [
        item
        for item in payload
        if isinstance(item, dict)
        and str(item.get("decision_id", "")) == decision_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one quote evidence item for decision {decision_id}"
        )
    return matches[0]


def _sample(row: dict[str, Any]) -> LiveActionCostSample:
    action = str(row["action"])
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported live lifecycle action: {action}")

    entry_outflow = _d(row["entry_outflow_quote"])
    if entry_outflow <= 0:
        raise ValueError(
            "live action cost evidence requires positive entry_outflow_quote"
        )

    decision_id = str(row["decision_id"])
    evidence = _evidence_for_decision(
        row["quote_evidence_json"],
        decision_id,
    )
    composition = _d(evidence.get("composition_cost_quote", "0"))
    network = _d(evidence.get("network_cost_quote", "0"))
    fees = _d(evidence.get("fee_income_quote", "0"))
    rewards = _d(evidence.get("reward_income_quote", "0"))
    net_cashflow = _d(evidence.get("net_cashflow_quote", "0"))
    if composition < 0 or network < 0:
        raise ValueError(
            "live action direct costs cannot be negative"
        )

    direct = composition + network
    return LiveActionCostSample(
        position_address=str(row["position_address"]),
        decision_id=decision_id,
        signature=str(row["signature"]),
        pool_address=str(row["pool_address"]),
        action=action,
        event_time=str(row["event_time"]),
        quote_unit=str(row["quote_unit"]),
        entry_outflow_quote=_fmt(entry_outflow),
        composition_cost_quote=_fmt(composition),
        network_cost_quote=_fmt(network),
        direct_cost_quote=_fmt(direct),
        composition_cost_bps=float(
            composition * BPS / entry_outflow
        ),
        network_cost_bps=float(
            network * BPS / entry_outflow
        ),
        direct_cost_bps=float(
            direct * BPS / entry_outflow
        ),
        fee_income_quote=_fmt(fees),
        reward_income_quote=_fmt(rewards),
        net_cashflow_quote=_fmt(net_cashflow),
        valuation_max_age_seconds=int(row["max_age_seconds"]),
    )


def _action_summaries(
    samples: tuple[LiveActionCostSample, ...],
) -> tuple[LiveActionCostSummary, ...]:
    output: list[LiveActionCostSummary] = []
    for action in sorted({item.action for item in samples}):
        rows = [item for item in samples if item.action == action]
        direct = [item.direct_cost_bps for item in rows]
        output.append(
            LiveActionCostSummary(
                action=action,
                samples=len(rows),
                positions=len({item.position_address for item in rows}),
                pools=len({item.pool_address for item in rows}),
                mean_direct_cost_bps=float(fmean(direct)),
                median_direct_cost_bps=float(median(direct)),
                mean_composition_cost_bps=float(
                    fmean(item.composition_cost_bps for item in rows)
                ),
                mean_network_cost_bps=float(
                    fmean(item.network_cost_bps for item in rows)
                ),
            )
        )
    return tuple(output)


def _pool_action_summaries(
    samples: tuple[LiveActionCostSample, ...],
) -> tuple[LivePoolActionCostSummary, ...]:
    keys = sorted(
        {(item.pool_address, item.action) for item in samples}
    )
    output: list[LivePoolActionCostSummary] = []
    for pool, action in keys:
        rows = [
            item
            for item in samples
            if item.pool_address == pool and item.action == action
        ]
        direct = [item.direct_cost_bps for item in rows]
        output.append(
            LivePoolActionCostSummary(
                pool_address=pool,
                action=action,
                samples=len(rows),
                mean_direct_cost_bps=float(fmean(direct)),
                median_direct_cost_bps=float(median(direct)),
            )
        )
    return tuple(output)


def build_live_action_cost_evidence(
    database_path: str,
) -> LiveActionCostEvidenceReport:
    """
    Summarize quote-backed direct costs for valued LIVE lifecycle actions.

    The report deliberately does not pair an EXIT with a later ENTER because
    the current schema has no explicit transition relationship. It therefore
    cannot claim an observed switch cost yet.
    """
    rows = ResearchStore(database_path).live_valued_action_rows()
    samples = tuple(_sample(row) for row in rows)

    return LiveActionCostEvidenceReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        transition_pairs_inferred=False,
        samples_seen=len(samples),
        positions_seen=len(
            {item.position_address for item in samples}
        ),
        pools_seen=len({item.pool_address for item in samples}),
        actions_seen=tuple(
            sorted({item.action for item in samples})
        ),
        action_summaries=_action_summaries(samples),
        pool_action_summaries=_pool_action_summaries(samples),
        samples=samples,
    )
