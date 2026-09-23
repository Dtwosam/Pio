from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .collector import collect_once
from .phase9_chain_capture import run_phase9_chain_capture_batch
from .phase9_capture_plan import Phase9ChainCaptureCriteria
from .phase9_history_capture import run_phase9_history_capture
from .phase9_history_plan import build_phase9_history_plan
from .phase9_pool_cohort import (
    Phase9PoolCohortCriteria,
    evaluate_phase9_pool_cohort,
)
from .phase9_mint_capture import (
    Phase9MintCaptureCriteria,
    build_phase9_mint_capture_plan,
    run_phase9_mint_capture,
)
from .phase9_validation import Phase9ResearchBundleCriteria
from .phase9_wallet_flow_capture import (
    run_phase9_wallet_flow_capture,
    wallet_flow_source_state,
)
from .settings import Settings
from .storage import Storage, utc_now_iso
from .wallet_flow import WalletFlowCriteria


@dataclass(frozen=True)
class Phase9SourceCaptureReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    api_refresh: dict[str, Any] | None
    chain_capture: dict[str, Any] | None
    history_capture: dict[str, Any] | None
    mint_capture: dict[str, Any] | None
    wallet_flow_captures: tuple[dict[str, Any], ...]
    pool_cohort: dict[str, Any] | None
    selected_wallet_pools: tuple[str, ...]
    chain_history_ready: bool
    mint_inputs_ready: bool
    wallet_source_ready_pools: int
    wallet_source_required_pools: int
    automatic_source_ready: bool
    errors: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _top_chain_pools(
    storage: Storage,
    *,
    limit: int,
) -> tuple[str, ...]:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT pool_address, COUNT(*) AS observations
            FROM chain_pool_snapshots
            WHERE pool_address IS NOT NULL
              AND TRIM(pool_address) != ''
            GROUP BY pool_address
            ORDER BY observations DESC, pool_address ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def run_phase9_source_capture(
    storage: Storage,
    *,
    settings: Settings | None = None,
    criteria: Phase9ResearchBundleCriteria = (
        Phase9ResearchBundleCriteria()
    ),
    refresh_api: bool = True,
    chain_pool_target: int = 3,
    chain_max_candidates: int = 8,
    cohort_target_pools: int = 5,
    cohort_max_sampling_pools: int = 8,
    cohort_new_pools_per_run: int = 1,
    api_ranking_max_age_seconds: int = 10_800,
    bin_array_radius: int = 1,
    mint_max_snapshot_age_seconds: int = 3600,
    history_min_observation_interval_seconds: int = 3600,
    wallet_discovery_limit: int = 250,
    wallet_max_positions_per_run: int = 50,
    wallet_enable_historical_activity: bool = True,
    wallet_historical_signature_limit: int = 25,
    wallet_expand_closed_positions: bool = True,
    wallet_owner_expansion_limit: int = 25,
    wallet_owner_position_max_pages: int = 3,
    rust_manifest_path: str | Path | None = None,
    rust_binary_path: str | Path | None = None,
    timeout_seconds: int = 120,
) -> Phase9SourceCaptureReport:
    if chain_pool_target < 3:
        raise ValueError("chain_pool_target must be at least 3")
    if chain_max_candidates < 1:
        raise ValueError("chain_max_candidates must be positive")
    if cohort_target_pools < chain_pool_target:
        raise ValueError(
            "cohort_target_pools cannot be below chain_pool_target"
        )
    if cohort_max_sampling_pools < cohort_target_pools:
        raise ValueError(
            "cohort_max_sampling_pools cannot be below cohort_target_pools"
        )
    if cohort_new_pools_per_run < 1:
        raise ValueError("cohort_new_pools_per_run must be positive")
    if api_ranking_max_age_seconds < 0:
        raise ValueError(
            "api_ranking_max_age_seconds cannot be negative"
        )
    if bin_array_radius < 0:
        raise ValueError("bin_array_radius cannot be negative")
    if history_min_observation_interval_seconds < 0:
        raise ValueError(
            "history_min_observation_interval_seconds cannot be negative"
        )
    if (
        wallet_historical_signature_limit < 1
        or wallet_historical_signature_limit > 1_000
    ):
        raise ValueError(
            "wallet_historical_signature_limit must be between 1 and 1000"
        )
    if wallet_owner_expansion_limit < 1:
        raise ValueError("wallet_owner_expansion_limit must be positive")
    if wallet_owner_position_max_pages < 1:
        raise ValueError("wallet_owner_position_max_pages must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    current_settings = settings or Settings.from_env()
    if Path(current_settings.database_path) != Path(storage.path):
        raise ValueError(
            "source capture settings database_path must match storage"
        )

    capture_as_of = utc_now_iso()
    errors: list[str] = []
    api_record = None
    chain_record = None
    history_record = None
    mint_record = None
    wallet_records: list[dict[str, Any]] = []
    cohort_record = None

    if refresh_api:
        try:
            api_record = collect_once(current_settings).__dict__
        except Exception as exc:
            errors.append(
                "api refresh failed: "
                f"{type(exc).__name__}: {str(exc)[:1000]}"
            )

    try:
        cohort_before = evaluate_phase9_pool_cohort(
            storage,
            criteria=Phase9PoolCohortCriteria(
                min_research_pools=chain_pool_target,
                target_pools=cohort_target_pools,
                max_sampling_pools=cohort_max_sampling_pools,
                max_api_snapshot_age_seconds=(
                    api_ranking_max_age_seconds
                ),
            as_of=capture_as_of,
            ),
        )
        chain = run_phase9_chain_capture_batch(
            storage,
            criteria=Phase9ChainCaptureCriteria(
                target_chain_pools=chain_pool_target,
                max_candidates=chain_max_candidates,
                bin_array_radius=bin_array_radius,
                max_api_snapshot_age_seconds=(
                    api_ranking_max_age_seconds
                ),
            ),
            rust_manifest_path=rust_manifest_path,
            rust_binary_path=rust_binary_path,
            timeout_seconds=timeout_seconds,
            preferred_pool_addresses=cohort_before.desired_pools,
            max_preferred_candidates=cohort_new_pools_per_run,
            api_ranking_as_of=capture_as_of,
        )
        chain_record = chain.to_record()
    except Exception as exc:
        errors.append(
            "chain capture failed: "
            f"{type(exc).__name__}: {str(exc)[:1000]}"
        )

    try:
        cohort_after_chain = evaluate_phase9_pool_cohort(
            storage,
            criteria=Phase9PoolCohortCriteria(
                min_research_pools=chain_pool_target,
                target_pools=cohort_target_pools,
                max_sampling_pools=cohort_max_sampling_pools,
                max_api_snapshot_age_seconds=(
                    api_ranking_max_age_seconds
                ),
            as_of=capture_as_of,
            ),
        )
        cohort_record = cohort_after_chain.to_record()
        history = run_phase9_history_capture(
            storage,
            bin_array_radius=bin_array_radius,
            rust_manifest_path=rust_manifest_path,
            rust_binary_path=rust_binary_path,
            timeout_seconds=timeout_seconds,
            min_observation_interval_seconds=(
                history_min_observation_interval_seconds
            ),
            continue_sampling_when_ready=True,
            pool_addresses=cohort_after_chain.sampling_pools,
        )
        history_record = history.to_record()
    except Exception as exc:
        errors.append(
            "history capture failed: "
            f"{type(exc).__name__}: {str(exc)[:1000]}"
        )

    try:
        mint = run_phase9_mint_capture(
            storage,
            criteria=Phase9MintCaptureCriteria(
                target_pools=criteria.min_mint_risk_pools,
                max_snapshot_age_seconds=(
                    mint_max_snapshot_age_seconds
                ),
                include_reward_mints=True,
            ),
            rust_manifest_path=rust_manifest_path,
            rust_binary_path=rust_binary_path,
            timeout_seconds=timeout_seconds,
        )
        mint_record = mint.to_record()
    except Exception as exc:
        errors.append(
            "mint capture failed: "
            f"{type(exc).__name__}: {str(exc)[:1000]}"
        )

    wallet_pools = _top_chain_pools(
        storage,
        limit=criteria.min_wallet_flow_pools,
    )
    for pool in wallet_pools:
        try:
            report = run_phase9_wallet_flow_capture(
                storage,
                pool_address=pool,
                criteria=WalletFlowCriteria(),
                discovery_limit=wallet_discovery_limit,
                max_positions_per_run=wallet_max_positions_per_run,
                enable_historical_activity=(
                    wallet_enable_historical_activity
                ),
                historical_signature_limit=(
                    wallet_historical_signature_limit
                ),
                expand_closed_positions=wallet_expand_closed_positions,
                owner_expansion_limit=wallet_owner_expansion_limit,
                owner_position_max_pages=wallet_owner_position_max_pages,
                settings=current_settings,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
            )
            wallet_records.append(report.to_record())
        except Exception as exc:
            errors.append(
                f"wallet-flow capture failed for {pool}: "
                f"{type(exc).__name__}: {str(exc)[:1000]}"
            )

    final_cohort = evaluate_phase9_pool_cohort(
        storage,
        criteria=Phase9PoolCohortCriteria(
            min_research_pools=chain_pool_target,
            target_pools=cohort_target_pools,
            max_sampling_pools=cohort_max_sampling_pools,
        ),
    as_of=capture_as_of,
    )
    cohort_record = final_cohort.to_record()
    history_plan = (
        build_phase9_history_plan(
            storage,
            pool_addresses=final_cohort.research_pools,
        )
        if final_cohort.research_pools
        else build_phase9_history_plan(storage)
    )
    mint_plan = build_phase9_mint_capture_plan(
        storage,
        criteria=Phase9MintCaptureCriteria(
            target_pools=criteria.min_mint_risk_pools,
            max_snapshot_age_seconds=mint_max_snapshot_age_seconds,
            include_reward_mints=True,
        ),
    )
    wallet_ready = sum(
        wallet_flow_source_state(
            storage,
            pool_address=pool,
            criteria=WalletFlowCriteria(),
        ).ready
        for pool in wallet_pools
    )
    automatic_ready = (
        history_plan.plan_ready
        and mint_plan.inputs_ready
        and len(wallet_pools) >= criteria.min_wallet_flow_pools
        and wallet_ready >= criteria.min_wallet_flow_pools
    )

    return Phase9SourceCaptureReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        api_refresh=api_record,
        chain_capture=chain_record,
        history_capture=history_record,
        mint_capture=mint_record,
        wallet_flow_captures=tuple(wallet_records),
        pool_cohort=cohort_record,
        selected_wallet_pools=wallet_pools,
        chain_history_ready=history_plan.plan_ready,
        mint_inputs_ready=mint_plan.inputs_ready,
        wallet_source_ready_pools=wallet_ready,
        wallet_source_required_pools=criteria.min_wallet_flow_pools,
        automatic_source_ready=automatic_ready,
        errors=tuple(errors),
    )
