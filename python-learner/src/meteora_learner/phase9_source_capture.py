from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .collector import collect_once
from .phase9_chain_capture import run_phase9_chain_capture_batch
from .phase9_capture_plan import Phase9ChainCaptureCriteria
from .phase9_history_capture import run_phase9_history_capture
from .phase9_history_plan import build_phase9_history_plan
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
from .storage import Storage
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
    bin_array_radius: int = 1,
    mint_max_snapshot_age_seconds: int = 3600,
    wallet_discovery_limit: int = 250,
    wallet_max_positions_per_run: int = 50,
    rust_manifest_path: str | Path | None = None,
    rust_binary_path: str | Path | None = None,
    timeout_seconds: int = 120,
) -> Phase9SourceCaptureReport:
    if chain_pool_target < 3:
        raise ValueError("chain_pool_target must be at least 3")
    if chain_max_candidates < 1:
        raise ValueError("chain_max_candidates must be positive")
    if bin_array_radius < 0:
        raise ValueError("bin_array_radius cannot be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    current_settings = settings or Settings.from_env()
    if Path(current_settings.database_path) != Path(storage.path):
        raise ValueError(
            "source capture settings database_path must match storage"
        )

    errors: list[str] = []
    api_record = None
    chain_record = None
    history_record = None
    mint_record = None
    wallet_records: list[dict[str, Any]] = []

    if refresh_api:
        try:
            api_record = collect_once(current_settings).__dict__
        except Exception as exc:
            errors.append(
                "api refresh failed: "
                f"{type(exc).__name__}: {str(exc)[:1000]}"
            )

    try:
        chain = run_phase9_chain_capture_batch(
            storage,
            criteria=Phase9ChainCaptureCriteria(
                target_chain_pools=chain_pool_target,
                max_candidates=chain_max_candidates,
                bin_array_radius=bin_array_radius,
            ),
            rust_manifest_path=rust_manifest_path,
            rust_binary_path=rust_binary_path,
            timeout_seconds=timeout_seconds,
        )
        chain_record = chain.to_record()
    except Exception as exc:
        errors.append(
            "chain capture failed: "
            f"{type(exc).__name__}: {str(exc)[:1000]}"
        )

    try:
        history = run_phase9_history_capture(
            storage,
            bin_array_radius=bin_array_radius,
            rust_manifest_path=rust_manifest_path,
            rust_binary_path=rust_binary_path,
            timeout_seconds=timeout_seconds,
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

    history_plan = build_phase9_history_plan(storage)
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
        selected_wallet_pools=wallet_pools,
        chain_history_ready=history_plan.plan_ready,
        mint_inputs_ready=mint_plan.inputs_ready,
        wallet_source_ready_pools=wallet_ready,
        wallet_source_required_pools=criteria.min_wallet_flow_pools,
        automatic_source_ready=automatic_ready,
        errors=tuple(errors),
    )
