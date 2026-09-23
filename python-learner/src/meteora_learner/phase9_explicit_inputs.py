from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

from .multi_pool_research import (
    PoolResearchInput,
    build_multi_pool_research,
)
from .portfolio_allocation import (
    PortfolioAllocationCriteria,
    persist_portfolio_allocation_research,
    persist_portfolio_candidate_research,
    research_portfolio_allocation,
)
from .static_hedge import (
    HedgeInstrumentAssumptions,
    StaticHedgeCriteria,
    persist_static_hedge_research,
    research_static_inventory_hedge,
)
from .storage import Storage
from .strategy import StrategyType


PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE = (
    "PHASE9_EXPLICIT_RESEARCH_INPUTS_V1"
)
PHASE9_EXPLICIT_INPUTS_SCOPE = "__PHASE9_EXPLICIT_RESEARCH_INPUTS__"


@dataclass(frozen=True)
class Phase9StaticHedgeInput:
    pool_address: str
    amount_x: int
    amount_y: int
    instrument: HedgeInstrumentAssumptions
    criteria: StaticHedgeCriteria
    as_of: str | None


@dataclass(frozen=True)
class Phase9PortfolioResearchConfig:
    account_equity_quote: float
    cash_quote: float
    current_deployed_quote: float
    portfolio_drawdown_bps: int
    observation_limit: int
    half_widths: tuple[int, ...]
    center_offsets: tuple[int, ...]
    strategies: tuple[str, ...]
    max_share_bps: int
    favor_x_in_active_bin: bool
    budget_quote: float
    allocation_criteria: PortfolioAllocationCriteria


@dataclass(frozen=True)
class Phase9ExplicitResearchInputs:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    static_hedges: tuple[Phase9StaticHedgeInput, ...]
    pool_inputs: tuple[PoolResearchInput, ...]
    portfolio: Phase9PortfolioResearchConfig

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9ExplicitInputsArtifact:
    evidence_id: int
    artifact_sha256: str
    inputs: Phase9ExplicitResearchInputs

    def to_record(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "artifact_sha256": self.artifact_sha256,
            "inputs": self.inputs.to_record(),
        }


@dataclass(frozen=True)
class Phase9ExplicitResearchRun:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    input_evidence_id: int
    input_artifact_sha256: str
    static_hedge_reports: tuple[dict[str, Any], ...]
    static_hedge_evidence_ids: tuple[int, ...]
    candidate_evidence_id: int | None
    candidate_evidence_sha256: str | None
    portfolio_allocation: dict[str, Any]
    portfolio_allocation_evidence_id: int | None
    explicit_research_ready: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _candidate_pools(
    storage: Storage,
    *,
    limit: int = 3,
) -> tuple[str, ...]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT pool_address, COUNT(*) AS observations
            FROM chain_pool_snapshots
            WHERE pool_address IS NOT NULL
              AND TRIM(pool_address) != ''
            GROUP BY pool_address
            ORDER BY observations DESC, pool_address ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def build_phase9_explicit_input_template(
    storage: Storage,
    *,
    pool_addresses: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    pools = tuple(
        sorted(
            {
                value.strip()
                for value in (pool_addresses or _candidate_pools(storage))
                if value.strip()
            }
        )
    )
    static_pool = pools[0] if pools else "<POOL>"
    portfolio_pools = pools or ("<POOL_A>", "<POOL_B>")

    return {
        "static_hedges": [
            {
                "pool_address": static_pool,
                "amount_x": None,
                "amount_y": None,
                "instrument": {
                    "instrument_id": None,
                    "venue": None,
                    "available_liquidity_y_atomic": None,
                    "max_liquidity_share_bps": 1000,
                    "max_leverage": 1.0,
                    "funding_bps_per_holding_window": None,
                },
                "criteria": {
                    "observation_limit": 96,
                    "holding_observations": 6,
                    "hedge_fraction": 1.0,
                    "hedge_round_trip_cost_bps": None,
                    "min_windows": 20,
                    "min_mean_abs_return_reduction_bps": 0.0,
                    "min_worst_loss_improvement_bps": 0.0,
                    "max_mean_return_drag_bps": 100.0,
                },
                "as_of": None,
            }
        ],
        "pool_inputs": [
            {
                "pool_address": pool,
                "amount_x": None,
                "amount_y": None,
                "requested_quote": None,
                "network_cost_y_atomic": None,
            }
            for pool in portfolio_pools
        ],
        "portfolio": {
            "account_equity_quote": None,
            "cash_quote": None,
            "current_deployed_quote": None,
            "portfolio_drawdown_bps": None,
            "observation_limit": 12,
            "half_widths": [0, 1, 2, 5, 10],
            "center_offsets": [0],
            "strategies": [
                StrategyType.SPOT.value,
                StrategyType.CURVE.value,
                StrategyType.BID_ASK.value,
            ],
            "max_share_bps": 500,
            "favor_x_in_active_bin": False,
            "budget_quote": None,
            "allocation_criteria": {
                "max_positions": 3,
                "min_positions": 2,
                "max_pool_allocation_bps": 4000,
                "min_range_survival_ratio": 0.75,
                "min_excess_vs_hold_bps": 0,
                "min_position_quote": 10.0,
                "min_budget_utilization_rate": 0.75,
            },
        },
    }


def _required(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise ValueError(f"{path}.{key} is required")
    return mapping[key]


def parse_phase9_explicit_inputs(
    payload: Any,
) -> Phase9ExplicitResearchInputs:
    if not isinstance(payload, dict):
        raise ValueError("Phase 9 explicit inputs must be a JSON object")

    raw_hedges = payload.get("static_hedges")
    if not isinstance(raw_hedges, list) or not raw_hedges:
        raise ValueError("static_hedges must contain at least one entry")

    hedge_inputs: list[Phase9StaticHedgeInput] = []
    for index, raw in enumerate(raw_hedges):
        path = f"static_hedges[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must be an object")
        instrument_raw = _required(raw, "instrument", path)
        criteria_raw = raw.get("criteria") or {}
        if not isinstance(instrument_raw, dict):
            raise ValueError(f"{path}.instrument must be an object")
        if not isinstance(criteria_raw, dict):
            raise ValueError(f"{path}.criteria must be an object")

        instrument = HedgeInstrumentAssumptions(
            instrument_id=str(
                _required(instrument_raw, "instrument_id", f"{path}.instrument")
            ),
            venue=str(
                _required(instrument_raw, "venue", f"{path}.instrument")
            ),
            available_liquidity_y_atomic=float(
                _required(
                    instrument_raw,
                    "available_liquidity_y_atomic",
                    f"{path}.instrument",
                )
            ),
            max_liquidity_share_bps=int(
                instrument_raw.get("max_liquidity_share_bps", 1000)
            ),
            max_leverage=float(instrument_raw.get("max_leverage", 1.0)),
            funding_bps_per_holding_window=float(
                _required(
                    instrument_raw,
                    "funding_bps_per_holding_window",
                    f"{path}.instrument",
                )
            ),
        )
        criteria = StaticHedgeCriteria(
            observation_limit=int(
                criteria_raw.get("observation_limit", 96)
            ),
            holding_observations=int(
                criteria_raw.get("holding_observations", 6)
            ),
            hedge_fraction=float(criteria_raw.get("hedge_fraction", 1.0)),
            hedge_round_trip_cost_bps=float(
                _required(
                    criteria_raw,
                    "hedge_round_trip_cost_bps",
                    f"{path}.criteria",
                )
            ),
            min_windows=int(criteria_raw.get("min_windows", 20)),
            min_mean_abs_return_reduction_bps=float(
                criteria_raw.get(
                    "min_mean_abs_return_reduction_bps",
                    0.0,
                )
            ),
            min_worst_loss_improvement_bps=float(
                criteria_raw.get(
                    "min_worst_loss_improvement_bps",
                    0.0,
                )
            ),
            max_mean_return_drag_bps=float(
                criteria_raw.get("max_mean_return_drag_bps", 100.0)
            ),
        )
        amount_x = int(_required(raw, "amount_x", path))
        amount_y = int(_required(raw, "amount_y", path))
        if amount_x < 0 or amount_y < 0:
            raise ValueError(f"{path} token amounts cannot be negative")
        if amount_x == 0 and amount_y == 0:
            raise ValueError(
                f"{path} requires at least one positive token amount"
            )
        pool = str(_required(raw, "pool_address", path)).strip()
        if not pool:
            raise ValueError(f"{path}.pool_address is required")
        hedge_inputs.append(
            Phase9StaticHedgeInput(
                pool_address=pool,
                amount_x=amount_x,
                amount_y=amount_y,
                instrument=instrument,
                criteria=criteria,
                as_of=(
                    str(raw["as_of"])
                    if raw.get("as_of") is not None
                    else None
                ),
            )
        )

    raw_pool_inputs = payload.get("pool_inputs")
    if not isinstance(raw_pool_inputs, list) or len(raw_pool_inputs) < 2:
        raise ValueError("pool_inputs must contain at least two pools")
    pool_inputs = tuple(
        PoolResearchInput(
            pool_address=str(
                _required(raw, "pool_address", f"pool_inputs[{index}]")
            ),
            amount_x=int(
                _required(raw, "amount_x", f"pool_inputs[{index}]")
            ),
            amount_y=int(
                _required(raw, "amount_y", f"pool_inputs[{index}]")
            ),
            requested_quote=float(
                _required(
                    raw,
                    "requested_quote",
                    f"pool_inputs[{index}]",
                )
            ),
            network_cost_y_atomic=int(
                _required(
                    raw,
                    "network_cost_y_atomic",
                    f"pool_inputs[{index}]",
                )
            ),
        )
        for index, raw in enumerate(raw_pool_inputs)
        if isinstance(raw, dict)
    )
    if len(pool_inputs) != len(raw_pool_inputs):
        raise ValueError("every pool_inputs entry must be an object")
    if len({item.pool_address for item in pool_inputs}) != len(pool_inputs):
        raise ValueError("pool_inputs pool addresses must be unique")
    requested = pool_inputs[0].requested_quote
    if any(
        abs(item.requested_quote - requested)
        > max(1e-9, abs(requested) * 1e-9)
        for item in pool_inputs[1:]
    ):
        raise ValueError(
            "pool_inputs requested_quote must be identical across pools"
        )

    raw_portfolio = payload.get("portfolio")
    if not isinstance(raw_portfolio, dict):
        raise ValueError("portfolio must be an object")
    raw_allocation = raw_portfolio.get("allocation_criteria") or {}
    if not isinstance(raw_allocation, dict):
        raise ValueError("portfolio.allocation_criteria must be an object")

    equity = float(
        _required(raw_portfolio, "account_equity_quote", "portfolio")
    )
    cash = float(_required(raw_portfolio, "cash_quote", "portfolio"))
    deployed = float(
        _required(raw_portfolio, "current_deployed_quote", "portfolio")
    )
    drawdown = int(
        _required(raw_portfolio, "portfolio_drawdown_bps", "portfolio")
    )
    budget = float(_required(raw_portfolio, "budget_quote", "portfolio"))
    if equity <= 0:
        raise ValueError("portfolio.account_equity_quote must be positive")
    if cash < 0 or deployed < 0:
        raise ValueError("portfolio cash/deployed values cannot be negative")
    if not 0 <= drawdown <= 10_000:
        raise ValueError(
            "portfolio.portfolio_drawdown_bps must be between 0 and 10000"
        )
    if budget <= 0:
        raise ValueError("portfolio.budget_quote must be positive")

    half_widths = tuple(
        int(value)
        for value in raw_portfolio.get(
            "half_widths",
            [0, 1, 2, 5, 10],
        )
    )
    center_offsets = tuple(
        int(value)
        for value in raw_portfolio.get("center_offsets", [0])
    )
    strategies = tuple(
        StrategyType(str(value)).value
        for value in raw_portfolio.get(
            "strategies",
            [
                StrategyType.SPOT.value,
                StrategyType.CURVE.value,
                StrategyType.BID_ASK.value,
            ],
        )
    )
    if not half_widths or any(value < 0 for value in half_widths):
        raise ValueError(
            "portfolio.half_widths must contain non-negative values"
        )
    if not center_offsets:
        raise ValueError("portfolio.center_offsets cannot be empty")
    if not strategies:
        raise ValueError("portfolio.strategies cannot be empty")

    allocation_criteria = PortfolioAllocationCriteria(
        max_positions=int(raw_allocation.get("max_positions", 3)),
        min_positions=int(raw_allocation.get("min_positions", 2)),
        max_pool_allocation_bps=int(
            raw_allocation.get("max_pool_allocation_bps", 4000)
        ),
        min_range_survival_ratio=float(
            raw_allocation.get("min_range_survival_ratio", 0.75)
        ),
        min_excess_vs_hold_bps=int(
            raw_allocation.get("min_excess_vs_hold_bps", 0)
        ),
        min_position_quote=float(
            raw_allocation.get("min_position_quote", 10.0)
        ),
        min_budget_utilization_rate=float(
            raw_allocation.get("min_budget_utilization_rate", 0.75)
        ),
    )
    portfolio = Phase9PortfolioResearchConfig(
        account_equity_quote=equity,
        cash_quote=cash,
        current_deployed_quote=deployed,
        portfolio_drawdown_bps=drawdown,
        observation_limit=int(
            raw_portfolio.get("observation_limit", 12)
        ),
        half_widths=half_widths,
        center_offsets=center_offsets,
        strategies=strategies,
        max_share_bps=int(raw_portfolio.get("max_share_bps", 500)),
        favor_x_in_active_bin=bool(
            raw_portfolio.get("favor_x_in_active_bin", False)
        ),
        budget_quote=budget,
        allocation_criteria=allocation_criteria,
    )
    if portfolio.observation_limit < 2:
        raise ValueError("portfolio.observation_limit must be at least 2")
    if not 1 <= portfolio.max_share_bps <= 10_000:
        raise ValueError(
            "portfolio.max_share_bps must be between 1 and 10000"
        )

    return Phase9ExplicitResearchInputs(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        static_hedges=tuple(hedge_inputs),
        pool_inputs=pool_inputs,
        portfolio=portfolio,
    )


def persist_phase9_explicit_inputs(
    storage: Storage,
    *,
    inputs: Phase9ExplicitResearchInputs,
) -> Phase9ExplicitInputsArtifact:
    payload = inputs.to_record()
    digest = _canonical_sha256(payload)
    evidence_id = storage.save_advanced_edge_evidence(
        edge_type=PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE,
        pool_address=PHASE9_EXPLICIT_INPUTS_SCOPE,
        as_of=None,
        status="INPUTS_VALIDATED",
        qualified=False,
        evidence={
            "artifact_sha256": digest,
            "inputs": payload,
        },
    )
    return Phase9ExplicitInputsArtifact(
        evidence_id=evidence_id,
        artifact_sha256=digest,
        inputs=inputs,
    )


def load_phase9_explicit_inputs(
    storage: Storage,
    *,
    evidence_id: int | None = None,
) -> Phase9ExplicitInputsArtifact | None:
    if evidence_id is None:
        row = storage.latest_advanced_edge_evidence(
            edge_type=PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE,
            pool_address=PHASE9_EXPLICIT_INPUTS_SCOPE,
        )
    else:
        with storage.connect() as conn:
            raw = conn.execute(
                """
                SELECT id, evidence_json
                FROM advanced_edge_evidence
                WHERE id = ?
                  AND edge_type = ?
                  AND pool_address = ?
                LIMIT 1
                """,
                (
                    evidence_id,
                    PHASE9_EXPLICIT_INPUTS_EVIDENCE_TYPE,
                    PHASE9_EXPLICIT_INPUTS_SCOPE,
                ),
            ).fetchone()
        row = (
            {
                "id": int(raw[0]),
                "evidence": json.loads(str(raw[1])),
            }
            if raw is not None
            else None
        )
    if row is None:
        return None

    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("persisted explicit input evidence is malformed")
    raw_inputs = evidence.get("inputs")
    inputs = parse_phase9_explicit_inputs(raw_inputs)
    digest = _canonical_sha256(inputs.to_record())
    persisted_digest = str(evidence.get("artifact_sha256", "")).strip()
    if not persisted_digest or persisted_digest != digest:
        raise ValueError(
            "persisted explicit input artifact SHA-256 does not match inputs"
        )
    return Phase9ExplicitInputsArtifact(
        evidence_id=int(row["id"]),
        artifact_sha256=digest,
        inputs=inputs,
    )


def run_phase9_explicit_research(
    storage: Storage,
    *,
    artifact: Phase9ExplicitInputsArtifact,
    persist: bool = False,
) -> Phase9ExplicitResearchRun:
    static_reports = []
    static_ids: list[int] = []
    for spec in artifact.inputs.static_hedges:
        report = research_static_inventory_hedge(
            storage,
            pool_address=spec.pool_address,
            amount_x=spec.amount_x,
            amount_y=spec.amount_y,
            instrument=spec.instrument,
            criteria=spec.criteria,
            as_of=spec.as_of,
        )
        static_reports.append(report)
        if persist:
            static_ids.append(
                persist_static_hedge_research(
                    storage,
                    report=report,
                )
            )

    portfolio = artifact.inputs.portfolio
    comparison_result = build_multi_pool_research(
        str(storage.path),
        inputs=artifact.inputs.pool_inputs,
        account_equity_quote=portfolio.account_equity_quote,
        cash_quote=portfolio.cash_quote,
        current_deployed_quote=portfolio.current_deployed_quote,
        portfolio_drawdown_bps=portfolio.portfolio_drawdown_bps,
        phase2_gate=None,
        observation_limit=portfolio.observation_limit,
        half_widths=portfolio.half_widths,
        center_offsets=portfolio.center_offsets,
        strategies=tuple(
            StrategyType(value) for value in portfolio.strategies
        ),
        max_share_bps=portfolio.max_share_bps,
        favor_x_in_active_bin=portfolio.favor_x_in_active_bin,
    )

    candidate_id = None
    candidate_sha = None
    candidate_lineage = None
    if persist:
        source_inputs = [asdict(item) for item in artifact.inputs.pool_inputs]
        assumptions = {
            "explicit_input_evidence_id": artifact.evidence_id,
            "explicit_input_artifact_sha256": artifact.artifact_sha256,
            "account_equity_quote": portfolio.account_equity_quote,
            "cash_quote": portfolio.cash_quote,
            "current_deployed_quote": portfolio.current_deployed_quote,
            "portfolio_drawdown_bps": portfolio.portfolio_drawdown_bps,
            "observation_limit": portfolio.observation_limit,
            "half_widths": list(portfolio.half_widths),
            "center_offsets": list(portfolio.center_offsets),
            "strategies": list(portfolio.strategies),
            "max_share_bps": portfolio.max_share_bps,
            "favor_x_in_active_bin": portfolio.favor_x_in_active_bin,
        }
        candidate_id, candidate_sha = persist_portfolio_candidate_research(
            storage,
            comparison=comparison_result.comparison,
            source_inputs=source_inputs,
            assumptions=assumptions,
        )
        candidate_lineage = {
            "candidate_evidence_id": candidate_id,
            "candidate_evidence_sha256": candidate_sha,
        }

    allocation = research_portfolio_allocation(
        storage,
        comparison=comparison_result.comparison,
        budget_quote=portfolio.budget_quote,
        criteria=portfolio.allocation_criteria,
        candidate_lineage=candidate_lineage,
    )
    allocation_id = None
    if persist:
        allocation_id = persist_portfolio_allocation_research(
            storage,
            report=allocation,
        )

    return Phase9ExplicitResearchRun(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        input_evidence_id=artifact.evidence_id,
        input_artifact_sha256=artifact.artifact_sha256,
        static_hedge_reports=tuple(
            report.to_record() for report in static_reports
        ),
        static_hedge_evidence_ids=tuple(static_ids),
        candidate_evidence_id=candidate_id,
        candidate_evidence_sha256=candidate_sha,
        portfolio_allocation=allocation.to_record(),
        portfolio_allocation_evidence_id=allocation_id,
        explicit_research_ready=(
            any(report.research_qualified for report in static_reports)
            and allocation.research_qualified
        ),
    )
