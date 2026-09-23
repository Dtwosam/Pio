from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from .storage import Storage


@dataclass(frozen=True)
class AdaptiveRangeCriteria:
    lookback_observations: int = 96
    holding_observations: int = 6
    target_coverage: float = 0.90
    min_half_width_bins: int = 1
    max_half_width_bins: int = 35
    min_historical_windows: int = 12

    def __post_init__(self) -> None:
        if self.lookback_observations < 3:
            raise ValueError("lookback_observations must be at least 3")
        if self.holding_observations < 1:
            raise ValueError("holding_observations must be positive")
        if not 0.0 < self.target_coverage <= 1.0:
            raise ValueError("target_coverage must be in (0, 1]")
        if self.min_half_width_bins < 0:
            raise ValueError("min_half_width_bins cannot be negative")
        if self.max_half_width_bins < self.min_half_width_bins:
            raise ValueError(
                "max_half_width_bins cannot be below min_half_width_bins"
            )
        if self.min_historical_windows < 1:
            raise ValueError("min_historical_windows must be positive")


@dataclass(frozen=True)
class AdaptiveRangeResearchReport:
    pool_address: str
    as_of: str | None
    phase8_promoted: bool
    status: str
    research_only: bool
    policy_actionable: bool
    observations_used: int
    historical_windows: int
    holding_observations: int
    target_coverage: float
    latest_active_bin_id: int | None
    net_drift_bins: int | None
    total_path_bins: int | None
    directional_efficiency: float | None
    median_abs_step_bins: float | None
    p90_abs_step_bins: int | None
    max_abs_step_bins: int | None
    raw_recommended_half_width_bins: int | None
    recommended_half_width_bins: int | None
    empirical_coverage: float | None
    criteria: AdaptiveRangeCriteria
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _nearest_rank(values: list[int], quantile: float) -> int:
    if not values:
        raise ValueError("quantile requires at least one value")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _median(values: list[int]) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _historical_max_displacements(
    active_bins: list[int],
    holding_observations: int,
) -> list[int]:
    values: list[int] = []
    # Each window uses a historical entry observation and only observations
    # after that entry that are still strictly before the current decision.
    for start in range(0, len(active_bins) - holding_observations):
        entry = active_bins[start]
        path = active_bins[
            start + 1 : start + holding_observations + 1
        ]
        values.append(max(abs(value - entry) for value in path))
    return values


def research_adaptive_range(
    storage: Storage,
    *,
    pool_address: str,
    criteria: AdaptiveRangeCriteria = AdaptiveRangeCriteria(),
    as_of: str | None = None,
) -> AdaptiveRangeResearchReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")

    phase8_promoted = storage.phase_is_promoted(
        PHASE8,
        evidence_type=PHASE8_EVIDENCE_TYPE,
    )

    with storage.connect() as conn:
        if as_of is None:
            rows = conn.execute(
                """
                SELECT observed_at, active_bin_id
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                ORDER BY julianday(observed_at) DESC, id DESC
                LIMIT ?
                """,
                (pool_address, criteria.lookback_observations),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT observed_at, active_bin_id
                FROM chain_pool_snapshots
                WHERE pool_address = ?
                  AND julianday(observed_at) <= julianday(?)
                ORDER BY julianday(observed_at) DESC, id DESC
                LIMIT ?
                """,
                (
                    pool_address,
                    as_of,
                    criteria.lookback_observations,
                ),
            ).fetchall()

    ordered_rows = list(reversed(rows))
    active_bins = [int(row[1]) for row in ordered_rows]
    reasons: list[str] = []

    if len(active_bins) < 2:
        reasons.append(
            "at least two historical chain observations are required"
        )
        return AdaptiveRangeResearchReport(
            pool_address=pool_address,
            as_of=as_of,
            phase8_promoted=phase8_promoted,
            status="INSUFFICIENT_HISTORY",
            research_only=True,
            policy_actionable=False,
            observations_used=len(active_bins),
            historical_windows=0,
            holding_observations=criteria.holding_observations,
            target_coverage=criteria.target_coverage,
            latest_active_bin_id=active_bins[-1] if active_bins else None,
            net_drift_bins=None,
            total_path_bins=None,
            directional_efficiency=None,
            median_abs_step_bins=None,
            p90_abs_step_bins=None,
            max_abs_step_bins=None,
            raw_recommended_half_width_bins=None,
            recommended_half_width_bins=None,
            empirical_coverage=None,
            criteria=criteria,
            reasons=tuple(reasons),
        )

    steps = [
        active_bins[index] - active_bins[index - 1]
        for index in range(1, len(active_bins))
    ]
    abs_steps = [abs(value) for value in steps]
    total_path = sum(abs_steps)
    net_drift = active_bins[-1] - active_bins[0]
    directional_efficiency = (
        abs(net_drift) / total_path if total_path > 0 else 0.0
    )
    windows = _historical_max_displacements(
        active_bins,
        criteria.holding_observations,
    )

    raw_width = None
    width = None
    coverage = None
    if len(windows) < criteria.min_historical_windows:
        reasons.append(
            f"historical holding windows {len(windows)} are below "
            f"{criteria.min_historical_windows}"
        )
        status = "INSUFFICIENT_WINDOWS"
    else:
        raw_width = _nearest_rank(
            windows,
            criteria.target_coverage,
        )
        width = min(
            criteria.max_half_width_bins,
            max(criteria.min_half_width_bins, raw_width),
        )
        coverage = sum(value <= width for value in windows) / len(windows)
        if raw_width > criteria.max_half_width_bins:
            reasons.append(
                f"raw required half-width {raw_width} exceeds configured "
                f"maximum {criteria.max_half_width_bins}"
            )
            status = "RANGE_CAP_EXCEEDED"
        elif not phase8_promoted:
            reasons.append(
                "Phase 8 is not persistently promoted; advanced edge remains research-only"
            )
            status = "RESEARCH_ONLY_PHASE8_BLOCKED"
        else:
            status = "RESEARCH_READY"

    return AdaptiveRangeResearchReport(
        pool_address=pool_address,
        as_of=as_of,
        phase8_promoted=phase8_promoted,
        status=status,
        research_only=True,
        policy_actionable=False,
        observations_used=len(active_bins),
        historical_windows=len(windows),
        holding_observations=criteria.holding_observations,
        target_coverage=criteria.target_coverage,
        latest_active_bin_id=active_bins[-1],
        net_drift_bins=net_drift,
        total_path_bins=total_path,
        directional_efficiency=directional_efficiency,
        median_abs_step_bins=_median(abs_steps),
        p90_abs_step_bins=_nearest_rank(abs_steps, 0.90),
        max_abs_step_bins=max(abs_steps),
        raw_recommended_half_width_bins=raw_width,
        recommended_half_width_bins=width,
        empirical_coverage=coverage,
        criteria=criteria,
        reasons=tuple(reasons),
    )
