from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .phase_promotion import PHASE8, PHASE8_EVIDENCE_TYPE
from .storage import Storage


REGIME_QUIET = "QUIET"
REGIME_NORMAL = "NORMAL"
REGIME_TREND_UP = "TREND_UP"
REGIME_TREND_DOWN = "TREND_DOWN"
REGIME_VOLATILE_CHOP = "VOLATILE_CHOP"


@dataclass(frozen=True)
class DLMMRegimeCriteria:
    lookback_observations: int = 72
    recent_observations: int = 8
    min_observations: int = 16
    trend_efficiency_threshold: float = 0.65
    activity_percentile: float = 0.75
    quiet_percentile: float = 0.25

    def __post_init__(self) -> None:
        if self.lookback_observations < 3:
            raise ValueError("lookback_observations must be at least 3")
        if self.recent_observations < 2:
            raise ValueError("recent_observations must be at least 2")
        if self.recent_observations >= self.lookback_observations:
            raise ValueError(
                "recent_observations must be below lookback_observations"
            )
        if self.min_observations < self.recent_observations + 1:
            raise ValueError(
                "min_observations must exceed recent_observations"
            )
        if not 0.0 <= self.trend_efficiency_threshold <= 1.0:
            raise ValueError(
                "trend_efficiency_threshold must be between 0 and 1"
            )
        if not 0.0 < self.quiet_percentile < self.activity_percentile <= 1.0:
            raise ValueError(
                "quiet_percentile must be below activity_percentile in (0, 1]"
            )


@dataclass(frozen=True)
class DLMMRegimeReport:
    pool_address: str
    as_of: str | None
    phase8_promoted: bool
    status: str
    regime: str | None
    research_only: bool
    policy_actionable: bool
    observations_used: int
    latest_active_bin_id: int | None
    recent_net_drift_bins: int | None
    recent_path_bins: int | None
    recent_directional_efficiency: float | None
    recent_mean_abs_step_bins: float | None
    historical_quiet_step_threshold: int | None
    historical_active_step_threshold: int | None
    historical_max_abs_step_bins: int | None
    criteria: DLMMRegimeCriteria
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _nearest_rank(values: list[int], quantile: float) -> int:
    if not values:
        raise ValueError("quantile requires values")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def classify_dlmm_regime(
    storage: Storage,
    *,
    pool_address: str,
    criteria: DLMMRegimeCriteria = DLMMRegimeCriteria(),
    as_of: str | None = None,
) -> DLMMRegimeReport:
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

    active_bins = [int(row[1]) for row in reversed(rows)]
    if len(active_bins) < criteria.min_observations:
        return DLMMRegimeReport(
            pool_address=pool_address,
            as_of=as_of,
            phase8_promoted=phase8_promoted,
            status="INSUFFICIENT_HISTORY",
            regime=None,
            research_only=True,
            policy_actionable=False,
            observations_used=len(active_bins),
            latest_active_bin_id=active_bins[-1] if active_bins else None,
            recent_net_drift_bins=None,
            recent_path_bins=None,
            recent_directional_efficiency=None,
            recent_mean_abs_step_bins=None,
            historical_quiet_step_threshold=None,
            historical_active_step_threshold=None,
            historical_max_abs_step_bins=None,
            criteria=criteria,
            reasons=(
                f"observations {len(active_bins)} are below "
                f"{criteria.min_observations}",
            ),
        )

    steps = [
        active_bins[index] - active_bins[index - 1]
        for index in range(1, len(active_bins))
    ]
    abs_steps = [abs(value) for value in steps]
    quiet_threshold = _nearest_rank(
        abs_steps,
        criteria.quiet_percentile,
    )
    active_threshold = _nearest_rank(
        abs_steps,
        criteria.activity_percentile,
    )

    recent_bins = active_bins[-(criteria.recent_observations + 1):]
    recent_steps = [
        recent_bins[index] - recent_bins[index - 1]
        for index in range(1, len(recent_bins))
    ]
    recent_abs = [abs(value) for value in recent_steps]
    recent_path = sum(recent_abs)
    recent_drift = recent_bins[-1] - recent_bins[0]
    efficiency = (
        abs(recent_drift) / recent_path
        if recent_path > 0
        else 0.0
    )
    recent_mean = (
        sum(recent_abs) / len(recent_abs)
        if recent_abs
        else 0.0
    )

    # Thresholds are derived from the same trailing history available at the
    # decision cutoff. No future observations participate.
    trend_magnitude_floor = max(1, active_threshold)
    trending = (
        efficiency >= criteria.trend_efficiency_threshold
        and abs(recent_drift) >= trend_magnitude_floor
    )
    high_activity = (
        active_threshold > 0
        and recent_mean >= active_threshold
    )
    quiet = (
        recent_path == 0
        or recent_mean <= quiet_threshold
    )

    if trending:
        regime = (
            REGIME_TREND_UP
            if recent_drift > 0
            else REGIME_TREND_DOWN
        )
    elif high_activity:
        regime = REGIME_VOLATILE_CHOP
    elif quiet:
        regime = REGIME_QUIET
    else:
        regime = REGIME_NORMAL

    reasons: list[str] = []
    if not phase8_promoted:
        status = "RESEARCH_ONLY_PHASE8_BLOCKED"
        reasons.append(
            "Phase 8 is not persistently promoted; regime output remains research-only"
        )
    else:
        status = "RESEARCH_READY"

    return DLMMRegimeReport(
        pool_address=pool_address,
        as_of=as_of,
        phase8_promoted=phase8_promoted,
        status=status,
        regime=regime,
        research_only=True,
        policy_actionable=False,
        observations_used=len(active_bins),
        latest_active_bin_id=active_bins[-1],
        recent_net_drift_bins=recent_drift,
        recent_path_bins=recent_path,
        recent_directional_efficiency=efficiency,
        recent_mean_abs_step_bins=recent_mean,
        historical_quiet_step_threshold=quiet_threshold,
        historical_active_step_threshold=active_threshold,
        historical_max_abs_step_bins=max(abs_steps),
        criteria=criteria,
        reasons=tuple(reasons),
    )
