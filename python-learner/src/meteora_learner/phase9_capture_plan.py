from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import shlex
from typing import Any, Sequence

from .storage import Storage


@dataclass(frozen=True)
class Phase9ChainCaptureCriteria:
    target_chain_pools: int = 3
    max_candidates: int = 8
    bin_array_radius: int = 1
    max_api_snapshot_age_seconds: int = 10_800

    def validate(self) -> None:
        if self.target_chain_pools < 1:
            raise ValueError("target_chain_pools must be positive")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if self.bin_array_radius < 0:
            raise ValueError("bin_array_radius cannot be negative")
        if self.max_api_snapshot_age_seconds < 0:
            raise ValueError(
                "max_api_snapshot_age_seconds cannot be negative"
            )


@dataclass(frozen=True)
class Phase9ChainCaptureCandidate:
    pool_address: str
    api_observed_at: str
    tvl: float | None
    volume_24h: float | None
    fees_24h: float | None
    rank: int
    shell_command: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9ChainCapturePlan:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    current_chain_pool_count: int
    target_chain_pool_count: int
    additional_chain_pools_needed: int
    capture_required: bool
    api_pool_count: int
    stale_api_pool_count: int
    api_ranking_as_of: str | None
    candidates_available: int
    preferred_pool_count: int
    preferred_missing_chain_pools: tuple[str, ...]
    plan_ready: bool
    criteria: Phase9ChainCaptureCriteria
    candidates: tuple[Phase9ChainCaptureCandidate, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _q(value: object) -> str:
    return shlex.quote(str(value))


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Phase 9 capture-plan timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _latest_api_pools(
    storage: Storage,
    *,
    as_of: str | None,
    max_age_seconds: int,
) -> tuple[tuple[dict[str, Any], ...], int]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            WITH ranked AS (
                SELECT
                    address, observed_at, tvl,
                    volume_24h, fees_24h,
                    ROW_NUMBER() OVER (
                        PARTITION BY address
                        ORDER BY julianday(observed_at) DESC, id DESC
                    ) AS row_rank
                FROM pool_snapshots
                WHERE address IS NOT NULL
                  AND TRIM(address) != ''
            )
            SELECT address, observed_at, tvl,
                   volume_24h, fees_24h
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

    latest = tuple(
        {
            "pool_address": str(row[0]),
            "observed_at": str(row[1]),
            "tvl": float(row[2]) if row[2] is not None else None,
            "volume_24h": (
                float(row[3]) if row[3] is not None else None
            ),
            "fees_24h": (
                float(row[4]) if row[4] is not None else None
            ),
        }
        for row in rows
    )
    if as_of is None:
        return latest, 0

    now = _parse_time(as_of)
    cutoff = now - timedelta(seconds=max_age_seconds)
    fresh = tuple(
        item
        for item in latest
        if cutoff <= _parse_time(str(item["observed_at"])) <= now
    )
    return fresh, len(latest) - len(fresh)


def _chain_pool_addresses(storage: Storage) -> set[str]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT pool_address
            FROM chain_pool_snapshots
            WHERE pool_address IS NOT NULL
              AND TRIM(pool_address) != ''
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


def build_phase9_chain_capture_plan(
    storage: Storage,
    *,
    criteria: Phase9ChainCaptureCriteria = Phase9ChainCaptureCriteria(),
    rpc_url: str | None = None,
    executor_bin: str = "meteora-executor",
    preferred_pool_addresses: Sequence[str] | None = None,
    max_preferred_candidates: int | None = None,
    as_of: str | None = None,
) -> Phase9ChainCapturePlan:
    criteria.validate()
    if not executor_bin.strip():
        raise ValueError("executor_bin is required")

    if (
        max_preferred_candidates is not None
        and max_preferred_candidates < 1
    ):
        raise ValueError(
            "max_preferred_candidates must be positive when provided"
        )

    api_pools, stale_api_pools = _latest_api_pools(
        storage,
        as_of=as_of,
        max_age_seconds=criteria.max_api_snapshot_age_seconds,
    )
    chain_pools = _chain_pool_addresses(storage)
    needed = max(
        0,
        criteria.target_chain_pools - len(chain_pools),
    )
    available = tuple(
        item
        for item in api_pools
        if item["pool_address"] not in chain_pools
    )
    api_by_pool = {
        str(item["pool_address"]): item
        for item in api_pools
    }
    preferred = tuple(
        dict.fromkeys(
            str(pool).strip()
            for pool in (preferred_pool_addresses or ())
            if str(pool).strip()
        )
    )
    preferred_missing = tuple(
        pool
        for pool in preferred
        if pool not in chain_pools
    )
    preferred_limit = (
        criteria.max_candidates
        if max_preferred_candidates is None
        else min(
            criteria.max_candidates,
            max_preferred_candidates,
        )
    )
    selected_items: list[dict[str, Any]] = []
    for pool in preferred_missing:
        item = api_by_pool.get(pool)
        if item is None:
            continue
        if len(selected_items) >= preferred_limit:
            break
        selected_items.append(item)

    remaining_capacity = criteria.max_candidates - len(selected_items)
    minimum_remaining = max(
        0,
        needed - len(selected_items),
    )
    if remaining_capacity > 0 and minimum_remaining > 0:
        selected_pools = {
            str(item["pool_address"])
            for item in selected_items
        }
        for item in available:
            if str(item["pool_address"]) in selected_pools:
                continue
            selected_items.append(item)
            selected_pools.add(str(item["pool_address"]))
            if (
                len(selected_items) >= criteria.max_candidates
                or len(selected_items) >= needed
            ):
                break
    selected = tuple(selected_items)
    rpc = rpc_url if rpc_url is not None else "<RPC_URL>"

    candidates = tuple(
        Phase9ChainCaptureCandidate(
            pool_address=str(item["pool_address"]),
            api_observed_at=str(item["observed_at"]),
            tvl=item["tvl"],
            volume_24h=item["volume_24h"],
            fees_24h=item["fees_24h"],
            rank=index,
            shell_command=(
                _q(executor_bin)
                + " inspect-pool "
                + _q(rpc)
                + " "
                + _q(item["pool_address"])
                + " "
                + _q(criteria.bin_array_radius)
                + " | pio ingest-chain-snapshot --file -"
            ),
        )
        for index, item in enumerate(selected, start=1)
    )

    reasons: list[str] = []
    unresolved_preferred = tuple(
        pool
        for pool in preferred_missing
        if pool not in {
            str(item["pool_address"])
            for item in selected
        }
    )
    if needed == 0 and not preferred_missing:
        reasons.append(
            "target chain-observed pool count is already satisfied"
        )
    elif needed == 0 and preferred_missing:
        reasons.append(
            "minimum chain-pool count is satisfied but ranked cohort "
            "onboarding is still required"
        )
    if stale_api_pools:
        reasons.append(
            f"{stale_api_pools} stale API pool snapshot(s) were excluded "
            "from chain onboarding"
        )
    if not api_pools and (needed > 0 or preferred_missing):
        reasons.append(
            "no fresh discovered API pool snapshots are available; run "
            "collect-once before building a chain capture plan"
        )
    if len(candidates) < needed:
        reasons.append(
            f"{needed - len(candidates)} additional discovered pool(s) are "
            "still needed to reach the target chain-pool count"
        )
    if unresolved_preferred:
        reasons.append(
            "ranked cohort pool(s) still require chain capture: "
            + ", ".join(unresolved_preferred)
        )

    capture_required = needed > 0 or bool(preferred_missing)
    plan_ready = (
        (needed == 0 or len(candidates) >= needed)
        and not unresolved_preferred
    )

    return Phase9ChainCapturePlan(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        current_chain_pool_count=len(chain_pools),
        target_chain_pool_count=criteria.target_chain_pools,
        additional_chain_pools_needed=needed,
        capture_required=capture_required,
        api_pool_count=len(api_pools),
        stale_api_pool_count=stale_api_pools,
        api_ranking_as_of=as_of,
        candidates_available=len(available),
        preferred_pool_count=len(preferred),
        preferred_missing_chain_pools=preferred_missing,
        plan_ready=plan_ready,
        criteria=criteria,
        candidates=candidates,
        reasons=tuple(reasons),
    )
