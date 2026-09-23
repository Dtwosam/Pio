from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import shlex
from typing import Any

from .adaptive_range import AdaptiveRangeCriteria
from .adaptive_range_validation import AdaptiveRangeValidationCriteria
from .market_regime import DLMMRegimeCriteria
from .phase9_research import Phase9ResearchCriteria
from .storage import Storage


@dataclass(frozen=True)
class Phase9HistoryPoolPlan:
    pool_address: str
    observations: int
    adaptive_required_observations: int
    regime_required_observations: int
    required_observations: int
    additional_observations_needed: int
    history_ready: bool
    shell_command: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9HistoryPlan:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    chain_pools_seen: int
    research_pools_required: int
    qualified_pools_required_at_minimum: int
    pools_selected: int
    pools_history_ready: int
    plan_ready: bool
    research_criteria: Phase9ResearchCriteria
    adaptive_criteria: AdaptiveRangeCriteria
    validation_criteria: AdaptiveRangeValidationCriteria
    regime_criteria: DLMMRegimeCriteria
    pools: tuple[Phase9HistoryPoolPlan, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def adaptive_minimum_observations(
    adaptive: AdaptiveRangeCriteria,
    validation: AdaptiveRangeValidationCriteria,
) -> int | None:
    maximum_windows = (
        adaptive.lookback_observations
        - adaptive.holding_observations
    )
    if maximum_windows < adaptive.min_historical_windows:
        return None

    # A valid decision needs H + W trailing observations, where H is the
    # forward holding window used to form each historical displacement and W
    # is the minimum count of such windows. D valid decisions then need another
    # H future observations for their outcomes. Inclusive index arithmetic
    # yields D + 2H + W - 1 total observations.
    return (
        validation.min_decisions
        + 2 * adaptive.holding_observations
        + adaptive.min_historical_windows
        - 1
    )


def _pool_observation_counts(
    storage: Storage,
) -> tuple[tuple[str, int], ...]:
    with storage.connect() as conn:
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
    return tuple((str(row[0]), int(row[1])) for row in rows)


def _q(value: object) -> str:
    return shlex.quote(str(value))


def build_phase9_history_plan(
    storage: Storage,
    *,
    research_criteria: Phase9ResearchCriteria = Phase9ResearchCriteria(),
    adaptive_criteria: AdaptiveRangeCriteria = AdaptiveRangeCriteria(),
    validation_criteria: AdaptiveRangeValidationCriteria = (
        AdaptiveRangeValidationCriteria()
    ),
    regime_criteria: DLMMRegimeCriteria = DLMMRegimeCriteria(),
    executor_bin: str = "meteora-executor",
    rpc_url: str | None = None,
    bin_array_radius: int = 1,
) -> Phase9HistoryPlan:
    if not executor_bin.strip():
        raise ValueError("executor_bin is required")
    if bin_array_radius < 0:
        raise ValueError("bin_array_radius cannot be negative")

    adaptive_required = adaptive_minimum_observations(
        adaptive_criteria,
        validation_criteria,
    )
    reasons: list[str] = []
    if adaptive_required is None:
        reasons.append(
            "adaptive lookback cannot contain the configured minimum "
            "historical displacement windows"
        )
        required_observations = regime_criteria.min_observations
    else:
        required_observations = max(
            adaptive_required,
            regime_criteria.min_observations,
        )

    minimum_qualified_by_rate = math.ceil(
        research_criteria.min_qualified_pool_rate
        * research_criteria.min_pools
    )
    qualified_required = max(
        research_criteria.min_qualified_pools,
        minimum_qualified_by_rate,
    )

    counts = _pool_observation_counts(storage)
    selected = counts[: research_criteria.min_pools]
    rpc = rpc_url if rpc_url is not None else "<RPC_URL>"

    pool_plans: list[Phase9HistoryPoolPlan] = []
    for pool_address, observations in selected:
        additional = max(0, required_observations - observations)
        pool_plans.append(
            Phase9HistoryPoolPlan(
                pool_address=pool_address,
                observations=observations,
                adaptive_required_observations=(
                    adaptive_required
                    if adaptive_required is not None
                    else -1
                ),
                regime_required_observations=(
                    regime_criteria.min_observations
                ),
                required_observations=required_observations,
                additional_observations_needed=additional,
                history_ready=(
                    adaptive_required is not None
                    and additional == 0
                ),
                shell_command=(
                    None
                    if additional == 0
                    else (
                        _q(executor_bin)
                        + " inspect-pool "
                        + _q(rpc)
                        + " "
                        + _q(pool_address)
                        + " "
                        + _q(bin_array_radius)
                        + " | pio ingest-chain-snapshot --file -"
                    )
                ),
            )
        )

    if len(counts) < research_criteria.min_pools:
        reasons.append(
            f"chain-observed pools {len(counts)} are below "
            f"{research_criteria.min_pools}"
        )

    history_ready_count = sum(
        item.history_ready for item in pool_plans
    )
    if (
        len(selected) >= research_criteria.min_pools
        and history_ready_count < qualified_required
    ):
        reasons.append(
            f"history-ready pools {history_ready_count} are below the "
            f"minimum {qualified_required} needed to satisfy the default "
            "qualified-pool count/rate gate at the minimum pool set"
        )

    plan_ready = (
        adaptive_required is not None
        and len(selected) >= research_criteria.min_pools
        and history_ready_count >= qualified_required
        and not reasons
    )

    return Phase9HistoryPlan(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        chain_pools_seen=len(counts),
        research_pools_required=research_criteria.min_pools,
        qualified_pools_required_at_minimum=qualified_required,
        pools_selected=len(selected),
        pools_history_ready=history_ready_count,
        plan_ready=plan_ready,
        research_criteria=research_criteria,
        adaptive_criteria=adaptive_criteria,
        validation_criteria=validation_criteria,
        regime_criteria=regime_criteria,
        pools=tuple(pool_plans),
        reasons=tuple(reasons),
    )
