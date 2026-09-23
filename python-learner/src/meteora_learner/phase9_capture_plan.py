from __future__ import annotations

from dataclasses import asdict, dataclass
import shlex
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class Phase9ChainCaptureCriteria:
    target_chain_pools: int = 3
    max_candidates: int = 8
    bin_array_radius: int = 1

    def validate(self) -> None:
        if self.target_chain_pools < 1:
            raise ValueError("target_chain_pools must be positive")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if self.bin_array_radius < 0:
            raise ValueError("bin_array_radius cannot be negative")


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
    candidates_available: int
    plan_ready: bool
    criteria: Phase9ChainCaptureCriteria
    candidates: tuple[Phase9ChainCaptureCandidate, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _q(value: object) -> str:
    return shlex.quote(str(value))


def _latest_api_pools(
    storage: Storage,
) -> tuple[dict[str, Any], ...]:
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

    return tuple(
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
) -> Phase9ChainCapturePlan:
    criteria.validate()
    if not executor_bin.strip():
        raise ValueError("executor_bin is required")

    api_pools = _latest_api_pools(storage)
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
    selected = available[: min(needed, criteria.max_candidates)]
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
    if needed == 0:
        reasons.append(
            "target chain-observed pool count is already satisfied"
        )
    elif not api_pools:
        reasons.append(
            "no discovered API pool snapshots are available; run collect-once "
            "before building a chain capture plan"
        )
    elif len(candidates) < needed:
        reasons.append(
            f"{needed - len(candidates)} additional discovered pool(s) are "
            "still needed to reach the target chain-pool count"
        )

    capture_required = needed > 0
    plan_ready = needed == 0 or len(candidates) >= needed

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
        candidates_available=len(available),
        plan_ready=plan_ready,
        criteria=criteria,
        candidates=candidates,
        reasons=tuple(reasons),
    )
