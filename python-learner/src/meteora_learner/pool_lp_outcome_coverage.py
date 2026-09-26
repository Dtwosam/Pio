from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd


DEFAULT_OUTCOME_QUANTILES = (
    0.01,
    0.05,
    0.10,
    0.25,
    0.50,
    0.75,
    0.90,
    0.95,
    0.99,
)

OUTCOME_TARGET_COLUMNS = (
    "target_net_return_bps",
    "target_excess_vs_hold_bps",
    "target_range_survival_ratio",
    "target_downside_bps",
)


@dataclass(frozen=True)
class OutcomeQuantile:
    quantile: float
    value: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeTargetCoverage:
    target: str
    observations: int
    unique_values: int
    minimum: float
    maximum: float
    mean: float
    std: float
    quantiles: tuple[OutcomeQuantile, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PoolOutcomeCoverage:
    pool_address: str
    observations: int
    negative_net_return_rows: int
    zero_net_return_rows: int
    positive_net_return_rows: int
    minimum_net_return_bps: float
    median_net_return_bps: float
    maximum_net_return_bps: float
    maximum_downside_bps: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeCoverageReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    rows_seen: int
    pools_seen: int
    quantiles: tuple[float, ...]
    targets: tuple[OutcomeTargetCoverage, ...]
    pools: tuple[PoolOutcomeCoverage, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeCoverageComparison:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    source: OutcomeCoverageReport
    model_ready: OutcomeCoverageReport
    rows_retained: int
    rows_dropped: int
    retention_rate: float
    source_negative_net_return_rows: int
    model_ready_negative_net_return_rows: int
    negative_net_return_retention_rate: float | None
    source_worst_net_return_bps: float
    model_ready_worst_net_return_bps: float | None
    source_maximum_downside_bps: float
    model_ready_maximum_downside_bps: float | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _validate_quantiles(
    quantiles: Sequence[float],
) -> tuple[float, ...]:
    values = tuple(float(value) for value in quantiles)
    if not values:
        raise ValueError("quantiles must contain at least one value")
    if any(not 0.0 < value < 1.0 for value in values):
        raise ValueError(
            "outcome quantiles must be strictly between 0 and 1"
        )
    if tuple(sorted(set(values))) != values:
        raise ValueError(
            "outcome quantiles must be strictly increasing"
        )
    return values


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "pool_address",
        "target_net_return_bps",
        "target_excess_vs_hold_bps",
        "target_range_survival_ratio",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"missing outcome coverage columns: {missing}"
        )

    work = frame.copy()
    work["pool_address"] = (
        work["pool_address"].astype(str).str.strip()
    )
    if (work["pool_address"] == "").any():
        raise ValueError("pool_address cannot be empty")

    for column in (
        "target_net_return_bps",
        "target_excess_vs_hold_bps",
        "target_range_survival_ratio",
    ):
        work[column] = pd.to_numeric(
            work[column],
            errors="coerce",
        )
    if work[
        [
            "target_net_return_bps",
            "target_excess_vs_hold_bps",
            "target_range_survival_ratio",
        ]
    ].isna().any(axis=None):
        raise ValueError(
            "outcome coverage frame contains missing targets"
        )

    values = work[
        [
            "target_net_return_bps",
            "target_excess_vs_hold_bps",
            "target_range_survival_ratio",
        ]
    ].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(
            "outcome coverage frame contains non-finite targets"
        )

    work["target_downside_bps"] = np.maximum(
        0.0,
        -work["target_net_return_bps"].to_numpy(dtype=float),
    )
    return work


def build_outcome_coverage_report(
    frame: pd.DataFrame,
    *,
    quantiles: Sequence[float] = DEFAULT_OUTCOME_QUANTILES,
) -> OutcomeCoverageReport:
    """
    Describe the realized outcome distribution available to the learner.

    Quantiles and sign counts are descriptive statistics, not risk cutoffs.
    """
    q = _validate_quantiles(quantiles)
    work = _prepare_frame(frame)

    if work.empty:
        return OutcomeCoverageReport(
            research_only=True,
            policy_actionable=False,
            execution_wired=False,
            rows_seen=0,
            pools_seen=0,
            quantiles=q,
            targets=(),
            pools=(),
        )

    targets: list[OutcomeTargetCoverage] = []
    for target in OUTCOME_TARGET_COLUMNS:
        values = work[target].to_numpy(dtype=float)
        quantile_values = np.quantile(values, q)
        targets.append(
            OutcomeTargetCoverage(
                target=target,
                observations=len(values),
                unique_values=int(
                    pd.Series(values).nunique(dropna=False)
                ),
                minimum=float(np.min(values)),
                maximum=float(np.max(values)),
                mean=float(np.mean(values)),
                std=float(np.std(values)),
                quantiles=tuple(
                    OutcomeQuantile(
                        quantile=quantile,
                        value=float(value),
                    )
                    for quantile, value in zip(
                        q,
                        quantile_values,
                    )
                ),
            )
        )

    pools: list[PoolOutcomeCoverage] = []
    for pool_address, group in work.groupby(
        "pool_address",
        sort=True,
    ):
        net = group[
            "target_net_return_bps"
        ].to_numpy(dtype=float)
        downside = group[
            "target_downside_bps"
        ].to_numpy(dtype=float)
        pools.append(
            PoolOutcomeCoverage(
                pool_address=str(pool_address),
                observations=len(group),
                negative_net_return_rows=int(
                    np.sum(net < 0.0)
                ),
                zero_net_return_rows=int(
                    np.sum(net == 0.0)
                ),
                positive_net_return_rows=int(
                    np.sum(net > 0.0)
                ),
                minimum_net_return_bps=float(np.min(net)),
                median_net_return_bps=float(
                    np.median(net)
                ),
                maximum_net_return_bps=float(np.max(net)),
                maximum_downside_bps=float(
                    np.max(downside)
                ),
            )
        )

    return OutcomeCoverageReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        rows_seen=len(work),
        pools_seen=int(work["pool_address"].nunique()),
        quantiles=q,
        targets=tuple(targets),
        pools=tuple(pools),
    )


def compare_outcome_coverage(
    source_frame: pd.DataFrame,
    model_ready_frame: pd.DataFrame,
    *,
    quantiles: Sequence[float] = DEFAULT_OUTCOME_QUANTILES,
) -> OutcomeCoverageComparison:
    """
    Compare the canonical outcome distribution with the rows that survive
    context enrichment.

    This exposes whether missing context disproportionately removes loss cases.
    It does not define an acceptable retention rate.
    """
    source = build_outcome_coverage_report(
        source_frame,
        quantiles=quantiles,
    )
    model_ready = build_outcome_coverage_report(
        model_ready_frame,
        quantiles=quantiles,
    )

    if source.rows_seen == 0:
        raise ValueError(
            "source outcome coverage cannot be empty"
        )

    source_net = next(
        item
        for item in source.targets
        if item.target == "target_net_return_bps"
    )
    source_downside = next(
        item
        for item in source.targets
        if item.target == "target_downside_bps"
    )
    source_negative = sum(
        item.negative_net_return_rows
        for item in source.pools
    )

    if model_ready.rows_seen:
        ready_net = next(
            item
            for item in model_ready.targets
            if item.target == "target_net_return_bps"
        )
        ready_downside = next(
            item
            for item in model_ready.targets
            if item.target == "target_downside_bps"
        )
        ready_negative = sum(
            item.negative_net_return_rows
            for item in model_ready.pools
        )
        ready_worst = ready_net.minimum
        ready_max_downside = ready_downside.maximum
    else:
        ready_negative = 0
        ready_worst = None
        ready_max_downside = None

    negative_retention = (
        ready_negative / source_negative
        if source_negative > 0
        else None
    )

    return OutcomeCoverageComparison(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        source=source,
        model_ready=model_ready,
        rows_retained=model_ready.rows_seen,
        rows_dropped=source.rows_seen - model_ready.rows_seen,
        retention_rate=(
            model_ready.rows_seen / source.rows_seen
        ),
        source_negative_net_return_rows=source_negative,
        model_ready_negative_net_return_rows=ready_negative,
        negative_net_return_retention_rate=negative_retention,
        source_worst_net_return_bps=source_net.minimum,
        model_ready_worst_net_return_bps=ready_worst,
        source_maximum_downside_bps=source_downside.maximum,
        model_ready_maximum_downside_bps=ready_max_downside,
    )
