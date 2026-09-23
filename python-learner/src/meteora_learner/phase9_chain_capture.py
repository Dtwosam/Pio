from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Sequence

from .chain_ingest import ingest_chain_snapshot
from .paper_chain_refresh import inspect_pool_with_rust
from .phase9_capture_plan import (
    Phase9ChainCaptureCriteria,
    build_phase9_chain_capture_plan,
)
from .storage import Storage, utc_now_iso


InspectPool = Callable[[str, int], dict[str, Any]]


@dataclass(frozen=True)
class Phase9ChainCaptureItem:
    pool_address: str
    rank: int
    status: str
    bin_arrays: int | None
    bins: int | None
    error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9ChainCaptureBatchReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    target_chain_pool_count: int
    current_chain_pool_count_before: int
    current_chain_pool_count_after: int
    candidates_planned: int
    pools_attempted: int
    pools_captured: int
    pools_failed: int
    target_met: bool
    preferred_target_met: bool
    criteria: Phase9ChainCaptureCriteria
    items: tuple[Phase9ChainCaptureItem, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def run_phase9_chain_capture_batch(
    storage: Storage,
    *,
    criteria: Phase9ChainCaptureCriteria = Phase9ChainCaptureCriteria(),
    inspector: InspectPool | None = None,
    rust_manifest_path: str | None = None,
    rust_binary_path: str | None = None,
    timeout_seconds: int = 120,
    ingest_observed_at: str | None = None,
    preferred_pool_addresses: Sequence[str] | None = None,
    max_preferred_candidates: int | None = None,
    api_ranking_as_of: str | None = None,
) -> Phase9ChainCaptureBatchReport:
    criteria.validate()
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    ranking_as_of = api_ranking_as_of or utc_now_iso()
    plan = build_phase9_chain_capture_plan(
        storage,
        criteria=criteria,
        preferred_pool_addresses=preferred_pool_addresses,
        max_preferred_candidates=max_preferred_candidates,
        as_of=ranking_as_of,
    )
    before = plan.current_chain_pool_count

    if inspector is None:
        def inspect(pool: str, radius: int) -> dict[str, Any]:
            return inspect_pool_with_rust(
                pool,
                radius,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
            )
    else:
        inspect = inspector

    items: list[Phase9ChainCaptureItem] = []
    captured = 0
    failed = 0

    for candidate in plan.candidates:
        try:
            payload = inspect(
                candidate.pool_address,
                criteria.bin_array_radius,
            )
            if str(payload.get("pool_address", "")).strip() != (
                candidate.pool_address
            ):
                raise ValueError(
                    "Rust inspector returned a different pool_address"
                )
            result = ingest_chain_snapshot(
                storage,
                payload,
                observed_at=ingest_observed_at,
            )
            captured += 1
            items.append(
                Phase9ChainCaptureItem(
                    pool_address=candidate.pool_address,
                    rank=candidate.rank,
                    status="CAPTURED",
                    bin_arrays=result.bin_arrays,
                    bins=result.bins,
                    error=None,
                )
            )
        except Exception as exc:
            failed += 1
            items.append(
                Phase9ChainCaptureItem(
                    pool_address=candidate.pool_address,
                    rank=candidate.rank,
                    status="FAILED",
                    bin_arrays=None,
                    bins=None,
                    error=str(exc)[:2000],
                )
            )

    refreshed = build_phase9_chain_capture_plan(
        storage,
        criteria=criteria,
        preferred_pool_addresses=preferred_pool_addresses,
        max_preferred_candidates=max_preferred_candidates,
        as_of=ranking_as_of,
    )
    after = refreshed.current_chain_pool_count
    target_met = after >= criteria.target_chain_pools
    preferred_target_met = not refreshed.preferred_missing_chain_pools
    reasons: list[str] = []
    if not plan.capture_required:
        reasons.append(
            "target chain-observed pool count was already satisfied"
        )
    if failed:
        reasons.append(
            f"{failed} read-only pool capture(s) failed"
        )
    if not target_met:
        reasons.append(
            f"chain-observed pool count {after} remains below target "
            f"{criteria.target_chain_pools}"
        )
    if not preferred_target_met:
        reasons.append(
            "ranked cohort pool(s) still lack chain capture: "
            + ", ".join(refreshed.preferred_missing_chain_pools)
        )

    return Phase9ChainCaptureBatchReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        target_chain_pool_count=criteria.target_chain_pools,
        current_chain_pool_count_before=before,
        current_chain_pool_count_after=after,
        candidates_planned=len(plan.candidates),
        pools_attempted=len(plan.candidates),
        pools_captured=captured,
        pools_failed=failed,
        target_met=target_met,
        preferred_target_met=preferred_target_met,
        criteria=criteria,
        items=tuple(items),
        reasons=tuple(reasons),
    )
