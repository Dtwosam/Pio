from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .adaptive_range import AdaptiveRangeCriteria
from .adaptive_range_validation import AdaptiveRangeValidationCriteria
from .market_regime import DLMMRegimeCriteria
from .phase9_history_plan import adaptive_minimum_observations
from .storage import Storage


@dataclass(frozen=True)
class Phase9PoolCohortCriteria:
    min_research_pools: int = 3
    target_pools: int = 5
    max_sampling_pools: int = 8
    max_api_snapshot_age_seconds: int = 10_800

    def __post_init__(self) -> None:
        if self.min_research_pools < 1:
            raise ValueError("min_research_pools must be positive")
        if self.target_pools < self.min_research_pools:
            raise ValueError(
                "target_pools cannot be below min_research_pools"
            )
        if self.max_sampling_pools < self.target_pools:
            raise ValueError(
                "max_sampling_pools cannot be below target_pools"
            )
        if self.max_api_snapshot_age_seconds < 0:
            raise ValueError(
                "max_api_snapshot_age_seconds cannot be negative"
            )


@dataclass(frozen=True)
class Phase9PoolCohortItem:
    pool_address: str
    rank: int
    api_observed_at: str | None
    tvl: float | None
    volume_24h: float | None
    chain_observations: int
    chain_observed: bool
    history_ready: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9PoolCohortReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    required_observations: int
    criteria: Phase9PoolCohortCriteria
    api_pools_seen: int
    stale_api_pools_excluded: int
    api_ranking_as_of: str | None
    chain_pools_seen: int
    desired_pools: tuple[str, ...]
    research_pools: tuple[str, ...]
    sampling_pools: tuple[str, ...]
    missing_chain_pools: tuple[str, ...]
    research_ready: bool
    items: tuple[Phase9PoolCohortItem, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Phase 9 cohort timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _ranked_api_pools(
    storage: Storage,
    *,
    as_of: str | None,
    max_age_seconds: int,
) -> tuple[tuple[dict[str, Any], ...], int]:
    cutoff_text = None
    if as_of is not None:
        cutoff_text = (
            _parse_time(as_of)
            - timedelta(seconds=max_age_seconds)
        ).isoformat()

    with storage.connect() as conn:
        if as_of is None:
            rows = conn.execute(
                """
                WITH ranked AS (
                    SELECT
                        address, observed_at, tvl, volume_24h,
                        ROW_NUMBER() OVER (
                            PARTITION BY address
                            ORDER BY julianday(observed_at) DESC, id DESC
                        ) AS row_rank
                    FROM pool_snapshots
                    WHERE address IS NOT NULL
                      AND TRIM(address) != ''
                )
                SELECT address, observed_at, tvl, volume_24h
                FROM ranked
                WHERE row_rank = 1
                ORDER BY
                    CASE WHEN tvl IS NULL THEN 1 ELSE 0 END ASC,
                    tvl DESC,
                    CASE WHEN volume_24h IS NULL THEN 1 ELSE 0 END ASC,
                    volume_24h DESC,
                    address ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                WITH ranked AS (
                    SELECT
                        address, observed_at, tvl, volume_24h,
                        ROW_NUMBER() OVER (
                            PARTITION BY address
                            ORDER BY julianday(observed_at) DESC, id DESC
                        ) AS row_rank
                    FROM pool_snapshots
                    WHERE address IS NOT NULL
                      AND TRIM(address) != ''
                      AND julianday(observed_at) <= julianday(?)
                )
                SELECT address, observed_at, tvl, volume_24h
                FROM ranked
                WHERE row_rank = 1
                ORDER BY
                    CASE WHEN tvl IS NULL THEN 1 ELSE 0 END ASC,
                    tvl DESC,
                    CASE WHEN volume_24h IS NULL THEN 1 ELSE 0 END ASC,
                    volume_24h DESC,
                    address ASC
                """,
                (as_of,),
            ).fetchall()

    latest = tuple(
        {
            "pool_address": str(row[0]),
            "observed_at": str(row[1]),
            "tvl": float(row[2]) if row[2] is not None else None,
            "volume_24h": (
                float(row[3]) if row[3] is not None else None
            ),
        }
        for row in rows
    )
    if cutoff_text is None:
        return latest, 0

    cutoff = _parse_time(cutoff_text)
    fresh = tuple(
        item
        for item in latest
        if _parse_time(str(item["observed_at"])) >= cutoff
        and _parse_time(str(item["observed_at"])) <= _parse_time(as_of)
    )
    return fresh, len(latest) - len(fresh)


def _chain_counts(
    storage: Storage,
    *,
    as_of: str | None = None,
) -> dict[str, int]:
    with storage.connect() as conn:
        if as_of is None:
            rows = conn.execute(
                """
                SELECT pool_address, COUNT(*) AS observations
                FROM chain_pool_snapshots
                WHERE pool_address IS NOT NULL
                  AND TRIM(pool_address) != ''
                GROUP BY pool_address
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT pool_address, COUNT(*) AS observations
                FROM chain_pool_snapshots
                WHERE pool_address IS NOT NULL
                  AND TRIM(pool_address) != ''
                  AND julianday(observed_at) <= julianday(?)
                GROUP BY pool_address
                """,
                (as_of,),
            ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _required_observations() -> int:
    adaptive = adaptive_minimum_observations(
        AdaptiveRangeCriteria(),
        AdaptiveRangeValidationCriteria(),
    )
    if adaptive is None:
        raise ValueError(
            "default adaptive criteria cannot produce a history requirement"
        )
    return max(adaptive, DLMMRegimeCriteria().min_observations)


def select_phase9_cohort_source_pools(
    storage: Storage,
    *,
    cohort: Phase9PoolCohortReport,
    limit: int,
    as_of: str | None = None,
) -> tuple[str, ...]:
    if limit < 1:
        return ()

    selected: list[str] = []
    for pool in cohort.sampling_pools:
        value = str(pool).strip()
        if value and value not in selected:
            selected.append(value)
        if len(selected) >= limit:
            return tuple(selected)

    with storage.connect() as conn:
        if as_of is None:
            rows = conn.execute(
                """
                SELECT pool_address, COUNT(*) AS observations
                FROM chain_pool_snapshots
                WHERE pool_address IS NOT NULL
                  AND TRIM(pool_address) != ''
                GROUP BY pool_address
                ORDER BY observations DESC, pool_address ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT pool_address, COUNT(*) AS observations
                FROM chain_pool_snapshots
                WHERE pool_address IS NOT NULL
                  AND TRIM(pool_address) != ''
                  AND julianday(observed_at) <= julianday(?)
                GROUP BY pool_address
                ORDER BY observations DESC, pool_address ASC
                """,
                (as_of,),
            ).fetchall()
    for row in rows:
        pool = str(row[0])
        if pool not in selected:
            selected.append(pool)
        if len(selected) >= limit:
            break
    return tuple(selected)


def evaluate_phase9_pool_cohort(
    storage: Storage,
    *,
    criteria: Phase9PoolCohortCriteria = Phase9PoolCohortCriteria(),
    as_of: str | None = None,
) -> Phase9PoolCohortReport:
    api_pools, stale_api_pools = _ranked_api_pools(
        storage,
        as_of=as_of,
        max_age_seconds=criteria.max_api_snapshot_age_seconds,
    )
    chain_counts = _chain_counts(storage, as_of=as_of)
    required = _required_observations()

    if api_pools:
        ranked = list(api_pools)
        api_addresses = {
            str(item["pool_address"]) for item in api_pools
        }
        chain_only = sorted(
            (
                (pool, count)
                for pool, count in chain_counts.items()
                if pool not in api_addresses
            ),
            key=lambda row: (-row[1], row[0]),
        )
        ranked.extend(
            {
                "pool_address": pool,
                "observed_at": None,
                "tvl": None,
                "volume_24h": None,
            }
            for pool, _ in chain_only
        )
    else:
        ranked = [
            {
                "pool_address": pool,
                "observed_at": None,
                "tvl": None,
                "volume_24h": None,
            }
            for pool, _ in sorted(
                chain_counts.items(),
                key=lambda row: (-row[1], row[0]),
            )
        ]

    items = tuple(
        Phase9PoolCohortItem(
            pool_address=str(item["pool_address"]),
            rank=index,
            api_observed_at=(
                str(item["observed_at"])
                if item["observed_at"] is not None
                else None
            ),
            tvl=item["tvl"],
            volume_24h=item["volume_24h"],
            chain_observations=chain_counts.get(
                str(item["pool_address"]), 0
            ),
            chain_observed=(
                chain_counts.get(str(item["pool_address"]), 0) > 0
            ),
            history_ready=(
                chain_counts.get(str(item["pool_address"]), 0)
                >= required
            ),
        )
        for index, item in enumerate(ranked, start=1)
    )

    desired_items = items[: criteria.target_pools]
    research_items = tuple(
        item for item in items if item.history_ready
    )[: criteria.target_pools]

    sampling: list[str] = []
    for item in desired_items:
        if item.chain_observed and item.pool_address not in sampling:
            sampling.append(item.pool_address)
    for item in research_items:
        if (
            item.pool_address not in sampling
            and len(sampling) < criteria.max_sampling_pools
        ):
            sampling.append(item.pool_address)

    missing = tuple(
        item.pool_address
        for item in desired_items
        if not item.chain_observed
    )
    research_pools = tuple(
        item.pool_address for item in research_items
    )
    desired_pools = tuple(
        item.pool_address for item in desired_items
    )

    reasons: list[str] = []
    if stale_api_pools:
        reasons.append(
            f"{stale_api_pools} stale API pool ranking snapshot(s) were "
            "excluded from cohort steering"
        )
    if len(desired_pools) < criteria.min_research_pools:
        reasons.append(
            f"ranked pool universe {len(desired_pools)} is below "
            f"{criteria.min_research_pools}"
        )
    if missing:
        reasons.append(
            "top-ranked pools missing chain capture: "
            + ", ".join(missing)
        )
    if len(research_pools) < criteria.min_research_pools:
        reasons.append(
            f"history-ready ranked pools {len(research_pools)} are below "
            f"{criteria.min_research_pools}"
        )

    return Phase9PoolCohortReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        required_observations=required,
        criteria=criteria,
        api_pools_seen=len(api_pools),
        stale_api_pools_excluded=stale_api_pools,
        api_ranking_as_of=as_of,
        chain_pools_seen=len(chain_counts),
        desired_pools=desired_pools,
        research_pools=research_pools,
        sampling_pools=tuple(sampling),
        missing_chain_pools=missing,
        research_ready=(
            len(research_pools) >= criteria.min_research_pools
        ),
        items=items,
        reasons=tuple(reasons),
    )
