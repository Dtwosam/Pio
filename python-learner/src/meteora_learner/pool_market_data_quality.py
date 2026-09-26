from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

import pandas as pd

from .research_store import ResearchStore


@dataclass(frozen=True)
class PoolMarketCoverage:
    pool_address: str
    observations: int
    first_observed_at: str
    last_observed_at: str
    valid_price_observations: int
    valid_tvl_observations: int
    valid_volume_observations: int
    valid_fee_observations: int
    median_interval_seconds: float | None
    max_gap_seconds: float | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PoolMarketCoverageReport:
    research_only: bool
    policy_actionable: bool
    pools_seen: int
    observations_seen: int
    coverage: tuple[PoolMarketCoverage, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_pool_market_coverage_report(
    database_path: str,
    *,
    pool_addresses: tuple[str, ...] | None = None,
    start_observed_at: str | None = None,
    end_observed_at: str | None = None,
) -> PoolMarketCoverageReport:
    """
    Describe market-history coverage without applying economic acceptance rules.

    This report is diagnostic only. Missingness and gaps are exposed as raw
    evidence so later collection/training logic can make explicit decisions.
    """
    rows = ResearchStore(database_path).pool_snapshot_history(
        pool_addresses=pool_addresses,
        start_observed_at=start_observed_at,
        end_observed_at=end_observed_at,
    )
    if not rows:
        return PoolMarketCoverageReport(
            research_only=True,
            policy_actionable=False,
            pools_seen=0,
            observations_seen=0,
            coverage=(),
        )

    frame = pd.DataFrame(rows)
    frame["observed_at"] = pd.to_datetime(
        frame["observed_at"],
        utc=True,
        errors="coerce",
    )
    if frame["observed_at"].isna().any():
        raise ValueError(
            "pool snapshot history contains invalid timestamps"
        )

    output: list[PoolMarketCoverage] = []
    for address, group in frame.groupby("address", sort=True):
        group = group.sort_values("observed_at").reset_index(drop=True)
        times = group["observed_at"]

        gaps: list[float] = []
        if len(times) > 1:
            gaps = [
                float(value.total_seconds())
                for value in times.diff().dropna()
            ]

        def valid_count(column: str, *, positive: bool = False) -> int:
            numeric = pd.to_numeric(
                group[column],
                errors="coerce",
            )
            valid = numeric.notna()
            if positive:
                valid &= numeric > 0
            else:
                valid &= numeric >= 0
            return int(valid.sum())

        output.append(
            PoolMarketCoverage(
                pool_address=str(address),
                observations=len(group),
                first_observed_at=times.iloc[0].isoformat(),
                last_observed_at=times.iloc[-1].isoformat(),
                valid_price_observations=valid_count(
                    "current_price",
                    positive=True,
                ),
                valid_tvl_observations=valid_count(
                    "tvl",
                    positive=True,
                ),
                valid_volume_observations=valid_count(
                    "volume_24h",
                ),
                valid_fee_observations=valid_count(
                    "fees_24h",
                ),
                median_interval_seconds=(
                    float(median(gaps)) if gaps else None
                ),
                max_gap_seconds=(
                    float(max(gaps)) if gaps else None
                ),
            )
        )

    return PoolMarketCoverageReport(
        research_only=True,
        policy_actionable=False,
        pools_seen=len(output),
        observations_seen=len(frame),
        coverage=tuple(output),
    )
