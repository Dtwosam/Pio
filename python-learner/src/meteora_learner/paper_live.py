from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .paper_account import paper_position_snapshot
from .paper_chain import apply_chain_paper_observation
from .paper_chain_runner import (
    PaperChainBatchItem,
    run_chain_paper_batch,
)
from .paper_runner import PaperBatchRunReport
from .pool_safety import (
    PoolSafetyAssessment,
    PoolSafetyConfig,
    screen_pool_universe,
)
from .position_policy import PositionManagementConfig
from .research_store import ResearchStore
from .storage import Storage


@dataclass(frozen=True)
class LivePoolSafety:
    pool_address: str
    safe: bool
    reason: str | None
    assessment: PoolSafetyAssessment | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LivePaperChainResult:
    position_id: str
    observed_at: str
    safety: LivePoolSafety
    executed_action: str
    detail: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LivePaperChainBatchItem:
    position_id: str
    token_y_quote_per_atomic: float
    emergency_exit: bool = False
    estimated_exit_cost_quote: float = 0.0
    rebalance_cost_quote: float | None = None

    def __post_init__(self) -> None:
        if not self.position_id.strip():
            raise ValueError("position_id is required")
        if self.token_y_quote_per_atomic <= 0:
            raise ValueError("token_y_quote_per_atomic must be positive")
        if self.estimated_exit_cost_quote < 0:
            raise ValueError("estimated_exit_cost_quote cannot be negative")
        if self.rebalance_cost_quote is not None and self.rebalance_cost_quote < 0:
            raise ValueError("rebalance_cost_quote cannot be negative")


def _latest_observation(
    storage: Storage,
    *,
    pool_address: str,
) -> str:
    times = ResearchStore(storage.path).chain_observation_times(
        pool_address,
        limit=1,
        ascending=False,
    )
    if not times:
        raise ValueError(f"pool {pool_address} has no chain observations")
    return times[0]


def assess_live_pool_safety(
    storage: Storage,
    *,
    pool_address: str,
    observed_at: str,
    config: PoolSafetyConfig = PoolSafetyConfig(),
) -> LivePoolSafety:
    latest = _latest_observation(storage, pool_address=pool_address)
    if latest != observed_at:
        return LivePoolSafety(
            pool_address=pool_address,
            safe=False,
            reason=(
                f"observation {observed_at} is not latest local chain state "
                f"{latest}"
            ),
            assessment=None,
        )

    try:
        report = screen_pool_universe(
            str(storage.path),
            config=config,
            as_of=observed_at,
        )
    except ValueError as exc:
        return LivePoolSafety(
            pool_address=pool_address,
            safe=False,
            reason=f"pool safety unavailable: {exc}",
            assessment=None,
        )

    assessment = next(
        (
            item
            for item in report.assessments
            if item.pool_address == pool_address
        ),
        None,
    )
    if assessment is None:
        return LivePoolSafety(
            pool_address=pool_address,
            safe=False,
            reason="pool is absent from the latest normalized safety universe",
            assessment=None,
        )

    return LivePoolSafety(
        pool_address=pool_address,
        safe=assessment.accepted,
        reason=(
            None
            if assessment.accepted
            else "; ".join(assessment.rejection_reasons)
        ),
        assessment=assessment,
    )


def apply_live_chain_paper_observation(
    storage: Storage,
    *,
    position_id: str,
    observed_at: str,
    token_y_quote_per_atomic: float,
    estimated_exit_cost_quote: float = 0.0,
    rebalance_cost_quote: float | None = None,
    emergency_exit: bool = False,
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
    management_config: PositionManagementConfig = PositionManagementConfig(),
) -> LivePaperChainResult:
    """
    Apply one latest-chain paper observation with fail-closed local pool safety.

    The caller cannot mark a pool safe. Safety is derived from the latest
    normalized pool universe and latest local chain observation.
    """
    position = paper_position_snapshot(storage, position_id=position_id)
    if position.status != "OPEN":
        raise ValueError("paper position must be open")

    safety = assess_live_pool_safety(
        storage,
        pool_address=position.pool_address,
        observed_at=observed_at,
        config=safety_config,
    )

    result = apply_chain_paper_observation(
        storage,
        position_id=position_id,
        observed_at=observed_at,
        token_y_quote_per_atomic=token_y_quote_per_atomic,
        pool_safe=safety.safe,
        estimated_exit_cost_quote=estimated_exit_cost_quote,
        rebalance_cost_quote=rebalance_cost_quote,
        emergency_exit=emergency_exit,
        config=management_config,
    )
    return LivePaperChainResult(
        position_id=position_id,
        observed_at=observed_at,
        safety=safety,
        executed_action=result.executed_action,
        detail=result.to_record(),
    )


def run_live_chain_paper_batch(
    storage: Storage,
    *,
    run_id: str,
    observed_at: str,
    items: Sequence[LivePaperChainBatchItem],
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
    management_config: PositionManagementConfig = PositionManagementConfig(),
    retry_failed: bool = False,
) -> PaperBatchRunReport:
    """
    Apply the idempotent multi-position chain runner with derived pool safety.

    Safety results become part of the stored run input through the lower-level
    PaperChainBatchItem payload, so replaying a run_id cannot silently change the
    safety decision.
    """
    if not items:
        raise ValueError("at least one live chain paper item is required")

    safety_by_pool: dict[str, LivePoolSafety] = {}
    lower: list[PaperChainBatchItem] = []
    for item in items:
        position = paper_position_snapshot(
            storage,
            position_id=item.position_id,
        )
        safety = safety_by_pool.get(position.pool_address)
        if safety is None:
            safety = assess_live_pool_safety(
                storage,
                pool_address=position.pool_address,
                observed_at=observed_at,
                config=safety_config,
            )
            safety_by_pool[position.pool_address] = safety

        lower.append(
            PaperChainBatchItem(
                position_id=item.position_id,
                token_y_quote_per_atomic=item.token_y_quote_per_atomic,
                pool_safe=safety.safe,
                emergency_exit=item.emergency_exit,
                estimated_exit_cost_quote=item.estimated_exit_cost_quote,
                rebalance_cost_quote=item.rebalance_cost_quote,
            )
        )

    return run_chain_paper_batch(
        storage,
        run_id=run_id,
        observed_at=observed_at,
        items=tuple(lower),
        config=management_config,
        retry_failed=retry_failed,
    )
