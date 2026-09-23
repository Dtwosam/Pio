from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .adaptive_range import AdaptiveRangeCriteria
from .adaptive_range_validation import AdaptiveRangeValidationCriteria
from .chain_ingest import ingest_chain_snapshot
from .market_regime import DLMMRegimeCriteria
from .paper_chain_refresh import inspect_pool_with_rust
from .phase9_history_plan import build_phase9_history_plan
from .phase9_research import Phase9ResearchCriteria
from .storage import Storage


InspectPool = Callable[[str, int], dict[str, Any]]


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("history capture timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _latest_pool_observed_at(
    storage: Storage,
    pool_address: str,
) -> str | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT observed_at
            FROM chain_pool_snapshots
            WHERE pool_address = ?
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (pool_address,),
        ).fetchone()
    return str(row[0]) if row is not None else None


@dataclass(frozen=True)
class Phase9HistoryCaptureItem:
    pool_address: str
    observations_before: int
    observations_after: int
    additional_observations_needed_after: int
    status: str
    latest_observed_at: str | None
    capture_observed_at: str | None
    bin_arrays: int | None
    bins: int | None
    error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9HistoryCaptureReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    pools_attempted: int
    pools_captured: int
    pools_skipped_interval: int
    pools_failed: int
    min_observation_interval_seconds: int
    history_ready_before: bool
    history_ready_after: bool
    observations_remaining_after: int
    items: tuple[Phase9HistoryCaptureItem, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def run_phase9_history_capture(
    storage: Storage,
    *,
    research_criteria: Phase9ResearchCriteria = Phase9ResearchCriteria(),
    adaptive_criteria: AdaptiveRangeCriteria = AdaptiveRangeCriteria(),
    validation_criteria: AdaptiveRangeValidationCriteria = (
        AdaptiveRangeValidationCriteria()
    ),
    regime_criteria: DLMMRegimeCriteria = DLMMRegimeCriteria(),
    bin_array_radius: int = 1,
    inspector: InspectPool | None = None,
    rust_manifest_path: str | None = None,
    rust_binary_path: str | None = None,
    timeout_seconds: int = 120,
    ingest_observed_at: str | None = None,
    min_observation_interval_seconds: int = 0,
) -> Phase9HistoryCaptureReport:
    if bin_array_radius < 0:
        raise ValueError("bin_array_radius cannot be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if min_observation_interval_seconds < 0:
        raise ValueError(
            "min_observation_interval_seconds cannot be negative"
        )

    before = build_phase9_history_plan(
        storage,
        research_criteria=research_criteria,
        adaptive_criteria=adaptive_criteria,
        validation_criteria=validation_criteria,
        regime_criteria=regime_criteria,
        bin_array_radius=bin_array_radius,
    )

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

    attempted_pools = tuple(
        item
        for item in before.pools
        if item.additional_observations_needed > 0
    )
    items: list[Phase9HistoryCaptureItem] = []
    captured = 0
    skipped_interval = 0
    failed = 0

    explicit_observed_at = (
        _parse_time(ingest_observed_at)
        if ingest_observed_at is not None
        else None
    )

    for item in attempted_pools:
        try:
            latest_text = _latest_pool_observed_at(
                storage,
                item.pool_address,
            )
            capture_time = (
                explicit_observed_at
                if explicit_observed_at is not None
                else datetime.now(timezone.utc)
            )
            if latest_text is not None:
                latest_time = _parse_time(latest_text)
                if capture_time <= latest_time:
                    raise ValueError(
                        "history capture observed_at must be newer than "
                        "the latest persisted pool snapshot"
                    )
                elapsed_seconds = (
                    capture_time - latest_time
                ).total_seconds()
                if (
                    min_observation_interval_seconds > 0
                    and elapsed_seconds
                    < min_observation_interval_seconds
                ):
                    skipped_interval += 1
                    items.append(
                        Phase9HistoryCaptureItem(
                            pool_address=item.pool_address,
                            observations_before=item.observations,
                            observations_after=item.observations,
                            additional_observations_needed_after=(
                                item.additional_observations_needed
                            ),
                            status="SKIPPED_INTERVAL",
                            latest_observed_at=latest_text,
                            capture_observed_at=capture_time.isoformat(),
                            bin_arrays=None,
                            bins=None,
                            error=(
                                "latest observation is only "
                                f"{int(elapsed_seconds)}s old; minimum is "
                                f"{min_observation_interval_seconds}s"
                            ),
                        )
                    )
                    continue
            payload = inspect(item.pool_address, bin_array_radius)
            if str(payload.get("pool_address", "")).strip() != (
                item.pool_address
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
                Phase9HistoryCaptureItem(
                    pool_address=item.pool_address,
                    observations_before=item.observations,
                    observations_after=item.observations + 1,
                    additional_observations_needed_after=max(
                        0,
                        item.additional_observations_needed - 1,
                    ),
                    status="CAPTURED",
                    latest_observed_at=latest_text,
                    capture_observed_at=(
                        explicit_observed_at.isoformat()
                        if explicit_observed_at is not None
                        else None
                    ),
                    bin_arrays=result.bin_arrays,
                    bins=result.bins,
                    error=None,
                )
            )
        except Exception as exc:
            failed += 1
            items.append(
                Phase9HistoryCaptureItem(
                    pool_address=item.pool_address,
                    observations_before=item.observations,
                    observations_after=item.observations,
                    additional_observations_needed_after=(
                        item.additional_observations_needed
                    ),
                    status="FAILED",
                    latest_observed_at=_latest_pool_observed_at(
                        storage,
                        item.pool_address,
                    ),
                    capture_observed_at=(
                        explicit_observed_at.isoformat()
                        if explicit_observed_at is not None
                        else None
                    ),
                    bin_arrays=None,
                    bins=None,
                    error=str(exc)[:2000],
                )
            )

    after = build_phase9_history_plan(
        storage,
        research_criteria=research_criteria,
        adaptive_criteria=adaptive_criteria,
        validation_criteria=validation_criteria,
        regime_criteria=regime_criteria,
        bin_array_radius=bin_array_radius,
    )
    remaining = sum(
        item.additional_observations_needed
        for item in after.pools
    )
    reasons: list[str] = []
    if not before.pools:
        reasons.append(
            "no chain-observed Phase 9 pools are available for history capture"
        )
    if len(before.pools) < research_criteria.min_pools:
        reasons.append(
            "chain-pool diversity must be satisfied before history depth "
            "capture can complete"
        )
    if before.plan_ready:
        reasons.append(
            "Phase 9 chain-history depth was already ready"
        )
    if skipped_interval:
        reasons.append(
            f"{skipped_interval} pool(s) skipped because the minimum "
            "observation interval has not elapsed"
        )
    if failed:
        reasons.append(
            f"{failed} read-only history capture(s) failed"
        )
    if not after.plan_ready and remaining:
        reasons.append(
            f"{remaining} additional pool-observation capture(s) remain"
        )

    return Phase9HistoryCaptureReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        pools_attempted=len(attempted_pools),
        pools_captured=captured,
        pools_skipped_interval=skipped_interval,
        pools_failed=failed,
        min_observation_interval_seconds=(
            min_observation_interval_seconds
        ),
        history_ready_before=before.plan_ready,
        history_ready_after=after.plan_ready,
        observations_remaining_after=remaining,
        items=tuple(items),
        reasons=tuple(reasons),
    )
