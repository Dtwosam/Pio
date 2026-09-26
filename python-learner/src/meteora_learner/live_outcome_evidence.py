from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from statistics import fmean, median
from typing import Any

import pandas as pd

from .live_action_cost_evidence import (
    LiveActionCostEvidenceReport,
    build_live_action_cost_evidence,
)
from .research_store import ResearchStore


BPS = Decimal("10000")
BPS_PER_PERCENT = Decimal("100")


def _d(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("live learning evidence contains a non-finite value")
    return parsed


def _ratio_bps(value: Any, denominator: Decimal) -> float:
    return float(_d(value) * BPS / denominator)


@dataclass(frozen=True)
class LiveOutcomeEvidenceSample:
    position_address: str
    decision_id: str
    pool_address: str
    model_version: str
    strategy: str
    range_width_bins: int
    quote_unit: str
    valued_execution_count: int
    expected_net_return_bps: float
    realized_return_bps: int
    prediction_error_bps: int
    expected_downside_bps: float
    realized_downside_bps: int
    downside_error_bps: float
    downside_exceeded_expected: bool
    composition_cost_bps: float
    network_cost_bps: float
    fee_income_bps: float
    reward_income_bps: float
    realized_pnl_quote: str
    entry_outflow_quote: str
    valuation_max_age_seconds: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveModelCalibration:
    model_version: str
    samples: int
    pools: int
    mean_realized_return_bps: float
    mean_prediction_error_bps: float
    mean_absolute_prediction_error_bps: float
    downside_exceedance_rate: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveOutcomeEvidenceReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    samples_seen: int
    pools_seen: int
    models_seen: int
    strategies_seen: int
    positive_return_rate: float | None
    mean_realized_return_bps: float | None
    median_realized_return_bps: float | None
    mean_prediction_error_bps: float | None
    median_prediction_error_bps: float | None
    mean_absolute_prediction_error_bps: float | None
    mean_expected_downside_bps: float | None
    mean_realized_downside_bps: float | None
    mean_downside_error_bps: float | None
    downside_exceedance_rate: float | None
    mean_composition_cost_bps: float | None
    mean_network_cost_bps: float | None
    mean_fee_income_bps: float | None
    mean_reward_income_bps: float | None
    model_calibration: tuple[LiveModelCalibration, ...]
    samples: tuple[LiveOutcomeEvidenceSample, ...]
    action_cost_evidence: LiveActionCostEvidenceReport | None = None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(asdict(item) for item in self.samples)


def _sample(row: dict[str, Any]) -> LiveOutcomeEvidenceSample:
    label_return = int(row["realized_return_bps"])
    valuation_return = int(row["valuation_realized_return_bps"])
    if label_return != valuation_return:
        raise ValueError(
            "live learning label realized return differs from valuation"
        )

    entry_outflow = _d(row["entry_outflow_quote"])
    if entry_outflow <= 0:
        raise ValueError(
            "live learning evidence requires positive entry_outflow_quote"
        )

    expected_return_bps_int = int(
        _d(row["expected_net_return_pct"]) * BPS_PER_PERCENT
    )
    expected_return_bps = float(expected_return_bps_int)
    stored_prediction_error = int(row["prediction_error_bps"])
    calculated_prediction_error = (
        label_return - expected_return_bps_int
    )
    if stored_prediction_error != calculated_prediction_error:
        raise ValueError(
            "live learning label prediction_error_bps is inconsistent"
        )

    expected_downside_bps = float(
        _d(row["expected_downside_pct"]) * BPS_PER_PERCENT
    )
    realized_downside = max(0, -label_return)

    return LiveOutcomeEvidenceSample(
        position_address=str(row["position_address"]),
        decision_id=str(row["decision_id"]),
        pool_address=str(row["pool_address"]),
        model_version=str(row["model_version"]),
        strategy=str(row["strategy"]),
        range_width_bins=int(row["range_width_bins"]),
        quote_unit=str(row["quote_unit"]),
        valued_execution_count=int(row["valued_execution_count"]),
        expected_net_return_bps=expected_return_bps,
        realized_return_bps=label_return,
        prediction_error_bps=stored_prediction_error,
        expected_downside_bps=expected_downside_bps,
        realized_downside_bps=realized_downside,
        downside_error_bps=(
            float(realized_downside) - expected_downside_bps
        ),
        downside_exceeded_expected=(
            float(realized_downside) > expected_downside_bps
        ),
        composition_cost_bps=_ratio_bps(
            row["composition_cost_quote"],
            entry_outflow,
        ),
        network_cost_bps=_ratio_bps(
            row["network_cost_quote"],
            entry_outflow,
        ),
        fee_income_bps=_ratio_bps(
            row["fee_income_quote"],
            entry_outflow,
        ),
        reward_income_bps=_ratio_bps(
            row["reward_income_quote"],
            entry_outflow,
        ),
        realized_pnl_quote=str(row["realized_pnl_quote"]),
        entry_outflow_quote=str(row["entry_outflow_quote"]),
        valuation_max_age_seconds=int(row["max_age_seconds"]),
    )


def _model_calibration(
    samples: tuple[LiveOutcomeEvidenceSample, ...],
) -> tuple[LiveModelCalibration, ...]:
    output: list[LiveModelCalibration] = []
    versions = sorted({item.model_version for item in samples})
    for version in versions:
        rows = [item for item in samples if item.model_version == version]
        output.append(
            LiveModelCalibration(
                model_version=version,
                samples=len(rows),
                pools=len({item.pool_address for item in rows}),
                mean_realized_return_bps=float(
                    fmean(item.realized_return_bps for item in rows)
                ),
                mean_prediction_error_bps=float(
                    fmean(item.prediction_error_bps for item in rows)
                ),
                mean_absolute_prediction_error_bps=float(
                    fmean(abs(item.prediction_error_bps) for item in rows)
                ),
                downside_exceedance_rate=(
                    sum(item.downside_exceeded_expected for item in rows)
                    / len(rows)
                ),
            )
        )
    return tuple(output)


def build_live_outcome_evidence(
    database_path: str,
) -> LiveOutcomeEvidenceReport:
    """
    Summarize immutable, quote-backed controlled-live learning labels.

    This adapter does not create new labels, move capital, qualify a model, or
    mix live observations into historical training. It only exposes realized
    return/cost calibration evidence already persisted by the LIVE ledger.
    """
    rows = ResearchStore(database_path).live_learning_evidence_rows()
    samples = tuple(_sample(row) for row in rows)

    if not samples:
        return LiveOutcomeEvidenceReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            samples_seen=0,
            pools_seen=0,
            models_seen=0,
            strategies_seen=0,
            positive_return_rate=None,
            mean_realized_return_bps=None,
            median_realized_return_bps=None,
            mean_prediction_error_bps=None,
            median_prediction_error_bps=None,
            mean_absolute_prediction_error_bps=None,
            mean_expected_downside_bps=None,
            mean_realized_downside_bps=None,
            mean_downside_error_bps=None,
            downside_exceedance_rate=None,
            mean_composition_cost_bps=None,
            mean_network_cost_bps=None,
            mean_fee_income_bps=None,
            mean_reward_income_bps=None,
            model_calibration=(),
            samples=(),
            action_cost_evidence=build_live_action_cost_evidence(
                database_path
            ),
        )

    realized = [item.realized_return_bps for item in samples]
    errors = [item.prediction_error_bps for item in samples]

    return LiveOutcomeEvidenceReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        samples_seen=len(samples),
        pools_seen=len({item.pool_address for item in samples}),
        models_seen=len({item.model_version for item in samples}),
        strategies_seen=len({item.strategy for item in samples}),
        positive_return_rate=(
            sum(item.realized_return_bps > 0 for item in samples)
            / len(samples)
        ),
        mean_realized_return_bps=float(fmean(realized)),
        median_realized_return_bps=float(median(realized)),
        mean_prediction_error_bps=float(fmean(errors)),
        median_prediction_error_bps=float(median(errors)),
        mean_absolute_prediction_error_bps=float(
            fmean(abs(value) for value in errors)
        ),
        mean_expected_downside_bps=float(
            fmean(item.expected_downside_bps for item in samples)
        ),
        mean_realized_downside_bps=float(
            fmean(item.realized_downside_bps for item in samples)
        ),
        mean_downside_error_bps=float(
            fmean(item.downside_error_bps for item in samples)
        ),
        downside_exceedance_rate=(
            sum(item.downside_exceeded_expected for item in samples)
            / len(samples)
        ),
        mean_composition_cost_bps=float(
            fmean(item.composition_cost_bps for item in samples)
        ),
        mean_network_cost_bps=float(
            fmean(item.network_cost_bps for item in samples)
        ),
        mean_fee_income_bps=float(
            fmean(item.fee_income_bps for item in samples)
        ),
        mean_reward_income_bps=float(
            fmean(item.reward_income_bps for item in samples)
        ),
        model_calibration=_model_calibration(samples),
        samples=samples,
        action_cost_evidence=build_live_action_cost_evidence(
            database_path
        ),
    )
