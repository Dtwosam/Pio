from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from .chain_replay import SmallLPReplayResult, replay_small_lp_history
from .research_store import ResearchStore
from .strategy import StrategyType


@dataclass(frozen=True)
class ChainCandidateOutcome:
    strategy: str
    half_width: int
    center_offset: int
    min_bin_id: int
    max_bin_id: int
    status: str
    rejection_reason: str | None
    range_survival_ratio: float | None
    replay: SmallLPReplayResult | None


@dataclass(frozen=True)
class ChainScanResult:
    pool_address: str
    entry_active_bin_id: int
    decision_active_bin_id: int
    observation_count: int
    attempted: int
    accepted: int
    rejected: int
    candidates: tuple[ChainCandidateOutcome, ...]

    def to_record(self) -> dict[str, object]:
        return asdict(self)


def _range_survival_ratio(
    replay: SmallLPReplayResult,
    min_bin_id: int,
    max_bin_id: int,
) -> float:
    active_ids = [replay.start_active_bin_id]
    active_ids.extend(interval.end_active_bin_id for interval in replay.intervals)
    if not active_ids:
        return 0.0
    in_range = sum(min_bin_id <= active_id <= max_bin_id for active_id in active_ids)
    return in_range / len(active_ids)


def scan_chain_candidates(
    database_path: str,
    *,
    pool_address: str,
    amount_x: int,
    amount_y: int,
    observation_limit: int = 12,
    observation_times: Sequence[str] | None = None,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Iterable[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
) -> ChainScanResult:
    """
    Compare a grid of chain-backed LP candidates without inventing a single score.

    Accepted candidates carry raw ending inventory, fee earnings, entry
    composition fees, range survival and counterfactual-size diagnostics.
    Rejected candidates are retained with the exact fail-closed reason.
    """
    if amount_x < 0 or amount_y < 0:
        raise ValueError("amounts cannot be negative")
    if amount_x == 0 and amount_y == 0:
        raise ValueError("at least one token amount must be positive")
    if observation_limit < 2:
        raise ValueError("observation_limit must be at least 2")
    if observation_times is not None and len(observation_times) < 2:
        raise ValueError("observation_times must contain at least two observations")
    if not half_widths:
        raise ValueError("at least one half width is required")
    if any(width < 0 for width in half_widths):
        raise ValueError("half widths cannot be negative")

    normalized_strategies = [StrategyType(value) for value in strategies]
    if not normalized_strategies:
        raise ValueError("at least one strategy is required")

    store = ResearchStore(database_path)
    if observation_times is None:
        times_desc = store.chain_observation_times(
            pool_address,
            limit=observation_limit,
        )
        if len(times_desc) < 2:
            raise ValueError("need at least two chain observations for scanning")
        selected_times = list(reversed(times_desc))
    else:
        selected_times = [str(value) for value in observation_times]
        if selected_times != sorted(selected_times) or len(set(selected_times)) != len(selected_times):
            raise ValueError(
                "observation_times must be unique and strictly ascending"
            )

    entry_pool = store.chain_pool_snapshot_at(pool_address, selected_times[0])
    if entry_pool is None:
        raise ValueError("entry chain pool snapshot is missing")
    decision_pool = store.chain_pool_snapshot_at(pool_address, selected_times[-1])
    if decision_pool is None:
        raise ValueError("decision chain pool snapshot is missing")
    entry_active = int(entry_pool["active_bin_id"])
    decision_active = int(decision_pool["active_bin_id"])

    outcomes: list[ChainCandidateOutcome] = []
    seen: set[tuple[str, int, int]] = set()

    for half_width in half_widths:
        for center_offset in center_offsets:
            center = entry_active + int(center_offset)
            min_bin_id = center - int(half_width)
            max_bin_id = center + int(half_width)

            for strategy in normalized_strategies:
                key = (strategy.value, min_bin_id, max_bin_id)
                if key in seen:
                    continue
                seen.add(key)

                try:
                    replay = replay_small_lp_history(
                        database_path,
                        pool_address=pool_address,
                        amount_x=amount_x,
                        amount_y=amount_y,
                        min_bin_id=min_bin_id,
                        max_bin_id=max_bin_id,
                        strategy=strategy,
                        observation_limit=observation_limit,
                        observation_times=selected_times,
                        max_share_bps=max_share_bps,
                        favor_x_in_active_bin=favor_x_in_active_bin,
                    )
                    if replay.deposited_x == 0 and replay.deposited_y == 0:
                        raise ValueError("candidate deposits no capital into covered bins")
                except ValueError as exc:
                    outcomes.append(
                        ChainCandidateOutcome(
                            strategy=strategy.value,
                            half_width=int(half_width),
                            center_offset=int(center_offset),
                            min_bin_id=min_bin_id,
                            max_bin_id=max_bin_id,
                            status="REJECTED",
                            rejection_reason=str(exc),
                            range_survival_ratio=None,
                            replay=None,
                        )
                    )
                    continue

                outcomes.append(
                    ChainCandidateOutcome(
                        strategy=strategy.value,
                        half_width=int(half_width),
                        center_offset=int(center_offset),
                        min_bin_id=min_bin_id,
                        max_bin_id=max_bin_id,
                        status="ACCEPTED",
                        rejection_reason=None,
                        range_survival_ratio=_range_survival_ratio(
                            replay,
                            min_bin_id,
                            max_bin_id,
                        ),
                        replay=replay,
                    )
                )

    accepted = sum(item.status == "ACCEPTED" for item in outcomes)
    return ChainScanResult(
        pool_address=pool_address,
        entry_active_bin_id=entry_active,
        decision_active_bin_id=decision_active,
        observation_count=len(selected_times),
        attempted=len(outcomes),
        accepted=accepted,
        rejected=len(outcomes) - accepted,
        candidates=tuple(outcomes),
    )
