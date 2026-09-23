from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import pandas as pd

from .add_execution import build_add_execution_calibration
from .adaptive_range import AdaptiveRangeCriteria, research_adaptive_range
from .adaptive_range_validation import (
    AdaptiveRangeValidationCriteria,
    persist_adaptive_range_validation,
    validate_adaptive_range_walk_forward,
)
from .baseline_policy import BaselinePolicyConfig
from .baseline_walk_forward import walk_forward_baseline
from .calibration_queue import build_calibration_work_queue
from .capital_sizing import CapitalSizingConfig, size_position
from .cross_pool_research import CrossPoolResearchCandidate, CrossPoolResearchReport
from .calibration_status import build_phase2_calibration_evidence
from .chain_ingest import ingest_chain_snapshot
from .chain_replay import replay_small_lp_history
from .chain_scan import scan_chain_candidates
from .composition_labels import build_composition_fee_labels
from .composition_prestate import (
    build_composition_prestate_candidates,
    ingest_prestate_verification,
)
from .composition_reconciliation import build_composition_fee_reconciliation
from .collector import collect_once
from .meteora_api import MeteoraDataAPI
from .position_ingest import ingest_position_snapshot
from .position_history import collect_position_history
from .phase2_gate import Phase2PromotionCriteria, evaluate_phase2_promotion_gate
from .phase_promotion import (
    PHASE2,
    PHASE3,
    PHASE5,
    PHASE6,
    PHASE7,
    PHASE8,
    PHASE9,
    persist_phase2_promotion,
    persist_phase5_promotion,
    persist_phase6_promotion,
    persist_phase7_promotion,
    persist_phase8_promotion,
    persist_phase9_promotion,
    phase_promotion_state,
)
from .phase3_validation import Phase3PromotionCriteria
from .phase5_validation import Phase5PromotionCriteria, evaluate_phase5_promotion
from .phase6_validation import Phase6PromotionCriteria, evaluate_phase6_promotion
from .phase7_validation import Phase7PromotionCriteria, evaluate_phase7_promotion
from .phase8_validation import (
    Phase8PromotionCriteria,
    audit_persisted_phase8_promotion,
    evaluate_phase8_promotion,
)
from .phase9_research import (
    Phase9ResearchCriteria,
    evaluate_phase9_research,
    persist_phase9_research,
)
from .phase9_validation import (
    Phase9ResearchBundleCriteria,
    audit_persisted_phase9_promotion,
    evaluate_phase9_promotion,
    evaluate_phase9_research_bundle,
    persist_phase9_research_bundle,
)
from .phase9_replay_audit import evaluate_phase9_replay_audit
from .phase9_operational_audit import evaluate_phase9_operational_audit
from .phase9_progress import evaluate_phase9_progress
from .phase9_shadow import (
    Phase9ShadowCriteria,
    evaluate_phase9_shadow,
    persist_phase9_shadow,
)
from .phase9_policy_authorization import (
    Phase9PolicyAuthorizationCriteria,
    audit_persisted_phase9_policy_authorization,
    evaluate_phase9_policy_authorization,
    persist_phase9_policy_authorization,
)
from .phase9_policy_controlled_validation import (
    Phase9PolicyControlledValidationCriteria,
    audit_persisted_phase9_policy_controlled_validation,
    evaluate_phase9_policy_controlled_validation,
    persist_phase9_policy_controlled_validation,
)
from .phase9_policy_readiness import (
    evaluate_phase9_policy_readiness,
)
from .phase9_policy_rollout_simulation import (
    Phase9PolicyRolloutEnvelope,
    evaluate_phase9_policy_rollout_simulation,
    persist_phase9_policy_rollout_simulation,
    audit_persisted_phase9_policy_rollout_simulation,
)
from .phase9_policy_rollback_simulation import (
    Phase9PolicyRollbackCriteria,
    Phase9PolicyRollbackMetrics,
    evaluate_phase9_policy_rollback_simulation,
    persist_phase9_policy_rollback_simulation,
    audit_persisted_phase9_policy_rollback_simulation,
)
from .phase9_policy_prewire import (
    evaluate_phase9_policy_prewire_audit,
)
from .phase9_policy_status import (
    evaluate_phase9_policy_status,
)
from .phase9_policy_manifest import (
    audit_persisted_phase9_policy_prewire_manifest,
    evaluate_phase9_policy_prewire_manifest,
    persist_phase9_policy_prewire_manifest,
)
from .phase9_capture_plan import (
    Phase9ChainCaptureCriteria,
    build_phase9_chain_capture_plan,
)
from .phase9_history_plan import build_phase9_history_plan
from .phase9_history_capture import run_phase9_history_capture
from .phase9_mint_capture import (
    Phase9MintCaptureCriteria,
    build_phase9_mint_capture_plan,
    run_phase9_mint_capture,
)
from .phase9_position_discovery import (
    discover_pool_positions_with_rust,
)
from .phase9_pool_activity_discovery import (
    discover_historical_pool_activity_with_rust,
)
from .phase9_pool_activity_scan_state import (
    phase9_pool_activity_scan_state,
    record_phase9_pool_activity_page,
)
from .phase9_wallet_flow_capture import (
    run_phase9_wallet_flow_capture,
)
from .phase9_source_capture import run_phase9_source_capture
from .phase9_research_refresh import run_phase9_research_refresh
from .phase9_source_freshness import (
    evaluate_phase9_source_freshness,
)
from .phase9_operation_lease import (
    acquire_phase9_operation_lease,
    phase9_operation_lease_status,
    release_phase9_operation_lease,
)
from .phase9_evidence_status import evaluate_phase9_evidence_status
from .phase9_evidence_plan import build_phase9_evidence_plan
from .phase9_evidence_step import run_phase9_evidence_step
from .phase9_explicit_inputs import (
    audit_phase9_explicit_inputs,
    build_phase9_explicit_input_template,
    load_phase9_explicit_inputs,
    parse_phase9_explicit_inputs,
    persist_phase9_explicit_inputs,
    run_phase9_explicit_research,
)
from .phase9_bandit_dataset import (
    build_phase9_bandit_dataset,
    evaluate_phase9_contextual_bandit_from_dataset,
    persist_phase9_bandit_dataset,
    persist_phase9_contextual_bandit_from_dataset,
)
from .phase9_chain_capture import run_phase9_chain_capture_batch
from .phase9_storage_integrity import evaluate_phase9_storage_integrity
from .phase9_work_queue import (
    build_phase9_work_queue,
    persist_phase9_work_queue_snapshot,
)
from .phase3_workflow import (
    Phase3ValidationInput,
    validate_phase3_from_chain,
)
from .pool_safety import PoolSafetyConfig, screen_pool_universe
from .position_policy import PositionManagementConfig, decide_position_action
from .portfolio_allocation import (
    PortfolioAllocationCriteria,
    persist_portfolio_allocation_research,
    persist_portfolio_candidate_research,
    research_portfolio_allocation,
)
from .paper_account import (
    close_paper_position,
    create_paper_account,
    mark_paper_position,
    open_paper_position,
    paper_account_snapshot,
    rebalance_paper_position,
)
from .paper_policy import evaluate_paper_position_policy
from .paper_cycle import apply_paper_observation
from .paper_runner import PaperBatchObservation, run_paper_observation_batch
from .paper_chain import (
    apply_chain_paper_observation,
    bind_paper_position_to_chain,
    paper_chain_binding,
    value_paper_position_from_chain,
)
from .paper_chain_runner import PaperChainBatchItem, run_chain_paper_batch
from .paper_live import (
    LivePaperChainBatchItem,
    apply_live_chain_paper_observation,
    run_live_chain_paper_batch,
)
from .paper_latest import (
    LatestPaperCycleItem,
    run_latest_live_paper_cycle,
)
from .paper_portfolio import run_portfolio_live_paper_cycle
from .paper_entry_workflow import build_and_open_bound_phase3_paper_entry
from .paper_chain_collection import build_paper_chain_collection_queue
from .quote_registry import save_token_quote, token_quote_status
from .paper_supervisor import run_paper_supervisor
from .paper_tick import run_paper_tick
from .paper_scheduler import (
    paper_scheduler_state,
    run_scheduled_paper_tick,
)
from .paper_health import build_paper_health, render_paper_health_prometheus
from .paper_endurance import PaperEnduranceCriteria, build_paper_endurance_report
from .paper_audit import audit_paper_ledger
from .paper_performance import build_paper_performance
from .paper_challenger import (
    PaperChallengerCriteria,
    evaluate_paper_challenger,
    promote_paper_challenger,
)
from .phase3_plan import build_phase3_research_plan
from .multi_pool_research import PoolResearchInput, build_multi_pool_research
from .market_regime import DLMMRegimeCriteria, classify_dlmm_regime
from .mint_ingest import ingest_mint_snapshot
from .mint_risk import (
    MintRiskCriteria,
    persist_pool_mint_risk,
    research_pool_mint_risk,
)
from .ml_challenger import MLChallengerCriteria
from .ml_inference import MLInferenceConfig
from .ml_registry import model_record, start_paper_challenger
from .ml_retraining_dataset import MLRetrainPoolSpec
from .retraining_workflow import (
    start_retraining_cycle_with_dataset,
    train_retraining_cycle_challenger,
    evaluate_retraining_cycle_walk_forward,
)
from .ml_walk_forward import MLWalkForwardCriteria
from .ml_workflow import (
    evaluate_registered_offline_challenger,
    qualify_registered_offline_challenger,
    train_save_register_ml_v1,
)
from .reconciliation import reconcile_position
from .reconciliation_corpus import build_reconciliation_corpus
from .rebalance_execution import build_rebalance_execution_calibration
from .rebalance_replay import replay_rebalance_lifecycle
from .research import inventory_backtest_from_store
from .settings import Settings
from .storage import Storage, utc_now_iso
from .strategy import StrategyType
from .static_hedge import (
    HedgeInstrumentAssumptions,
    StaticHedgeCriteria,
    persist_static_hedge_research,
    research_static_inventory_hedge,
)
from .transaction_event_ingest import ingest_transaction_events
from .execution_receipt_ingest import ingest_execution_receipt
from .execution_decision_context_ingest import ingest_execution_decision_context
from .execution_receipt_audit import audit_execution_receipts
from .live_execution_effects import apply_live_execution_effect
from .live_position_ledger import apply_live_position_effect
from .live_position_closure import finalize_live_position_closure
from .live_position_outcome import build_live_position_outcome
from .live_position_valuation import value_live_position_outcome
from .live_learning_label import build_live_learning_label
from .live_execution_audit import audit_live_execution_ledger
from .contextual_bandit import (
    ContextualBanditCriteria,
    bandit_examples_from_records,
    evaluate_contextual_bandit,
    persist_contextual_bandit_research,
)
from .contextual_bandit_cycle import (
    evaluate_cycle_contextual_bandit,
    persist_cycle_contextual_bandit,
)
from .continuous_learning import (
    ContinuousLearningCriteria,
    build_continuous_learning_plan,
    persist_continuous_learning_plan,
)
from .continuous_promotion import (
    ContinuousChampionCriteria,
    evaluate_continuous_champion,
    promote_continuous_challenger,
)
from .retraining_cycle import (
    active_retraining_cycle,
    attach_retraining_challenger,
    retraining_cycle,
    start_retraining_cycle,
    sync_retraining_cycle,
    cancel_retraining_cycle,
)
from .live_champion_monitor import (
    LiveChampionCriteria,
    evaluate_live_champion,
    persist_live_champion_report,
    rollback_live_champion,
)
from .transaction_costs import build_transaction_cost_report
from .wallet_flow import (
    WalletFlowCriteria,
    persist_wallet_flow_research,
    research_wallet_flow,
)


def _parse_int_csv(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not parsed:
        raise argparse.ArgumentTypeError("at least one integer is required")
    return parsed


def _parse_strategy_csv(value: str) -> tuple[StrategyType, ...]:
    try:
        parsed = tuple(
            StrategyType(item.strip())
            for item in value.split(",")
            if item.strip()
        )
    except ValueError as exc:
        allowed = ", ".join(item.value for item in StrategyType)
        raise argparse.ArgumentTypeError(
            f"expected comma-separated strategies from: {allowed}"
        ) from exc
    if not parsed:
        raise argparse.ArgumentTypeError("at least one strategy is required")
    return parsed


def _portfolio_candidate_records(raw: object) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        if isinstance(raw.get("candidates"), list):
            records = raw["candidates"]
        elif (
            isinstance(raw.get("comparison"), dict)
            and isinstance(
                raw["comparison"].get("candidates"),
                list,
            )
        ):
            records = raw["comparison"]["candidates"]
        else:
            records = None
    else:
        records = None

    if not isinstance(records, list):
        raise ValueError(
            "portfolio allocation file must contain a JSON candidate array "
            "or a multi-pool report with comparison.candidates"
        )
    if any(not isinstance(item, dict) for item in records):
        raise ValueError(
            "portfolio allocation candidates must be JSON objects"
        )
    return records


def _pool_safety_config_from_args(args: argparse.Namespace) -> PoolSafetyConfig:
    return PoolSafetyConfig(
        min_tvl_usd=args.min_tvl_usd,
        min_volume_24h_usd=args.min_volume_24h_usd,
        min_pool_age_hours=args.min_pool_age_hours,
        min_chain_observations=args.min_chain_observations,
        max_dynamic_fee_pct=args.max_dynamic_fee_pct,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="pio")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("collect-once", help="Collect one Meteora market-data snapshot")
    subparsers.add_parser("protocol-metrics", help="Print current Meteora protocol metrics")
    subparsers.add_parser("data-status", help="Print local market-data coverage and quality status")

    pool_screen = subparsers.add_parser(
        "screen-pools",
        help="Apply fail-closed safety filters to the latest pool universe",
    )
    pool_screen.add_argument("--min-tvl-usd", type=float, default=50000.0)
    pool_screen.add_argument("--min-volume-24h-usd", type=float, default=10000.0)
    pool_screen.add_argument("--min-pool-age-hours", type=float, default=24.0)
    pool_screen.add_argument("--min-chain-observations", type=int, default=12)
    pool_screen.add_argument("--max-dynamic-fee-pct", type=float, default=5.0)

    size_position_cmd = subparsers.add_parser(
        "size-position",
        help="Apply deterministic portfolio and drawdown capital caps",
    )
    size_position_cmd.add_argument("--equity", required=True, type=float)
    size_position_cmd.add_argument("--cash", required=True, type=float)
    size_position_cmd.add_argument("--deployed", required=True, type=float)
    size_position_cmd.add_argument("--drawdown-bps", required=True, type=int)
    size_position_cmd.add_argument("--requested", type=float)
    size_position_cmd.add_argument("--max-position-bps", type=int, default=1000)
    size_position_cmd.add_argument("--max-deployed-bps", type=int, default=7000)
    size_position_cmd.add_argument("--reserve-bps", type=int, default=3000)
    size_position_cmd.add_argument("--soft-drawdown-bps", type=int, default=500)
    size_position_cmd.add_argument("--hard-drawdown-bps", type=int, default=1500)
    size_position_cmd.add_argument(
        "--drawdown-size-multiplier-bps",
        type=int,
        default=5000,
    )
    size_position_cmd.add_argument("--min-position", type=float, default=0.0)

    manage_position = subparsers.add_parser(
        "manage-position",
        help="Evaluate deterministic HOLD/REBALANCE/EXIT rules",
    )
    manage_position.add_argument("--active-bin", required=True, type=int)
    manage_position.add_argument("--min-bin", required=True, type=int)
    manage_position.add_argument("--max-bin", required=True, type=int)
    manage_position.add_argument("--holding-observations", required=True, type=int)
    manage_position.add_argument("--rebalances-done", required=True, type=int)
    manage_position.add_argument("--net-pnl-bps", type=int)
    manage_position.add_argument("--pool-unsafe", action="store_true")
    manage_position.add_argument("--emergency-exit", action="store_true")
    manage_position.add_argument("--stop-loss-bps", type=int, default=500)
    manage_position.add_argument("--take-profit-bps", type=int)
    manage_position.add_argument("--max-rebalances", type=int, default=3)
    manage_position.add_argument("--max-holding-observations", type=int)
    manage_position.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )

    paper_scheduler_run = subparsers.add_parser(
        "paper-scheduler-run",
        help="Run one leased, time-bucketed paper tick for cron/systemd",
    )
    paper_scheduler_run.add_argument("--account", required=True)
    paper_scheduler_run.add_argument("--interval-seconds", type=int, default=300)
    paper_scheduler_run.add_argument("--lease-seconds", type=int, default=900)
    paper_scheduler_run.add_argument("--owner-id")
    paper_scheduler_run.add_argument("--max-chain-age-seconds", type=int, default=300)
    paper_scheduler_run.add_argument("--max-quote-age-seconds", type=int, default=300)
    paper_scheduler_run.add_argument("--array-radius", type=int, default=1)
    paper_scheduler_run.add_argument("--max-positions", type=int)
    paper_scheduler_run.add_argument("--retry-failed", action="store_true")
    paper_scheduler_run.add_argument("--refresh-jupiter-quotes", action="store_true")
    paper_scheduler_run.add_argument("--stop-loss-bps", type=int, default=500)
    paper_scheduler_run.add_argument("--take-profit-bps", type=int)
    paper_scheduler_run.add_argument("--max-rebalances", type=int, default=3)
    paper_scheduler_run.add_argument("--max-holding-observations", type=int)
    paper_scheduler_run.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )
    paper_scheduler_run.add_argument("--min-tvl-usd", type=float, default=50000.0)
    paper_scheduler_run.add_argument("--min-volume-24h-usd", type=float, default=10000.0)
    paper_scheduler_run.add_argument("--min-pool-age-hours", type=float, default=24.0)
    paper_scheduler_run.add_argument("--min-chain-observations", type=int, default=12)
    paper_scheduler_run.add_argument("--max-dynamic-fee-pct", type=float, default=5.0)

    paper_health = subparsers.add_parser(
        "paper-health",
        help="Report PAPER scheduler, tick, chain and quote health",
    )
    paper_health.add_argument("--account", required=True)
    paper_health.add_argument("--max-tick-age-seconds", type=int, default=600)
    paper_health.add_argument("--max-chain-age-seconds", type=int, default=300)
    paper_health.add_argument("--max-quote-age-seconds", type=int, default=300)
    paper_health.add_argument("--max-consecutive-failures", type=int, default=2)
    paper_health.add_argument("--array-radius", type=int, default=1)
    paper_health.add_argument(
        "--format",
        choices=["json", "prometheus"],
        default="json",
    )
    paper_health.add_argument("--require-healthy", action="store_true")

    paper_audit = subparsers.add_parser(
        "paper-audit",
        help="Reconcile PAPER account/position state against the event ledger",
    )
    paper_audit.add_argument("--account", required=True)
    paper_audit.add_argument("--require-passing", action="store_true")

    paper_endurance = subparsers.add_parser(
        "paper-endurance-report",
        help="Evaluate accumulated PAPER endurance and restart evidence",
    )
    paper_endurance.add_argument("--account", required=True)
    paper_endurance.add_argument("--min-runtime-hours", type=float, default=24.0)
    paper_endurance.add_argument("--min-terminal-ticks", type=int, default=100)
    paper_endurance.add_argument("--min-success-rate-pct", type=float, default=95.0)
    paper_endurance.add_argument(
        "--max-dependency-blocked-pct",
        type=float,
        default=10.0,
    )
    paper_endurance.add_argument("--max-consecutive-failures", type=int, default=2)
    paper_endurance.add_argument("--max-stale-running-ticks", type=int, default=0)
    paper_endurance.add_argument(
        "--stale-running-after-seconds",
        type=int,
        default=900,
    )
    paper_endurance.add_argument(
        "--min-applied-chain-valuations",
        type=int,
        default=24,
    )
    paper_endurance.add_argument(
        "--min-distinct-positions-valued",
        type=int,
        default=1,
    )
    paper_endurance.add_argument("--require-passing", action="store_true")

    paper_scheduler_status = subparsers.add_parser(
        "paper-scheduler-status",
        help="Show persisted paper scheduler lease and health state",
    )
    paper_scheduler_status.add_argument("--account", required=True)

    paper_tick = subparsers.add_parser(
        "paper-tick",
        help="Refresh open-paper market/chain state and run one idempotent paper cycle",
    )
    paper_tick.add_argument("--account", required=True)
    paper_tick.add_argument("--tick-id", required=True)
    paper_tick.add_argument("--max-chain-age-seconds", type=int, default=300)
    paper_tick.add_argument("--max-quote-age-seconds", type=int, default=300)
    paper_tick.add_argument("--array-radius", type=int, default=1)
    paper_tick.add_argument("--max-positions", type=int)
    paper_tick.add_argument("--retry-failed", action="store_true")
    paper_tick.add_argument("--refresh-jupiter-quotes", action="store_true")
    paper_tick.add_argument("--stop-loss-bps", type=int, default=500)
    paper_tick.add_argument("--take-profit-bps", type=int)
    paper_tick.add_argument("--max-rebalances", type=int, default=3)
    paper_tick.add_argument("--max-holding-observations", type=int)
    paper_tick.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )
    paper_tick.add_argument("--min-tvl-usd", type=float, default=50000.0)
    paper_tick.add_argument("--min-volume-24h-usd", type=float, default=10000.0)
    paper_tick.add_argument("--min-pool-age-hours", type=float, default=24.0)
    paper_tick.add_argument("--min-chain-observations", type=int, default=12)
    paper_tick.add_argument("--max-dynamic-fee-pct", type=float, default=5.0)

    paper_supervise = subparsers.add_parser(
        "paper-supervise",
        help="Run safe paper positions and report stale chain/quote dependencies",
    )
    paper_supervise.add_argument("--account", required=True)
    paper_supervise.add_argument("--cycle-id", required=True)
    paper_supervise.add_argument("--max-chain-age-seconds", type=int, default=300)
    paper_supervise.add_argument("--max-quote-age-seconds", type=int, default=300)
    paper_supervise.add_argument("--array-radius", type=int, default=1)
    paper_supervise.add_argument("--max-positions", type=int)
    paper_supervise.add_argument("--retry-failed", action="store_true")
    paper_supervise.add_argument("--stop-loss-bps", type=int, default=500)
    paper_supervise.add_argument("--take-profit-bps", type=int)
    paper_supervise.add_argument("--max-rebalances", type=int, default=3)
    paper_supervise.add_argument("--max-holding-observations", type=int)
    paper_supervise.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )
    paper_supervise.add_argument("--min-tvl-usd", type=float, default=50000.0)
    paper_supervise.add_argument("--min-volume-24h-usd", type=float, default=10000.0)
    paper_supervise.add_argument("--min-pool-age-hours", type=float, default=24.0)
    paper_supervise.add_argument("--min-chain-observations", type=int, default=12)
    paper_supervise.add_argument("--max-dynamic-fee-pct", type=float, default=5.0)

    paper_quote_ingest = subparsers.add_parser(
        "paper-quote-ingest",
        help="Persist one timestamped token quote for paper valuation",
    )
    paper_quote_ingest.add_argument("--mint", required=True)
    paper_quote_ingest.add_argument("--quote-per-atomic", required=True, type=float)
    paper_quote_ingest.add_argument("--source", required=True)
    paper_quote_ingest.add_argument("--observed-at")

    paper_quote_status = subparsers.add_parser(
        "paper-quote-status",
        help="Show latest persisted paper quote and freshness",
    )
    paper_quote_status.add_argument("--mint", required=True)
    paper_quote_status.add_argument("--max-age-seconds", type=int, default=300)

    paper_chain_queue = subparsers.add_parser(
        "paper-chain-work-queue",
        help="Emit read-only Rust inspect-pool tasks for missing/stale open-paper chain state",
    )
    paper_chain_queue.add_argument("--account")
    paper_chain_queue.add_argument("--max-age-seconds", type=int, default=300)
    paper_chain_queue.add_argument("--array-radius", type=int, default=1)

    paper_phase3_open = subparsers.add_parser(
        "paper-open-phase3",
        help="Build an authorized Phase 3 plan from the paper account and atomically chain-bind it",
    )
    paper_phase3_open.add_argument("--account", required=True)
    paper_phase3_open.add_argument("--position", required=True)
    paper_phase3_open.add_argument("--event-key", required=True)
    paper_phase3_open.add_argument("--pool", required=True)
    paper_phase3_open.add_argument("--amount-x", required=True, type=int)
    paper_phase3_open.add_argument("--amount-y", required=True, type=int)
    paper_phase3_open.add_argument("--requested-quote", required=True, type=float)
    paper_phase3_open.add_argument("--network-cost-y-atomic", required=True, type=int)
    paper_phase3_open.add_argument("--entry-cost", type=float, default=0.0)
    paper_phase3_open.add_argument("--observations", type=int, default=12)
    paper_phase3_open.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(0, 1, 2, 5, 10),
    )
    paper_phase3_open.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
    )
    paper_phase3_open.add_argument(
        "--strategies",
        type=_parse_strategy_csv,
        default=tuple(StrategyType),
    )
    paper_phase3_open.add_argument("--max-share-bps", type=int, default=500)
    paper_phase3_open.add_argument("--favor-x-active", action="store_true")
    paper_phase3_open.add_argument("--max-position-bps", type=int, default=1000)
    paper_phase3_open.add_argument("--max-deployed-bps", type=int, default=7000)
    paper_phase3_open.add_argument("--reserve-bps", type=int, default=3000)
    paper_phase3_open.add_argument("--soft-drawdown-bps", type=int, default=500)
    paper_phase3_open.add_argument("--hard-drawdown-bps", type=int, default=1500)
    paper_phase3_open.add_argument(
        "--drawdown-size-multiplier-bps",
        type=int,
        default=5000,
    )
    paper_phase3_open.add_argument("--min-position", type=float, default=0.0)
    paper_phase3_open.add_argument("--min-tvl-usd", type=float, default=50000.0)
    paper_phase3_open.add_argument("--min-volume-24h-usd", type=float, default=10000.0)
    paper_phase3_open.add_argument("--min-pool-age-hours", type=float, default=24.0)
    paper_phase3_open.add_argument("--min-chain-observations", type=int, default=12)
    paper_phase3_open.add_argument("--max-dynamic-fee-pct", type=float, default=5.0)

    paper_create = subparsers.add_parser(
        "paper-create-account",
        help="Create a persistent paper-trading account",
    )
    paper_create.add_argument("--account", required=True)
    paper_create.add_argument("--cash", required=True, type=float)

    paper_status = subparsers.add_parser(
        "paper-status",
        help="Print paper account cash, equity, drawdown and PnL",
    )
    paper_status.add_argument("--account", required=True)

    paper_open = subparsers.add_parser(
        "paper-open",
        help="Open a paper position without signing a transaction",
    )
    paper_open.add_argument("--event-key", required=True)
    paper_open.add_argument("--account", required=True)
    paper_open.add_argument("--position", required=True)
    paper_open.add_argument("--pool", required=True)
    paper_open.add_argument(
        "--policy-source",
        choices=["DETERMINISTIC", "ML_CHALLENGER", "ML_CHAMPION"],
        required=True,
    )
    paper_open.add_argument("--model-id")
    paper_open.add_argument("--strategy", required=True)
    paper_open.add_argument("--min-bin", required=True, type=int)
    paper_open.add_argument("--max-bin", required=True, type=int)
    paper_open.add_argument("--capital", required=True, type=float)
    paper_open.add_argument("--entry-cost", type=float, default=0.0)

    paper_mark = subparsers.add_parser(
        "paper-mark",
        help="Update a paper position mark and incremental income",
    )
    paper_mark.add_argument("--event-key", required=True)
    paper_mark.add_argument("--position", required=True)
    paper_mark.add_argument("--mark", required=True, type=float)
    paper_mark.add_argument("--fee-delta", type=float, default=0.0)
    paper_mark.add_argument("--reward-delta", type=float, default=0.0)

    paper_rebalance = subparsers.add_parser(
        "paper-rebalance",
        help="Apply a paper rebalance and its cost",
    )
    paper_rebalance.add_argument("--event-key", required=True)
    paper_rebalance.add_argument("--position", required=True)
    paper_rebalance.add_argument("--min-bin", required=True, type=int)
    paper_rebalance.add_argument("--max-bin", required=True, type=int)
    paper_rebalance.add_argument("--mark", required=True, type=float)
    paper_rebalance.add_argument("--cost", required=True, type=float)

    paper_close = subparsers.add_parser(
        "paper-close",
        help="Close a paper position and realize net PnL",
    )
    paper_close.add_argument("--event-key", required=True)
    paper_close.add_argument("--position", required=True)
    paper_close.add_argument("--mark", required=True, type=float)
    paper_close.add_argument("--exit-cost", type=float, default=0.0)
    paper_close.add_argument("--fee-delta", type=float, default=0.0)
    paper_close.add_argument("--reward-delta", type=float, default=0.0)

    paper_manage = subparsers.add_parser(
        "paper-manage",
        help="Evaluate net-cost HOLD/REBALANCE/EXIT for a paper position",
    )
    paper_manage.add_argument("--position", required=True)
    paper_manage.add_argument("--active-bin", required=True, type=int)
    paper_manage.add_argument("--holding-observations", required=True, type=int)
    paper_manage.add_argument("--estimated-exit-cost", type=float, default=0.0)
    paper_manage.add_argument("--pool-unsafe", action="store_true")
    paper_manage.add_argument("--emergency-exit", action="store_true")
    paper_manage.add_argument("--stop-loss-bps", type=int, default=500)
    paper_manage.add_argument("--take-profit-bps", type=int)
    paper_manage.add_argument("--max-rebalances", type=int, default=3)
    paper_manage.add_argument("--max-holding-observations", type=int)
    paper_manage.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )

    paper_observe = subparsers.add_parser(
        "paper-observe",
        help="Mark a paper position and automatically HOLD/REBALANCE/EXIT",
    )
    paper_observe.add_argument("--event-key-prefix", required=True)
    paper_observe.add_argument("--position", required=True)
    paper_observe.add_argument("--active-bin", required=True, type=int)
    paper_observe.add_argument("--holding-observations", required=True, type=int)
    paper_observe.add_argument("--mark", required=True, type=float)
    paper_observe.add_argument("--fee-delta", type=float, default=0.0)
    paper_observe.add_argument("--reward-delta", type=float, default=0.0)
    paper_observe.add_argument("--estimated-exit-cost", type=float, default=0.0)
    paper_observe.add_argument("--rebalance-cost", type=float)
    paper_observe.add_argument("--pool-unsafe", action="store_true")
    paper_observe.add_argument("--emergency-exit", action="store_true")
    paper_observe.add_argument("--stop-loss-bps", type=int, default=500)
    paper_observe.add_argument("--take-profit-bps", type=int)
    paper_observe.add_argument("--max-rebalances", type=int, default=3)
    paper_observe.add_argument("--max-holding-observations", type=int)
    paper_observe.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )

    paper_chain_bind = subparsers.add_parser(
        "paper-chain-bind",
        help="Bind an open paper position to atomic chain replay state",
    )
    paper_chain_bind.add_argument("--position", required=True)
    paper_chain_bind.add_argument("--observed-at", required=True)
    paper_chain_bind.add_argument("--amount-x", required=True, type=int)
    paper_chain_bind.add_argument("--amount-y", required=True, type=int)
    paper_chain_bind.add_argument(
        "--token-y-quote-per-atomic",
        required=True,
        type=float,
    )
    paper_chain_bind.add_argument("--max-notional-error-bps", type=int, default=50)
    paper_chain_bind.add_argument("--max-share-bps", type=int, default=500)
    paper_chain_bind.add_argument("--favor-x-active", action="store_true")

    paper_chain_value = subparsers.add_parser(
        "paper-chain-value",
        help="Preview a paper position mark directly from chain snapshots",
    )
    paper_chain_value.add_argument("--position", required=True)
    paper_chain_value.add_argument("--observed-at", required=True)
    paper_chain_value.add_argument(
        "--token-y-quote-per-atomic",
        required=True,
        type=float,
    )

    paper_chain_observe = subparsers.add_parser(
        "paper-chain-observe",
        help="Value and apply one paper observation directly from chain state",
    )
    paper_chain_observe.add_argument("--position", required=True)
    paper_chain_observe.add_argument("--observed-at", required=True)
    paper_chain_observe.add_argument(
        "--token-y-quote-per-atomic",
        required=True,
        type=float,
    )
    paper_chain_observe.add_argument("--estimated-exit-cost", type=float, default=0.0)
    paper_chain_observe.add_argument("--rebalance-cost", type=float)
    paper_chain_observe.add_argument("--pool-unsafe", action="store_true")
    paper_chain_observe.add_argument("--emergency-exit", action="store_true")
    paper_chain_observe.add_argument("--stop-loss-bps", type=int, default=500)
    paper_chain_observe.add_argument("--take-profit-bps", type=int)
    paper_chain_observe.add_argument("--max-rebalances", type=int, default=3)
    paper_chain_observe.add_argument("--max-holding-observations", type=int)
    paper_chain_observe.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )

    paper_live_observe = subparsers.add_parser(
        "paper-live-observe",
        help="Apply latest-chain paper valuation with derived fail-closed pool safety",
    )
    paper_live_observe.add_argument("--position", required=True)
    paper_live_observe.add_argument("--observed-at", required=True)
    paper_live_observe.add_argument(
        "--token-y-quote-per-atomic",
        required=True,
        type=float,
    )
    paper_live_observe.add_argument("--estimated-exit-cost", type=float, default=0.0)
    paper_live_observe.add_argument("--rebalance-cost", type=float)
    paper_live_observe.add_argument("--emergency-exit", action="store_true")
    paper_live_observe.add_argument("--stop-loss-bps", type=int, default=500)
    paper_live_observe.add_argument("--take-profit-bps", type=int)
    paper_live_observe.add_argument("--max-rebalances", type=int, default=3)
    paper_live_observe.add_argument("--max-holding-observations", type=int)
    paper_live_observe.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )
    paper_live_observe.add_argument("--min-tvl-usd", type=float, default=50000.0)
    paper_live_observe.add_argument("--min-volume-24h-usd", type=float, default=10000.0)
    paper_live_observe.add_argument("--min-pool-age-hours", type=float, default=24.0)
    paper_live_observe.add_argument("--min-chain-observations", type=int, default=12)
    paper_live_observe.add_argument("--max-dynamic-fee-pct", type=float, default=5.0)

    paper_live_latest = subparsers.add_parser(
        "paper-live-latest-run",
        help="Run paper positions at each pool's latest stored chain observation",
    )
    paper_live_latest.add_argument("--cycle-id", required=True)
    paper_live_latest.add_argument(
        "--file",
        required=True,
        help="JSON array with position_id and token_y_quote_per_atomic",
    )
    paper_live_latest.add_argument("--retry-failed", action="store_true")
    paper_live_latest.add_argument("--stop-loss-bps", type=int, default=500)
    paper_live_latest.add_argument("--take-profit-bps", type=int)
    paper_live_latest.add_argument("--max-rebalances", type=int, default=3)
    paper_live_latest.add_argument("--max-holding-observations", type=int)
    paper_live_latest.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )
    paper_live_latest.add_argument("--min-tvl-usd", type=float, default=50000.0)
    paper_live_latest.add_argument("--min-volume-24h-usd", type=float, default=10000.0)
    paper_live_latest.add_argument("--min-pool-age-hours", type=float, default=24.0)
    paper_live_latest.add_argument("--min-chain-observations", type=int, default=12)
    paper_live_latest.add_argument("--max-dynamic-fee-pct", type=float, default=5.0)

    paper_portfolio = subparsers.add_parser(
        "paper-portfolio-run",
        help="Discover and run eligible chain-bound paper positions for one account",
    )
    paper_portfolio.add_argument("--account", required=True)
    paper_portfolio.add_argument("--cycle-id", required=True)
    paper_portfolio.add_argument(
        "--quotes-file",
        help="Optional JSON object mapping token-Y mint to quote-per-atomic value; omit to use persisted quotes",
    )
    paper_portfolio.add_argument("--max-quote-age-seconds", type=int, default=300)
    paper_portfolio.add_argument("--max-positions", type=int)
    paper_portfolio.add_argument("--retry-failed", action="store_true")
    paper_portfolio.add_argument("--stop-loss-bps", type=int, default=500)
    paper_portfolio.add_argument("--take-profit-bps", type=int)
    paper_portfolio.add_argument("--max-rebalances", type=int, default=3)
    paper_portfolio.add_argument("--max-holding-observations", type=int)
    paper_portfolio.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )
    paper_portfolio.add_argument("--min-tvl-usd", type=float, default=50000.0)
    paper_portfolio.add_argument("--min-volume-24h-usd", type=float, default=10000.0)
    paper_portfolio.add_argument("--min-pool-age-hours", type=float, default=24.0)
    paper_portfolio.add_argument("--min-chain-observations", type=int, default=12)
    paper_portfolio.add_argument("--max-dynamic-fee-pct", type=float, default=5.0)

    paper_live_run = subparsers.add_parser(
        "paper-live-run",
        help="Run latest-chain multi-position paper cycle with derived pool safety",
    )
    paper_live_run.add_argument("--run-id", required=True)
    paper_live_run.add_argument("--observed-at", required=True)
    paper_live_run.add_argument(
        "--file",
        required=True,
        help="JSON array with position_id and token_y_quote_per_atomic",
    )
    paper_live_run.add_argument("--retry-failed", action="store_true")
    paper_live_run.add_argument("--stop-loss-bps", type=int, default=500)
    paper_live_run.add_argument("--take-profit-bps", type=int)
    paper_live_run.add_argument("--max-rebalances", type=int, default=3)
    paper_live_run.add_argument("--max-holding-observations", type=int)
    paper_live_run.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )
    paper_live_run.add_argument("--min-tvl-usd", type=float, default=50000.0)
    paper_live_run.add_argument("--min-volume-24h-usd", type=float, default=10000.0)
    paper_live_run.add_argument("--min-pool-age-hours", type=float, default=24.0)
    paper_live_run.add_argument("--min-chain-observations", type=int, default=12)
    paper_live_run.add_argument("--max-dynamic-fee-pct", type=float, default=5.0)

    paper_chain_run = subparsers.add_parser(
        "paper-chain-run",
        help="Value and manage multiple bound paper positions from chain state",
    )
    paper_chain_run.add_argument("--run-id", required=True)
    paper_chain_run.add_argument("--observed-at", required=True)
    paper_chain_run.add_argument(
        "--file",
        required=True,
        help="JSON array with position_id and token_y_quote_per_atomic",
    )
    paper_chain_run.add_argument("--retry-failed", action="store_true")
    paper_chain_run.add_argument("--stop-loss-bps", type=int, default=500)
    paper_chain_run.add_argument("--take-profit-bps", type=int)
    paper_chain_run.add_argument("--max-rebalances", type=int, default=3)
    paper_chain_run.add_argument("--max-holding-observations", type=int)
    paper_chain_run.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )

    paper_run = subparsers.add_parser(
        "paper-run",
        help="Apply an idempotent multi-position paper observation batch",
    )
    paper_run.add_argument("--run-id", required=True)
    paper_run.add_argument("--observed-at", required=True)
    paper_run.add_argument(
        "--file",
        required=True,
        help="JSON array of paper position observations",
    )
    paper_run.add_argument("--retry-failed", action="store_true")
    paper_run.add_argument("--stop-loss-bps", type=int, default=500)
    paper_run.add_argument("--take-profit-bps", type=int)
    paper_run.add_argument("--max-rebalances", type=int, default=3)
    paper_run.add_argument("--max-holding-observations", type=int)
    paper_run.add_argument(
        "--proactive-rebalance-buffer-bins",
        type=int,
        default=0,
    )

    paper_perf = subparsers.add_parser(
        "paper-performance",
        help="Summarize completed paper-trade performance for one policy cohort",
    )
    paper_perf.add_argument("--account", required=True)
    paper_perf.add_argument(
        "--policy-source",
        choices=["DETERMINISTIC", "ML_CHALLENGER", "ML_CHAMPION"],
        required=True,
    )
    paper_perf.add_argument("--model-id")

    paper_challenger = subparsers.add_parser(
        "paper-challenger",
        help="Validate an ML paper challenger against deterministic paper results",
    )
    paper_challenger.add_argument("--account", required=True)
    paper_challenger.add_argument("--model-id", required=True)
    paper_challenger.add_argument("--min-closed-trades", type=int, default=20)
    paper_challenger.add_argument("--min-return-bps", type=int, default=0)
    paper_challenger.add_argument("--min-win-rate", type=float, default=0.5)
    paper_challenger.add_argument("--max-drawdown-bps", type=int, default=1500)
    paper_challenger.add_argument("--min-uplift-bps", type=int, default=0)
    paper_challenger.add_argument("--require-qualified", action="store_true")
    paper_challenger.add_argument("--promote", action="store_true")

    subparsers.add_parser(
        "phase-status",
        help="Print persisted Phase 2, Phase 3, Phase 5, Phase 6, Phase 7, Phase 8 and Phase 9 promotion state",
    )

    phase3_validate = subparsers.add_parser(
        "phase3-validate",
        help="Run multi-pool no-lookahead Phase 3 validation from chain history",
    )
    phase3_validate.add_argument(
        "--file",
        required=True,
        help="JSON array with pool_address, amount_x, amount_y, network_cost_y_atomic",
    )
    phase3_validate.add_argument("--lookback-observations", type=int, default=12)
    phase3_validate.add_argument("--forward-observations", type=int, default=2)
    phase3_validate.add_argument("--step-observations", type=int)
    phase3_validate.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(0, 1, 2, 5, 10),
    )
    phase3_validate.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
    )
    phase3_validate.add_argument(
        "--strategies",
        type=_parse_strategy_csv,
        default=tuple(StrategyType),
    )
    phase3_validate.add_argument("--max-share-bps", type=int, default=500)
    phase3_validate.add_argument("--favor-x-active", action="store_true")
    phase3_validate.add_argument("--min-pools", type=int, default=3)
    phase3_validate.add_argument("--min-total-steps", type=int, default=30)
    phase3_validate.add_argument("--min-complete-steps", type=int, default=20)
    phase3_validate.add_argument("--min-selection-rate", type=float, default=0.5)
    phase3_validate.add_argument("--min-complete-rate", type=float, default=0.5)
    phase3_validate.add_argument(
        "--min-positive-excess-rate",
        type=float,
        default=0.55,
    )
    phase3_validate.add_argument(
        "--min-mean-excess-bps",
        type=float,
        default=0.0,
    )
    phase3_validate.add_argument(
        "--max-single-step-loss-bps",
        type=int,
        default=1000,
    )
    phase3_validate.add_argument("--persist-ready", action="store_true")
    phase3_validate.add_argument("--require-ready", action="store_true")

    phase5_validate = subparsers.add_parser(
        "phase5-validate",
        help="Evaluate real PAPER endurance/accounting evidence for Phase 5",
    )
    phase5_validate.add_argument("--account", required=True)
    phase5_validate.add_argument("--min-runtime-hours", type=float, default=72.0)
    phase5_validate.add_argument("--min-terminal-ticks", type=int, default=500)
    phase5_validate.add_argument(
        "--min-success-rate-pct",
        type=float,
        default=99.0,
    )
    phase5_validate.add_argument(
        "--max-dependency-blocked-pct",
        type=float,
        default=5.0,
    )
    phase5_validate.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=1,
    )
    phase5_validate.add_argument(
        "--max-stale-running-ticks",
        type=int,
        default=0,
    )
    phase5_validate.add_argument(
        "--stale-running-after-seconds",
        type=int,
        default=900,
    )
    phase5_validate.add_argument(
        "--min-applied-chain-valuations",
        type=int,
        default=100,
    )
    phase5_validate.add_argument(
        "--min-distinct-positions-valued",
        type=int,
        default=3,
    )
    phase5_validate.add_argument(
        "--min-closed-positions",
        type=int,
        default=3,
    )
    phase5_validate.add_argument(
        "--min-distinct-pools",
        type=int,
        default=2,
    )
    phase5_validate.add_argument("--persist-ready", action="store_true")
    phase5_validate.add_argument("--require-ready", action="store_true")

    phase6_validate = subparsers.add_parser(
        "phase6-validate",
        help="Evaluate persisted pre-live Rust executor evidence for Phase 6",
    )
    phase6_validate.add_argument(
        "--execution-db",
        required=True,
        help="Absolute path to the Rust execution-intent SQLite database",
    )
    phase6_validate.add_argument(
        "--min-passed-enter-intents",
        type=int,
        default=10,
    )
    phase6_validate.add_argument(
        "--min-distinct-pools",
        type=int,
        default=2,
    )
    phase6_validate.add_argument(
        "--min-blocked-intents",
        type=int,
        default=2,
    )
    phase6_validate.add_argument(
        "--max-postsimulation-intents",
        type=int,
        default=0,
    )
    phase6_validate.add_argument("--persist-ready", action="store_true")
    phase6_validate.add_argument("--require-ready", action="store_true")

    phase7_validate = subparsers.add_parser(
        "phase7-validate",
        help="Evaluate reconciled controlled-live evidence for Phase 7",
    )
    phase7_validate.add_argument(
        "--min-closed-positions",
        type=int,
        default=3,
    )
    phase7_validate.add_argument(
        "--min-distinct-pools",
        type=int,
        default=2,
    )
    phase7_validate.add_argument(
        "--min-confirmed-receipts",
        type=int,
        default=6,
    )
    phase7_validate.add_argument(
        "--max-failed-receipts",
        type=int,
        default=0,
    )
    phase7_validate.add_argument(
        "--max-open-positions-at-validation",
        type=int,
        default=0,
    )
    phase7_validate.add_argument("--persist-ready", action="store_true")
    phase7_validate.add_argument("--require-ready", action="store_true")

    phase8_validate = subparsers.add_parser(
        "phase8-validate",
        help="Evaluate continuous-learning evidence for Phase 8 promotion",
    )
    phase8_validate.add_argument(
        "--min-completed-cycles",
        type=int,
        default=1,
    )
    phase8_validate.add_argument(
        "--min-live-labels",
        type=int,
        default=10,
    )
    phase8_validate.add_argument(
        "--min-live-pools",
        type=int,
        default=2,
    )
    phase8_validate.add_argument(
        "--max-realized-drawdown-bps",
        type=int,
        default=2000,
    )
    phase8_validate.add_argument(
        "--max-single-loss-bps",
        type=int,
        default=1500,
    )
    phase8_validate.add_argument(
        "--min-win-rate",
        type=float,
        default=0.30,
    )
    phase8_validate.add_argument(
        "--min-mean-return-bps",
        type=float,
        default=-100.0,
    )
    phase8_validate.add_argument(
        "--max-mean-abs-prediction-error-bps",
        type=float,
        default=1500.0,
    )
    phase8_validate.add_argument("--persist-ready", action="store_true")
    phase8_validate.add_argument("--require-ready", action="store_true")

    dlmm_regime = subparsers.add_parser(
        "dlmm-regime-research",
        help="Classify a no-lookahead DLMM active-bin movement regime",
    )
    dlmm_regime.add_argument("--pool", required=True)
    dlmm_regime.add_argument(
        "--lookback-observations",
        type=int,
        default=72,
    )
    dlmm_regime.add_argument(
        "--recent-observations",
        type=int,
        default=8,
    )
    dlmm_regime.add_argument(
        "--min-observations",
        type=int,
        default=16,
    )
    dlmm_regime.add_argument(
        "--trend-efficiency-threshold",
        type=float,
        default=0.65,
    )
    dlmm_regime.add_argument(
        "--activity-percentile",
        type=float,
        default=0.75,
    )
    dlmm_regime.add_argument(
        "--quiet-percentile",
        type=float,
        default=0.25,
    )
    dlmm_regime.add_argument("--as-of")
    dlmm_regime.add_argument(
        "--require-ready",
        action="store_true",
    )

    adaptive_range_validate = subparsers.add_parser(
        "adaptive-range-validate",
        help="Walk-forward validate adaptive DLMM ranges against a fixed-width baseline",
    )
    adaptive_range_validate.add_argument("--pool", required=True)
    adaptive_range_validate.add_argument(
        "--lookback-observations",
        type=int,
        default=96,
    )
    adaptive_range_validate.add_argument(
        "--holding-observations",
        type=int,
        default=6,
    )
    adaptive_range_validate.add_argument(
        "--target-coverage",
        type=float,
        default=0.90,
    )
    adaptive_range_validate.add_argument(
        "--min-half-width-bins",
        type=int,
        default=1,
    )
    adaptive_range_validate.add_argument(
        "--max-half-width-bins",
        type=int,
        default=35,
    )
    adaptive_range_validate.add_argument(
        "--min-historical-windows",
        type=int,
        default=12,
    )
    adaptive_range_validate.add_argument(
        "--fixed-half-width-bins",
        type=int,
        default=5,
    )
    adaptive_range_validate.add_argument(
        "--min-decisions",
        type=int,
        default=20,
    )
    adaptive_range_validate.add_argument(
        "--min-adaptive-survival-rate",
        type=float,
        default=0.75,
    )
    adaptive_range_validate.add_argument(
        "--min-survival-uplift-vs-fixed",
        type=float,
        default=0.0,
    )
    adaptive_range_validate.add_argument(
        "--max-mean-width-multiple-vs-fixed",
        type=float,
        default=2.0,
    )
    adaptive_range_validate.add_argument(
        "--max-cap-exceeded-rate",
        type=float,
        default=0.10,
    )
    adaptive_range_validate.add_argument("--as-of")
    adaptive_range_validate.add_argument("--persist", action="store_true")
    adaptive_range_validate.add_argument(
        "--require-qualified",
        action="store_true",
    )

    adaptive_range = subparsers.add_parser(
        "adaptive-range-research",
        help="Estimate a no-lookahead DLMM range width from historical active-bin movement",
    )
    adaptive_range.add_argument("--pool", required=True)
    adaptive_range.add_argument(
        "--lookback-observations",
        type=int,
        default=96,
    )
    adaptive_range.add_argument(
        "--holding-observations",
        type=int,
        default=6,
    )
    adaptive_range.add_argument(
        "--target-coverage",
        type=float,
        default=0.90,
    )
    adaptive_range.add_argument(
        "--min-half-width-bins",
        type=int,
        default=1,
    )
    adaptive_range.add_argument(
        "--max-half-width-bins",
        type=int,
        default=35,
    )
    adaptive_range.add_argument(
        "--min-historical-windows",
        type=int,
        default=12,
    )
    adaptive_range.add_argument("--as-of")
    adaptive_range.add_argument(
        "--require-ready",
        action="store_true",
    )

    static_hedge = subparsers.add_parser(
        "static-hedge-research",
        help="Walk-forward study a static token-X hedge using persisted DLMM prices",
    )
    static_hedge.add_argument("--pool", required=True)
    static_hedge.add_argument("--amount-x", type=int, required=True)
    static_hedge.add_argument("--amount-y", type=int, required=True)
    static_hedge.add_argument(
        "--observation-limit",
        type=int,
        default=96,
    )
    static_hedge.add_argument(
        "--holding-observations",
        type=int,
        default=6,
    )
    static_hedge.add_argument(
        "--hedge-fraction",
        type=float,
        default=1.0,
    )
    static_hedge.add_argument("--hedge-instrument-id", required=True)
    static_hedge.add_argument("--hedge-venue", required=True)
    static_hedge.add_argument(
        "--hedge-available-liquidity-y-atomic",
        type=float,
        required=True,
    )
    static_hedge.add_argument(
        "--hedge-max-liquidity-share-bps",
        type=int,
        default=1000,
    )
    static_hedge.add_argument(
        "--hedge-max-leverage",
        type=float,
        default=1.0,
    )
    static_hedge.add_argument(
        "--hedge-funding-bps-per-window",
        type=float,
        default=0.0,
    )
    static_hedge.add_argument(
        "--hedge-round-trip-cost-bps",
        type=float,
        default=10.0,
    )
    static_hedge.add_argument("--min-windows", type=int, default=20)
    static_hedge.add_argument(
        "--min-mean-abs-return-reduction-bps",
        type=float,
        default=0.0,
    )
    static_hedge.add_argument(
        "--min-worst-loss-improvement-bps",
        type=float,
        default=0.0,
    )
    static_hedge.add_argument(
        "--max-mean-return-drag-bps",
        type=float,
        default=100.0,
    )
    static_hedge.add_argument("--as-of")
    static_hedge.add_argument("--persist", action="store_true")
    static_hedge.add_argument(
        "--require-qualified",
        action="store_true",
    )

    mint_ingest = subparsers.add_parser(
        "mint-snapshot-ingest",
        help="Persist one authoritative Rust Solana mint snapshot JSON",
    )
    mint_ingest.add_argument("--file", required=True)
    mint_ingest.add_argument("--observed-at")

    mint_risk = subparsers.add_parser(
        "mint-risk-research",
        help="Evaluate no-lookahead research-only mint risk for one pool",
    )
    mint_risk.add_argument("--pool", required=True)
    mint_risk.add_argument(
        "--max-snapshot-age-seconds",
        type=int,
        default=3600,
    )
    mint_risk.add_argument("--max-decimals", type=int, default=12)
    mint_risk.add_argument(
        "--allow-active-mint-authority",
        action="store_true",
    )
    mint_risk.add_argument(
        "--allow-active-freeze-authority",
        action="store_true",
    )
    mint_risk.add_argument(
        "--disallow-token-2022",
        action="store_true",
    )
    mint_risk.add_argument(
        "--allow-token-2022-extension-data",
        action="store_true",
    )
    mint_risk.add_argument(
        "--exclude-reward-mints",
        action="store_true",
    )
    mint_risk.add_argument("--as-of")
    mint_risk.add_argument("--persist", action="store_true")
    mint_risk.add_argument(
        "--require-qualified",
        action="store_true",
    )

    wallet_flow = subparsers.add_parser(
        "wallet-flow-research",
        help="Summarize descriptive wallet activity concentration and classified LP flow",
    )
    wallet_flow.add_argument("--pool", required=True)
    wallet_flow.add_argument(
        "--lookback-events",
        type=int,
        default=500,
    )
    wallet_flow.add_argument(
        "--min-events",
        type=int,
        default=20,
    )
    wallet_flow.add_argument(
        "--min-unique-users",
        type=int,
        default=5,
    )
    wallet_flow.add_argument(
        "--max-top-user-share-bps",
        type=int,
        default=4000,
    )
    wallet_flow.add_argument("--as-of")
    wallet_flow.add_argument("--persist", action="store_true")
    wallet_flow.add_argument(
        "--require-qualified",
        action="store_true",
    )

    multi_pool = subparsers.add_parser(
        "multi-pool-research",
        help="Build comparable Phase 3 research plans and ranked cross-pool candidates",
    )
    multi_pool.add_argument(
        "--file",
        required=True,
        help="JSON array with pool_address, amount_x, amount_y, requested_quote, network_cost_y_atomic",
    )
    multi_pool.add_argument("--equity", required=True, type=float)
    multi_pool.add_argument("--cash", required=True, type=float)
    multi_pool.add_argument("--deployed", required=True, type=float)
    multi_pool.add_argument("--drawdown-bps", required=True, type=int)
    multi_pool.add_argument("--observations", type=int, default=12)
    multi_pool.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(0, 1, 2, 5, 10),
    )
    multi_pool.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
    )
    multi_pool.add_argument(
        "--strategies",
        type=_parse_strategy_csv,
        default=tuple(StrategyType),
    )
    multi_pool.add_argument("--max-share-bps", type=int, default=500)
    multi_pool.add_argument("--favor-x-active", action="store_true")
    multi_pool.add_argument(
        "--persist-phase9-candidates",
        action="store_true",
    )

    portfolio_allocation = subparsers.add_parser(
        "portfolio-allocation-research",
        help="Allocate a research-only quote budget across qualified pool candidates",
    )
    portfolio_allocation.add_argument(
        "--file",
        required=True,
        help="JSON candidate array or object containing a candidates array",
    )
    portfolio_allocation.add_argument(
        "--budget-quote",
        type=float,
        required=True,
    )
    portfolio_allocation.add_argument(
        "--max-positions",
        type=int,
        default=3,
    )
    portfolio_allocation.add_argument(
        "--min-positions",
        type=int,
        default=2,
    )
    portfolio_allocation.add_argument(
        "--max-pool-allocation-bps",
        type=int,
        default=4000,
    )
    portfolio_allocation.add_argument(
        "--min-range-survival-ratio",
        type=float,
        default=0.75,
    )
    portfolio_allocation.add_argument(
        "--min-excess-vs-hold-bps",
        type=int,
        default=0,
    )
    portfolio_allocation.add_argument(
        "--min-position-quote",
        type=float,
        default=10.0,
    )
    portfolio_allocation.add_argument(
        "--min-budget-utilization-rate",
        type=float,
        default=0.75,
    )
    portfolio_allocation.add_argument("--persist", action="store_true")
    portfolio_allocation.add_argument(
        "--require-qualified",
        action="store_true",
    )

    phase8_promotion_audit = subparsers.add_parser(
        "phase8-promotion-audit",
        help="Audit whether persisted Phase 8 champion promotion still matches current champion lineage and live health",
    )
    phase8_promotion_audit.add_argument(
        "--require-current",
        action="store_true",
    )

    phase9_research = subparsers.add_parser(
        "phase9-research-validate",
        help="Evaluate multi-pool research-only Phase 9 adaptive edge evidence",
    )
    phase9_research.add_argument(
        "--pools",
        required=True,
        help="Comma-separated pool addresses",
    )
    phase9_research.add_argument(
        "--min-pools",
        type=int,
        default=3,
    )
    phase9_research.add_argument(
        "--min-qualified-pools",
        type=int,
        default=2,
    )
    phase9_research.add_argument(
        "--min-qualified-pool-rate",
        type=float,
        default=0.67,
    )
    phase9_research.add_argument(
        "--min-mean-survival-uplift-vs-fixed",
        type=float,
        default=0.0,
    )
    phase9_research.add_argument(
        "--max-mean-width-multiple-vs-fixed",
        type=float,
        default=2.0,
    )
    phase9_research.add_argument(
        "--lookback-observations",
        type=int,
        default=96,
    )
    phase9_research.add_argument(
        "--holding-observations",
        type=int,
        default=6,
    )
    phase9_research.add_argument(
        "--target-coverage",
        type=float,
        default=0.90,
    )
    phase9_research.add_argument(
        "--min-half-width-bins",
        type=int,
        default=1,
    )
    phase9_research.add_argument(
        "--max-half-width-bins",
        type=int,
        default=35,
    )
    phase9_research.add_argument(
        "--min-historical-windows",
        type=int,
        default=12,
    )
    phase9_research.add_argument(
        "--fixed-half-width-bins",
        type=int,
        default=5,
    )
    phase9_research.add_argument(
        "--min-decisions",
        type=int,
        default=20,
    )
    phase9_research.add_argument(
        "--min-adaptive-survival-rate",
        type=float,
        default=0.75,
    )
    phase9_research.add_argument(
        "--min-survival-uplift-vs-fixed",
        type=float,
        default=0.0,
    )
    phase9_research.add_argument(
        "--max-pool-width-multiple-vs-fixed",
        type=float,
        default=2.0,
    )
    phase9_research.add_argument(
        "--max-cap-exceeded-rate",
        type=float,
        default=0.10,
    )
    phase9_research.add_argument(
        "--regime-lookback-observations",
        type=int,
        default=72,
    )
    phase9_research.add_argument(
        "--regime-recent-observations",
        type=int,
        default=8,
    )
    phase9_research.add_argument(
        "--regime-min-observations",
        type=int,
        default=16,
    )
    phase9_research.add_argument(
        "--trend-efficiency-threshold",
        type=float,
        default=0.65,
    )
    phase9_research.add_argument(
        "--activity-percentile",
        type=float,
        default=0.75,
    )
    phase9_research.add_argument(
        "--quiet-percentile",
        type=float,
        default=0.25,
    )
    phase9_research.add_argument("--as-of")
    phase9_research.add_argument("--persist", action="store_true")
    phase9_research.add_argument(
        "--require-qualified",
        action="store_true",
    )

    phase9_bundle = subparsers.add_parser(
        "phase9-research-bundle",
        help="Validate persisted Phase 9 research families without granting live-policy authority",
    )
    phase9_bundle.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_bundle.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_bundle.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_bundle.add_argument("--persist", action="store_true")
    phase9_bundle.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_storage_integrity = subparsers.add_parser(
        "phase9-storage-integrity",
        help="Verify immutable Phase 9 source, evidence and promotion-history storage",
    )
    phase9_storage_integrity.add_argument(
        "--require-verified",
        action="store_true",
    )

    phase9_policy_audit = subparsers.add_parser(
        "phase9-policy-authorization-audit",
        help="Audit whether persisted Phase 9 future-policy authorization evidence still matches current deterministic replay",
    )
    phase9_policy_audit.add_argument(
        "--require-current",
        action="store_true",
    )

    phase9_policy_gate = subparsers.add_parser(
        "phase9-policy-authorization-gate",
        help="Evaluate replay-verified post-promotion shadow evidence for a future LIVE-policy authorization boundary without wiring it to execution",
    )
    phase9_policy_gate.add_argument(
        "--min-shadow-runs",
        type=int,
        default=3,
    )
    phase9_policy_gate.add_argument(
        "--min-distinct-dataset-hashes",
        type=int,
        default=3,
    )
    phase9_policy_gate.add_argument(
        "--min-distinct-cutoffs",
        type=int,
        default=3,
    )
    phase9_policy_gate.add_argument(
        "--min-decisions-per-run",
        type=int,
        default=50,
    )
    phase9_policy_gate.add_argument(
        "--min-pools-per-run",
        type=int,
        default=3,
    )
    phase9_policy_gate.add_argument(
        "--min-selected-arms-per-run",
        type=int,
        default=2,
    )
    phase9_policy_gate.add_argument(
        "--min-total-decisions",
        type=int,
        default=150,
    )
    phase9_policy_gate.add_argument(
        "--min-mean-uplift-vs-baseline-bps",
        type=float,
        default=0.0,
    )
    phase9_policy_gate.add_argument(
        "--max-mean-regret-vs-oracle-bps",
        type=float,
        default=300.0,
    )
    phase9_policy_gate.add_argument("--persist", action="store_true")
    phase9_policy_gate.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_policy_controlled = subparsers.add_parser(
        "phase9-policy-controlled-validate",
        help="Run a fresh simulation-only holdout validation after current Phase 9 policy authorization evidence",
    )
    phase9_policy_controlled.add_argument("--cycle-id", required=True)
    phase9_policy_controlled.add_argument(
        "--warmup-decisions-per-context",
        type=int,
        default=2,
    )
    phase9_policy_controlled.add_argument(
        "--exploration-bonus-bps",
        type=float,
        default=50.0,
    )
    phase9_policy_controlled.add_argument(
        "--min-decisions",
        type=int,
        default=50,
    )
    phase9_policy_controlled.add_argument(
        "--min-pools",
        type=int,
        default=3,
    )
    phase9_policy_controlled.add_argument(
        "--min-selected-arms",
        type=int,
        default=2,
    )
    phase9_policy_controlled.add_argument(
        "--min-mean-uplift-vs-baseline-bps",
        type=float,
        default=0.0,
    )
    phase9_policy_controlled.add_argument(
        "--max-mean-regret-vs-oracle-bps",
        type=float,
        default=250.0,
    )
    phase9_policy_controlled.add_argument(
        "--min-post-authorization-seconds",
        type=int,
        default=1,
    )
    phase9_policy_controlled.add_argument(
        "--persist",
        action="store_true",
    )
    phase9_policy_controlled.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_policy_controlled_audit = subparsers.add_parser(
        "phase9-policy-controlled-audit",
        help="Audit whether persisted Phase 9 simulation-only controlled validation still matches current replay",
    )
    phase9_policy_controlled_audit.add_argument(
        "--require-current",
        action="store_true",
    )

    phase9_policy_readiness = subparsers.add_parser(
        "phase9-policy-readiness-audit",
        help="Require current Phase 9 authorization and fresh controlled holdout evidence without granting LIVE policy authority",
    )
    phase9_policy_readiness.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_policy_rollout = subparsers.add_parser(
        "phase9-policy-rollout-simulate",
        help="Validate a disabled Phase 9 rollout envelope against current controlled-live limits without wiring it to execution",
    )
    phase9_policy_rollout.add_argument(
        "--file",
        required=True,
        help="JSON object with current and proposed controlled-live envelope objects",
    )
    phase9_policy_rollout.add_argument(
        "--persist",
        action="store_true",
    )
    phase9_policy_rollout.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_policy_rollout_audit = subparsers.add_parser(
        "phase9-policy-rollout-audit",
        help="Audit whether persisted disabled Phase 9 rollout simulation still matches current policy readiness",
    )
    phase9_policy_rollout_audit.add_argument(
        "--require-current",
        action="store_true",
    )

    phase9_policy_rollback = subparsers.add_parser(
        "phase9-policy-rollback-simulate",
        help="Evaluate explicit Phase 9 canary rollback thresholds without taking any policy or execution action",
    )
    phase9_policy_rollback.add_argument(
        "--file",
        required=True,
        help="JSON object containing metrics and criteria objects",
    )
    phase9_policy_rollback.add_argument(
        "--persist",
        action="store_true",
    )

    phase9_policy_rollback_audit = subparsers.add_parser(
        "phase9-policy-rollback-audit",
        help="Audit whether persisted Phase 9 rollback simulation still matches current rollout evidence",
    )
    phase9_policy_rollback_audit.add_argument(
        "--require-current",
        action="store_true",
    )

    phase9_policy_prewire = subparsers.add_parser(
        "phase9-policy-prewire-audit",
        help="Require all Phase 9 future-policy simulation evidence to be current and rollback-clear without enabling execution",
    )
    phase9_policy_prewire.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_policy_manifest = subparsers.add_parser(
        "phase9-policy-manifest",
        help="Build and optionally persist an immutable manifest binding the exact current Phase 9 pre-wiring evidence chain",
    )
    phase9_policy_manifest.add_argument(
        "--persist",
        action="store_true",
    )
    phase9_policy_manifest.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_policy_manifest_audit = subparsers.add_parser(
        "phase9-policy-manifest-audit",
        help="Audit whether the persisted Phase 9 pre-wiring evidence manifest still matches the current evidence chain",
    )
    phase9_policy_manifest_audit.add_argument(
        "--require-current",
        action="store_true",
    )

    phase9_shadow = subparsers.add_parser(
        "phase9-shadow-validate",
        help="Validate a post-promotion checksum-bound Phase 9 shadow corpus without granting LIVE policy authority",
    )
    phase9_shadow.add_argument("--cycle-id", required=True)
    phase9_shadow.add_argument(
        "--warmup-decisions-per-context",
        type=int,
        default=2,
    )
    phase9_shadow.add_argument(
        "--exploration-bonus-bps",
        type=float,
        default=50.0,
    )
    phase9_shadow.add_argument("--min-decisions", type=int, default=50)
    phase9_shadow.add_argument("--min-pools", type=int, default=3)
    phase9_shadow.add_argument(
        "--min-selected-arms",
        type=int,
        default=2,
    )
    phase9_shadow.add_argument(
        "--min-mean-uplift-vs-baseline-bps",
        type=float,
        default=0.0,
    )
    phase9_shadow.add_argument(
        "--max-mean-regret-vs-oracle-bps",
        type=float,
        default=300.0,
    )
    phase9_shadow.add_argument(
        "--min-post-promotion-seconds",
        type=int,
        default=1,
    )
    phase9_shadow.add_argument("--persist", action="store_true")
    phase9_shadow.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_progress = subparsers.add_parser(
        "phase9-progress",
        help="Summarize checksum-verified Phase 9 evidence progress snapshots",
    )
    phase9_progress.add_argument(
        "--require-snapshot",
        action="store_true",
    )
    phase9_progress.add_argument(
        "--require-integrity",
        action="store_true",
    )

    phase9_operational_audit = subparsers.add_parser(
        "phase9-operational-audit",
        help="Verify Phase 9 storage integrity, deterministic replay and promotion currentness together",
    )
    phase9_operational_audit.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_operational_audit.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_operational_audit.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_operational_audit.add_argument(
        "--require-verified",
        action="store_true",
    )

    phase9_replay_audit = subparsers.add_parser(
        "phase9-replay-audit",
        help="Audit deterministic replay verification for every required Phase 9 research family",
    )
    phase9_replay_audit.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_replay_audit.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_replay_audit.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_replay_audit.add_argument(
        "--require-verified",
        action="store_true",
    )

    phase9_promotion_audit = subparsers.add_parser(
        "phase9-promotion-audit",
        help="Audit whether persisted Phase 9 research promotion still matches current replay-verified evidence",
    )
    phase9_promotion_audit.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_promotion_audit.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_promotion_audit.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_promotion_audit.add_argument(
        "--require-current",
        action="store_true",
    )

    phase9_capture_plan = subparsers.add_parser(
        "phase9-chain-capture-plan",
        help="Plan read-only Rust inspect-pool captures for discovered Meteora pools that lack Phase 9 chain evidence",
    )
    phase9_capture_plan.add_argument(
        "--target-chain-pools",
        type=int,
        default=3,
    )
    phase9_capture_plan.add_argument(
        "--max-candidates",
        type=int,
        default=8,
    )
    phase9_capture_plan.add_argument(
        "--bin-array-radius",
        type=int,
        default=1,
    )
    phase9_capture_plan.add_argument(
        "--max-api-snapshot-age-seconds",
        type=int,
        default=10_800,
        help="Maximum age of API pool rankings allowed to steer chain onboarding",
    )
    phase9_capture_plan.add_argument(
        "--as-of",
        help="Optional timezone-aware ranking cutoff; defaults to current time",
    )
    phase9_capture_plan.add_argument(
        "--rpc-url",
        help="Optional Solana RPC URL inserted into emitted read-only capture commands",
    )
    phase9_capture_plan.add_argument(
        "--executor-bin",
        default="meteora-executor",
        help="Rust read-only executor binary used in emitted inspect-pool commands",
    )
    phase9_capture_plan.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_mint_plan = subparsers.add_parser(
        "phase9-mint-capture-plan",
        help="Plan fresh authoritative read-only mint captures for the highest-depth Phase 9 pools",
    )
    phase9_mint_plan.add_argument(
        "--target-pools",
        type=int,
        default=2,
    )
    phase9_mint_plan.add_argument(
        "--pools",
        help="Optional comma-separated exact pool set instead of highest-depth automatic selection",
    )
    phase9_mint_plan.add_argument(
        "--max-snapshot-age-seconds",
        type=int,
        default=3600,
    )
    phase9_mint_plan.add_argument(
        "--exclude-reward-mints",
        action="store_true",
    )
    phase9_mint_plan.add_argument("--as-of")
    phase9_mint_plan.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_mint_run = subparsers.add_parser(
        "phase9-mint-capture-run",
        help="Capture missing or stale Phase 9 mint snapshots through the read-only Rust inspect-mint-env command",
    )
    phase9_mint_run.add_argument(
        "--target-pools",
        type=int,
        default=2,
    )
    phase9_mint_run.add_argument(
        "--pools",
        help="Optional comma-separated exact pool set instead of highest-depth automatic selection",
    )
    phase9_mint_run.add_argument(
        "--max-snapshot-age-seconds",
        type=int,
        default=3600,
    )
    phase9_mint_run.add_argument(
        "--exclude-reward-mints",
        action="store_true",
    )
    phase9_mint_run.add_argument("--rust-manifest-path")
    phase9_mint_run.add_argument("--rust-binary-path")
    phase9_mint_run.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
    )
    phase9_mint_run.add_argument(
        "--observed-at",
        help="Optional timezone-aware evaluation/capture timestamp",
    )
    phase9_mint_run.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_position_discovery = subparsers.add_parser(
        "phase9-position-discovery",
        help="Discover current on-chain Meteora PositionV2 accounts for one pool through read-only RPC filtering",
    )
    phase9_position_discovery.add_argument("--pool", required=True)
    phase9_position_discovery.add_argument(
        "--limit",
        type=int,
        default=250,
    )
    phase9_position_discovery.add_argument("--rust-manifest-path")
    phase9_position_discovery.add_argument("--rust-binary-path")
    phase9_position_discovery.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
    )

    phase9_pool_activity = subparsers.add_parser(
        "phase9-pool-activity-discovery",
        help="Discover bounded historical Meteora position/owner candidates from Solana signatures mentioning one pool",
    )
    phase9_pool_activity.add_argument("--pool", required=True)
    phase9_pool_activity.add_argument(
        "--limit",
        type=int,
        default=25,
    )
    phase9_pool_activity.add_argument(
        "--before-signature",
        help="Optional explicit getSignaturesForAddress pagination cursor",
    )
    phase9_pool_activity.add_argument(
        "--advance-backfill",
        action="store_true",
        help="Use and advance the persisted per-pool historical backfill cursor",
    )
    phase9_pool_activity.add_argument("--rust-manifest-path")
    phase9_pool_activity.add_argument("--rust-binary-path")
    phase9_pool_activity.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
    )

    phase9_wallet_capture = subparsers.add_parser(
        "phase9-wallet-flow-capture-run",
        help="Collect official Meteora histories for a bounded current-position cohort until wallet-flow source thresholds are met",
    )
    phase9_wallet_capture.add_argument("--pool", required=True)
    phase9_wallet_capture.add_argument(
        "--lookback-events",
        type=int,
        default=500,
    )
    phase9_wallet_capture.add_argument(
        "--min-events",
        type=int,
        default=20,
    )
    phase9_wallet_capture.add_argument(
        "--min-unique-users",
        type=int,
        default=5,
    )
    phase9_wallet_capture.add_argument(
        "--max-top-user-share-bps",
        type=int,
        default=4000,
    )
    phase9_wallet_capture.add_argument(
        "--discovery-limit",
        type=int,
        default=250,
    )
    phase9_wallet_capture.add_argument(
        "--max-positions-per-run",
        type=int,
        default=50,
    )
    phase9_wallet_capture.add_argument(
        "--historical-signature-limit",
        type=int,
        default=25,
        help="Maximum pool signatures per historical recent/backfill page",
    )
    phase9_wallet_capture.add_argument(
        "--skip-historical-pool-activity",
        action="store_true",
        help="Disable live read-only pool-signature history discovery",
    )
    phase9_wallet_capture.add_argument(
        "--skip-owner-position-expansion",
        action="store_true",
        help="Use only current on-chain PositionV2 addresses instead of expanding each current owner through Meteora status=all position PnL",
    )
    phase9_wallet_capture.add_argument(
        "--owner-expansion-limit",
        type=int,
        default=25,
    )
    phase9_wallet_capture.add_argument(
        "--owner-position-max-pages",
        type=int,
        default=3,
    )
    phase9_wallet_capture.add_argument("--as-of")
    phase9_wallet_capture.add_argument("--rust-manifest-path")
    phase9_wallet_capture.add_argument("--rust-binary-path")
    phase9_wallet_capture.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
    )
    phase9_wallet_capture.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_source_capture = subparsers.add_parser(
        "phase9-source-capture-run",
        help="Run one bounded read-only Phase 9 source-acquisition pass without executing research or policy",
    )
    phase9_source_capture.add_argument(
        "--skip-api-refresh",
        action="store_true",
    )
    phase9_source_capture.add_argument(
        "--chain-pool-target",
        type=int,
        default=3,
    )
    phase9_source_capture.add_argument(
        "--chain-max-candidates",
        type=int,
        default=8,
    )
    phase9_source_capture.add_argument(
        "--api-ranking-max-age-seconds",
        type=int,
        default=10_800,
        help="Maximum age of API pool rankings allowed to steer unattended cohort capture",
    )
    phase9_source_capture.add_argument(
        "--bin-array-radius",
        type=int,
        default=1,
    )
    phase9_source_capture.add_argument(
        "--mint-max-snapshot-age-seconds",
        type=int,
        default=3600,
    )
    phase9_source_capture.add_argument(
        "--history-min-observation-interval-seconds",
        type=int,
        default=3600,
        help="Minimum seconds between persisted chain-history observations for the same pool",
    )
    phase9_source_capture.add_argument(
        "--wallet-discovery-limit",
        type=int,
        default=250,
    )
    phase9_source_capture.add_argument(
        "--wallet-max-positions-per-run",
        type=int,
        default=50,
    )
    phase9_source_capture.add_argument(
        "--wallet-historical-signature-limit",
        type=int,
        default=25,
        help="Maximum pool signatures per live historical wallet-flow page",
    )
    phase9_source_capture.add_argument(
        "--skip-wallet-historical-pool-activity",
        action="store_true",
    )
    phase9_source_capture.add_argument(
        "--skip-wallet-owner-position-expansion",
        action="store_true",
    )
    phase9_source_capture.add_argument(
        "--wallet-owner-expansion-limit",
        type=int,
        default=25,
    )
    phase9_source_capture.add_argument(
        "--wallet-owner-position-max-pages",
        type=int,
        default=3,
    )
    phase9_source_capture.add_argument("--rust-manifest-path")
    phase9_source_capture.add_argument("--rust-binary-path")
    phase9_source_capture.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
    )
    phase9_source_capture.add_argument(
        "--require-automatic-ready",
        action="store_true",
    )
    phase9_source_capture.add_argument(
        "--lease-seconds",
        type=int,
        default=1800,
        help="SQLite lease duration preventing overlapping source-capture runs",
    )

    phase9_source_freshness = subparsers.add_parser(
        "phase9-source-freshness",
        help="Show whether persisted Phase 9 research has incorporated the latest source observations",
    )
    phase9_source_freshness.add_argument(
        "--as-of",
        help="Optional timezone-aware ranking freshness cutoff; defaults to current time",
    )
    phase9_source_freshness.add_argument(
        "--require-current",
        action="store_true",
        help="Exit non-zero unless every Phase 9 research family is source-current",
    )

    phase9_evidence_status = subparsers.add_parser(
        "phase9-evidence-status",
        help="Show quantitative Phase 9 ranked-cohort and research evidence readiness without external calls",
    )
    phase9_evidence_status.add_argument(
        "--as-of",
        help="Optional timezone-aware evaluation cutoff; defaults to current time",
    )
    phase9_evidence_status.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_evidence_status.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_evidence_status.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_evidence_status.add_argument(
        "--require-source-ready",
        action="store_true",
    )
    phase9_evidence_status.add_argument(
        "--require-bundle-ready",
        action="store_true",
    )

    phase9_evidence_plan = subparsers.add_parser(
        "phase9-evidence-plan",
        help="Rank current Phase 9 evidence debt and emit one deterministic next safe action",
    )
    phase9_evidence_plan.add_argument(
        "--as-of",
        help="Optional timezone-aware evaluation cutoff; defaults to current time",
    )
    phase9_evidence_plan.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_evidence_plan.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_evidence_plan.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_evidence_plan.add_argument(
        "--history-interval-seconds",
        type=int,
        default=3600,
    )
    phase9_evidence_plan.add_argument(
        "--require-bundle-ready",
        action="store_true",
    )

    phase9_evidence_step = subparsers.add_parser(
        "phase9-evidence-step-run",
        help="Execute exactly one planner-selected safe Phase 9 evidence step without shell execution or policy changes",
    )
    phase9_evidence_step.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_evidence_step.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_evidence_step.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_evidence_step.add_argument(
        "--history-interval-seconds",
        type=int,
        default=3600,
    )
    phase9_evidence_step.add_argument(
        "--lease-seconds",
        type=int,
        default=1800,
    )
    phase9_evidence_step.add_argument(
        "--require-progress",
        action="store_true",
    )
    phase9_evidence_step.add_argument(
        "--require-bundle-ready",
        action="store_true",
    )

    phase9_maintenance_status = subparsers.add_parser(
        "phase9-maintenance-status",
        help="Show the shared Phase 9 source/research maintenance lease without mutating it",
    )
    phase9_maintenance_status.add_argument(
        "--as-of",
        help="Optional timezone-aware evaluation time for deterministic lease inspection",
    )

    phase9_research_refresh = subparsers.add_parser(
        "phase9-research-refresh-run",
        help="Recompute missing, non-replay-verified or source-stale Phase 9 research families from persisted sources",
    )
    phase9_research_refresh.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_research_refresh.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_research_refresh.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_research_refresh.add_argument(
        "--no-persist-bundle",
        action="store_true",
        help="Do not persist a ready research bundle after component refresh",
    )
    phase9_research_refresh.add_argument(
        "--require-automatic-ready",
        action="store_true",
        help="Exit non-zero unless adaptive, mint-risk, wallet-flow and contextual-bandit evidence meet their automatic-family thresholds",
    )
    phase9_research_refresh.add_argument(
        "--require-bundle-ready",
        action="store_true",
        help="Exit non-zero unless the complete Phase 9 research bundle is ready after refresh",
    )
    phase9_research_refresh.add_argument(
        "--lease-seconds",
        type=int,
        default=1800,
        help="SQLite lease duration preventing overlapping research-refresh runs",
    )

    phase9_input_template = subparsers.add_parser(
        "phase9-research-input-template",
        help="Generate a Phase 9 explicit-assumption template without inventing economic inputs",
    )
    phase9_input_template.add_argument(
        "--pools",
        help="Optional comma-separated pool set; defaults to current highest-depth chain pools",
    )

    phase9_inputs_ingest = subparsers.add_parser(
        "phase9-research-inputs-ingest",
        help="Validate and persist checksum-bound explicit hedge/portfolio research assumptions",
    )
    phase9_inputs_ingest.add_argument("--file", required=True)

    phase9_inputs_audit = subparsers.add_parser(
        "phase9-research-inputs-audit",
        help="Audit the latest persisted Phase 9 explicit research input artifact and its SHA/boundary",
    )
    phase9_inputs_audit.add_argument(
        "--require-valid",
        action="store_true",
    )

    phase9_explicit_run = subparsers.add_parser(
        "phase9-explicit-research-run",
        help="Run static-hedge and portfolio research from a persisted checksum-bound explicit input artifact",
    )
    phase9_explicit_run.add_argument(
        "--input-evidence-id",
        type=int,
        help="Explicit input evidence ID; defaults to the latest valid artifact",
    )
    phase9_explicit_run.add_argument("--persist", action="store_true")
    phase9_explicit_run.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_bandit_run = subparsers.add_parser(
        "phase9-bandit-research-run",
        help="Build a checksum-bound Phase 9 counterfactual action dataset from explicit inputs and run contextual-bandit research",
    )
    phase9_bandit_run.add_argument(
        "--input-evidence-id",
        type=int,
        help="Explicit input evidence ID; defaults to the latest valid artifact",
    )
    phase9_bandit_run.add_argument(
        "--output-directory",
        help="Optional directory for deterministic checksum-named dataset CSVs",
    )
    phase9_bandit_run.add_argument(
        "--cutoff",
        help="Optional timezone-aware source cutoff; defaults to the latest common observation across the explicit pools",
    )
    phase9_bandit_run.add_argument(
        "--persist",
        action="store_true",
        help="Persist contextual-bandit research evidence; the checksum-bound dataset artifact is always persisted",
    )
    phase9_bandit_run.add_argument(
        "--require-qualified",
        action="store_true",
    )

    phase9_history_plan = subparsers.add_parser(
        "phase9-chain-history-plan",
        help="Calculate exact per-pool Phase 9 chain-history depth required by adaptive walk-forward and regime gates",
    )
    phase9_history_plan.add_argument("--min-pools", type=int, default=3)
    phase9_history_plan.add_argument(
        "--min-qualified-pools",
        type=int,
        default=2,
    )
    phase9_history_plan.add_argument(
        "--min-qualified-pool-rate",
        type=float,
        default=0.67,
    )
    phase9_history_plan.add_argument(
        "--lookback-observations",
        type=int,
        default=96,
    )
    phase9_history_plan.add_argument(
        "--holding-observations",
        type=int,
        default=6,
    )
    phase9_history_plan.add_argument(
        "--min-historical-windows",
        type=int,
        default=12,
    )
    phase9_history_plan.add_argument(
        "--min-decisions",
        type=int,
        default=20,
    )
    phase9_history_plan.add_argument(
        "--regime-lookback-observations",
        type=int,
        default=72,
    )
    phase9_history_plan.add_argument(
        "--regime-recent-observations",
        type=int,
        default=8,
    )
    phase9_history_plan.add_argument(
        "--regime-min-observations",
        type=int,
        default=16,
    )
    phase9_history_plan.add_argument(
        "--as-of",
        help="Optional historical cutoff; only chain observations at or before this time count toward history depth",
    )
    phase9_history_plan.add_argument(
        "--rpc-url",
        help="Optional Solana RPC URL inserted into emitted read-only one-snapshot capture commands",
    )
    phase9_history_plan.add_argument(
        "--executor-bin",
        default="meteora-executor",
    )
    phase9_history_plan.add_argument(
        "--bin-array-radius",
        type=int,
        default=1,
    )
    phase9_history_plan.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_history_run = subparsers.add_parser(
        "phase9-chain-history-run",
        help="Capture one fresh read-only chain snapshot for each Phase 9 pool still below exact adaptive/regime history depth",
    )
    phase9_history_run.add_argument("--min-pools", type=int, default=3)
    phase9_history_run.add_argument(
        "--min-qualified-pools",
        type=int,
        default=2,
    )
    phase9_history_run.add_argument(
        "--min-qualified-pool-rate",
        type=float,
        default=0.67,
    )
    phase9_history_run.add_argument(
        "--lookback-observations",
        type=int,
        default=96,
    )
    phase9_history_run.add_argument(
        "--holding-observations",
        type=int,
        default=6,
    )
    phase9_history_run.add_argument(
        "--min-historical-windows",
        type=int,
        default=12,
    )
    phase9_history_run.add_argument(
        "--min-decisions",
        type=int,
        default=20,
    )
    phase9_history_run.add_argument(
        "--regime-lookback-observations",
        type=int,
        default=72,
    )
    phase9_history_run.add_argument(
        "--regime-recent-observations",
        type=int,
        default=8,
    )
    phase9_history_run.add_argument(
        "--regime-min-observations",
        type=int,
        default=16,
    )
    phase9_history_run.add_argument(
        "--bin-array-radius",
        type=int,
        default=1,
    )
    phase9_history_run.add_argument("--rust-manifest-path")
    phase9_history_run.add_argument("--rust-binary-path")
    phase9_history_run.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
    )
    phase9_history_run.add_argument(
        "--min-observation-interval-seconds",
        type=int,
        default=3600,
        help="Minimum age of the latest pool snapshot before another history observation may be added",
    )
    phase9_history_run.add_argument(
        "--observed-at",
        help="Optional timezone-aware timestamp; must be newer than each captured pool's latest snapshot",
    )
    phase9_history_run.add_argument(
        "--continue-sampling-when-ready",
        action="store_true",
        help="Continue one cadence-guarded observation per selected pool after minimum Phase 9 history depth is already satisfied",
    )
    phase9_history_run.add_argument(
        "--require-ready",
        action="store_true",
    )

    phase9_capture_run = subparsers.add_parser(
        "phase9-chain-capture-run",
        help="Run the Phase 9 read-only Rust inspect-pool capture plan and ingest returned snapshots locally",
    )
    phase9_capture_run.add_argument(
        "--target-chain-pools",
        type=int,
        default=3,
    )
    phase9_capture_run.add_argument(
        "--max-candidates",
        type=int,
        default=8,
    )
    phase9_capture_run.add_argument(
        "--bin-array-radius",
        type=int,
        default=1,
    )
    phase9_capture_run.add_argument(
        "--max-api-snapshot-age-seconds",
        type=int,
        default=10_800,
    )
    phase9_capture_run.add_argument(
        "--api-ranking-as-of",
        help="Optional timezone-aware API ranking cutoff; defaults to current time",
    )
    phase9_capture_run.add_argument(
        "--rust-manifest-path",
    )
    phase9_capture_run.add_argument(
        "--rust-binary-path",
    )
    phase9_capture_run.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
    )
    phase9_capture_run.add_argument(
        "--observed-at",
        help="Optional timestamp used when ingesting the returned read-only snapshots",
    )
    phase9_capture_run.add_argument(
        "--require-target",
        action="store_true",
    )

    phase9_work_queue = subparsers.add_parser(
        "phase9-work-queue",
        help="Show the next concrete missing Phase 9 research, promotion and future-policy simulation evidence tasks",
    )
    phase9_work_queue.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_work_queue.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_work_queue.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_work_queue.add_argument(
        "--rpc-url",
        help="Optional Solana RPC URL used in emitted read-only mint inspection commands",
    )
    phase9_work_queue.add_argument(
        "--as-of",
        help="Optional source cutoff for chain, mint and wallet evidence tasks; later state cannot backfill gaps at this cutoff",
    )
    phase9_work_queue.add_argument(
        "--persist-snapshot",
        action="store_true",
        help="Persist a sanitized append-only progress snapshot without shell commands or RPC URLs",
    )

    phase9_validate = subparsers.add_parser(
        "phase9-validate",
        help="Validate and optionally persist non-actionable Phase 9 research promotion evidence",
    )
    phase9_validate.add_argument(
        "--min-mint-risk-pools",
        type=int,
        default=2,
    )
    phase9_validate.add_argument(
        "--min-wallet-flow-pools",
        type=int,
        default=2,
    )
    phase9_validate.add_argument(
        "--min-static-hedge-pools",
        type=int,
        default=1,
    )
    phase9_validate.add_argument(
        "--persist-ready",
        action="store_true",
    )
    phase9_validate.add_argument(
        "--require-ready",
        action="store_true",
    )

    contextual_bandit_cycle = subparsers.add_parser(
        "contextual-bandit-cycle-research",
        help="Replay contextual-bandit research from a checksum-bound retraining cycle dataset",
    )
    contextual_bandit_cycle.add_argument("--cycle-id", required=True)
    contextual_bandit_cycle.add_argument(
        "--warmup-decisions-per-context",
        type=int,
        default=2,
    )
    contextual_bandit_cycle.add_argument(
        "--exploration-bonus-bps",
        type=float,
        default=50.0,
    )
    contextual_bandit_cycle.add_argument(
        "--min-decisions",
        type=int,
        default=30,
    )
    contextual_bandit_cycle.add_argument(
        "--min-pools",
        type=int,
        default=3,
    )
    contextual_bandit_cycle.add_argument(
        "--min-selected-arms",
        type=int,
        default=2,
    )
    contextual_bandit_cycle.add_argument(
        "--min-mean-uplift-vs-baseline-bps",
        type=float,
        default=0.0,
    )
    contextual_bandit_cycle.add_argument(
        "--max-mean-regret-vs-oracle-bps",
        type=float,
        default=500.0,
    )
    contextual_bandit_cycle.add_argument(
        "--persist",
        action="store_true",
    )
    contextual_bandit_cycle.add_argument(
        "--require-qualified",
        action="store_true",
    )

    contextual_bandit = subparsers.add_parser(
        "contextual-bandit-research",
        help="Replay a research-only contextual bandit over a fixed ML action CSV",
    )
    contextual_bandit.add_argument("--file", required=True)
    contextual_bandit.add_argument(
        "--warmup-decisions-per-context",
        type=int,
        default=2,
    )
    contextual_bandit.add_argument(
        "--exploration-bonus-bps",
        type=float,
        default=50.0,
    )
    contextual_bandit.add_argument("--min-decisions", type=int, default=30)
    contextual_bandit.add_argument("--min-pools", type=int, default=3)
    contextual_bandit.add_argument(
        "--min-selected-arms",
        type=int,
        default=2,
    )
    contextual_bandit.add_argument(
        "--min-mean-uplift-vs-baseline-bps",
        type=float,
        default=0.0,
    )
    contextual_bandit.add_argument(
        "--max-mean-regret-vs-oracle-bps",
        type=float,
        default=500.0,
    )
    contextual_bandit.add_argument("--persist", action="store_true")
    contextual_bandit.add_argument(
        "--require-qualified",
        action="store_true",
    )

    ml_train = subparsers.add_parser(
        "ml-train-csv",
        help="Train, checksum, save and register an experimental ML v1 model",
    )
    ml_train.add_argument("--file", required=True)
    ml_train.add_argument("--model-id", required=True)
    ml_train.add_argument("--dataset-version", required=True)
    ml_train.add_argument("--artifact-dir", required=True)
    ml_train.add_argument("--split-fraction", type=float, default=0.8)
    ml_train.add_argument("--min-rows", type=int, default=50)

    ml_eval = subparsers.add_parser(
        "ml-offline-evaluate-csv",
        help="Evaluate a registered ML challenger on its held-out decision window",
    )
    ml_eval.add_argument("--file", required=True)
    ml_eval.add_argument("--model-id", required=True)
    ml_eval.add_argument("--risk-lambda", type=float, default=1.5)
    ml_eval.add_argument("--min-positive-probability", type=float, default=0.55)
    ml_eval.add_argument("--min-range-survival", type=float, default=0.50)
    ml_eval.add_argument("--min-score-bps", type=float, default=0.0)
    ml_eval.add_argument("--min-decisions", type=int, default=20)
    ml_eval.add_argument("--min-choice-coverage", type=float, default=0.80)
    ml_eval.add_argument("--min-uplift-bps", type=float, default=0.0)
    ml_eval.add_argument("--min-win-rate", type=float, default=0.50)
    ml_eval.add_argument("--min-positive-rate", type=float, default=0.50)
    ml_eval.add_argument("--max-single-loss-bps", type=int, default=1000)
    ml_eval.add_argument("--qualify", action="store_true")
    ml_eval.add_argument("--require-qualified", action="store_true")

    ml_start_paper = subparsers.add_parser(
        "ml-start-paper",
        help="Move an offline-qualified model into PAPER_CHALLENGER state",
    )
    ml_start_paper.add_argument("--model-id", required=True)

    ml_retrain_plan = subparsers.add_parser(
        "ml-retrain-plan",
        help="Plan a new challenger from fresh chain/live evidence",
    )
    ml_retrain_plan.add_argument(
        "--min-new-chain-observations",
        type=int,
        default=500,
    )
    ml_retrain_plan.add_argument(
        "--min-new-chain-pools",
        type=int,
        default=3,
    )
    ml_retrain_plan.add_argument(
        "--min-new-live-labels",
        type=int,
        default=5,
    )
    ml_retrain_plan.add_argument(
        "--max-champion-age-days",
        type=float,
        default=14.0,
    )
    ml_retrain_plan.add_argument("--as-of")
    ml_retrain_plan.add_argument("--persist", action="store_true")
    ml_retrain_plan.add_argument("--require-due", action="store_true")

    ml_retrain_build = subparsers.add_parser(
        "ml-retrain-build",
        help="Build a cutoff-bound multi-pool dataset and start its retraining cycle",
    )
    ml_retrain_build.add_argument(
        "--file",
        required=True,
        help="JSON array of pool_address, amount_x, amount_y, network_cost_y_atomic",
    )
    ml_retrain_build.add_argument("--output", required=True)
    ml_retrain_build.add_argument("--cycle-id")
    ml_retrain_build.add_argument("--as-of")
    ml_retrain_build.add_argument(
        "--lookback-observations",
        type=int,
        default=12,
    )
    ml_retrain_build.add_argument(
        "--forward-observations",
        type=int,
        default=2,
    )
    ml_retrain_build.add_argument("--step-observations", type=int)
    ml_retrain_build.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(0, 1, 2, 5, 10),
    )
    ml_retrain_build.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
    )
    ml_retrain_build.add_argument(
        "--max-share-bps",
        type=int,
        default=500,
    )
    ml_retrain_build.add_argument(
        "--favor-x-active",
        action="store_true",
    )
    ml_retrain_build.add_argument(
        "--min-new-chain-observations",
        type=int,
        default=500,
    )
    ml_retrain_build.add_argument(
        "--min-new-chain-pools",
        type=int,
        default=3,
    )
    ml_retrain_build.add_argument(
        "--min-new-live-labels",
        type=int,
        default=5,
    )
    ml_retrain_build.add_argument(
        "--max-champion-age-days",
        type=float,
        default=14.0,
    )

    ml_retrain_train = subparsers.add_parser(
        "ml-retrain-train",
        help="Train/register a challenger from the exact dataset bound to a retraining cycle",
    )
    ml_retrain_train.add_argument("--cycle-id", required=True)
    ml_retrain_train.add_argument("--file", required=True)
    ml_retrain_train.add_argument("--model-id", required=True)
    ml_retrain_train.add_argument("--artifact-dir", required=True)
    ml_retrain_train.add_argument(
        "--split-fraction",
        type=float,
        default=0.8,
    )
    ml_retrain_train.add_argument("--min-rows", type=int, default=50)

    ml_retrain_walk = subparsers.add_parser(
        "ml-retrain-walk-forward",
        help="Run and persist no-lookahead walk-forward validation for a cycle challenger",
    )
    ml_retrain_walk.add_argument("--cycle-id", required=True)
    ml_retrain_walk.add_argument("--file", required=True)
    ml_retrain_walk.add_argument(
        "--min-train-decision-times",
        type=int,
        default=30,
    )
    ml_retrain_walk.add_argument(
        "--validation-decision-times",
        type=int,
        default=10,
    )
    ml_retrain_walk.add_argument(
        "--step-decision-times",
        type=int,
        default=10,
    )
    ml_retrain_walk.add_argument("--min-folds", type=int, default=3)
    ml_retrain_walk.add_argument(
        "--min-total-comparable-decisions",
        type=int,
        default=30,
    )
    ml_retrain_walk.add_argument(
        "--min-qualified-fold-rate",
        type=float,
        default=0.67,
    )
    ml_retrain_walk.add_argument(
        "--min-mean-fold-uplift-bps",
        type=float,
        default=0.0,
    )
    ml_retrain_walk.add_argument(
        "--min-positive-fold-rate",
        type=float,
        default=0.50,
    )
    ml_retrain_walk.add_argument(
        "--max-worst-fold-uplift-loss-bps",
        type=float,
        default=250.0,
    )
    ml_retrain_walk.add_argument(
        "--training-split-fraction",
        type=float,
        default=0.8,
    )
    ml_retrain_walk.add_argument(
        "--training-min-rows",
        type=int,
        default=50,
    )
    ml_retrain_walk.add_argument("--require-qualified", action="store_true")

    ml_retrain_start = subparsers.add_parser(
        "ml-retrain-start",
        help="Start one immutable evidence-bound continuous retraining cycle",
    )
    ml_retrain_start.add_argument("--dataset-version", required=True)
    ml_retrain_start.add_argument("--cycle-id")
    ml_retrain_start.add_argument("--as-of")
    ml_retrain_start.add_argument(
        "--min-new-chain-observations",
        type=int,
        default=500,
    )
    ml_retrain_start.add_argument(
        "--min-new-chain-pools",
        type=int,
        default=3,
    )
    ml_retrain_start.add_argument(
        "--min-new-live-labels",
        type=int,
        default=5,
    )
    ml_retrain_start.add_argument(
        "--max-champion-age-days",
        type=float,
        default=14.0,
    )

    ml_retrain_status = subparsers.add_parser(
        "ml-retrain-status",
        help="Inspect an active or named continuous retraining cycle",
    )
    ml_retrain_status.add_argument("--cycle-id")

    ml_retrain_sync = subparsers.add_parser(
        "ml-retrain-sync",
        help="Synchronize a retraining cycle with its challenger registry state",
    )
    ml_retrain_sync.add_argument("--cycle-id", required=True)

    ml_retrain_cancel = subparsers.add_parser(
        "ml-retrain-cancel",
        help="Cancel a retraining cycle after any attached challenger is terminal",
    )
    ml_retrain_cancel.add_argument("--cycle-id", required=True)
    ml_retrain_cancel.add_argument("--notes")

    ml_retrain_attach = subparsers.add_parser(
        "ml-retrain-attach",
        help="Attach a registered offline candidate to a retraining cycle",
    )
    ml_retrain_attach.add_argument("--cycle-id", required=True)
    ml_retrain_attach.add_argument("--model-id", required=True)

    ml_continuous_validate = subparsers.add_parser(
        "ml-continuous-validate",
        help="Validate and optionally rotate a retrained challenger over the incumbent champion",
    )
    ml_continuous_validate.add_argument("--cycle-id", required=True)
    ml_continuous_validate.add_argument("--account", required=True)
    ml_continuous_validate.add_argument(
        "--min-challenger-closed-trades",
        type=int,
        default=20,
    )
    ml_continuous_validate.add_argument(
        "--min-incumbent-closed-trades",
        type=int,
        default=20,
    )
    ml_continuous_validate.add_argument(
        "--min-challenger-return-bps",
        type=int,
        default=0,
    )
    ml_continuous_validate.add_argument(
        "--min-challenger-win-rate",
        type=float,
        default=0.50,
    )
    ml_continuous_validate.add_argument(
        "--max-challenger-drawdown-bps",
        type=int,
        default=1500,
    )
    ml_continuous_validate.add_argument(
        "--max-single-trade-loss-bps",
        type=int,
        default=1000,
    )
    ml_continuous_validate.add_argument(
        "--min-uplift-vs-incumbent-bps",
        type=int,
        default=0,
    )
    ml_continuous_validate.add_argument("--promote", action="store_true")
    ml_continuous_validate.add_argument(
        "--require-qualified",
        action="store_true",
    )

    ml_live_monitor = subparsers.add_parser(
        "ml-live-monitor",
        help="Evaluate persisted live labels for champion rollback safety",
    )
    ml_live_monitor.add_argument("--model-id")
    ml_live_monitor.add_argument("--min-live-labels", type=int, default=10)
    ml_live_monitor.add_argument("--min-live-pools", type=int, default=2)
    ml_live_monitor.add_argument(
        "--max-drawdown-bps",
        type=int,
        default=2000,
    )
    ml_live_monitor.add_argument(
        "--max-single-loss-bps",
        type=int,
        default=1500,
    )
    ml_live_monitor.add_argument(
        "--min-win-rate",
        type=float,
        default=0.30,
    )
    ml_live_monitor.add_argument(
        "--min-mean-return-bps",
        type=float,
        default=-100.0,
    )
    ml_live_monitor.add_argument(
        "--max-mean-abs-prediction-error-bps",
        type=float,
        default=1500.0,
    )
    ml_live_monitor.add_argument("--persist", action="store_true")
    ml_live_monitor.add_argument("--rollback", action="store_true")
    ml_live_monitor.add_argument("--require-healthy", action="store_true")

    ml_status = subparsers.add_parser(
        "ml-model-status",
        help="Print one registered ML model state",
    )
    ml_status.add_argument("--model-id", required=True)

    phase3_plan = subparsers.add_parser(
        "phase3-plan",
        help="Build one end-to-end research-only Phase 3 entry plan",
    )
    phase3_plan.add_argument("--pool", required=True)
    phase3_plan.add_argument("--amount-x", required=True, type=int)
    phase3_plan.add_argument("--amount-y", required=True, type=int)
    phase3_plan.add_argument("--requested-quote", required=True, type=float)
    phase3_plan.add_argument("--equity", required=True, type=float)
    phase3_plan.add_argument("--cash", required=True, type=float)
    phase3_plan.add_argument("--deployed", required=True, type=float)
    phase3_plan.add_argument("--drawdown-bps", required=True, type=int)
    phase3_plan.add_argument("--network-cost-y-atomic", required=True, type=int)
    phase3_plan.add_argument("--observations", type=int, default=12)
    phase3_plan.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(0, 1, 2, 5, 10),
    )
    phase3_plan.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
    )
    phase3_plan.add_argument(
        "--strategies",
        type=_parse_strategy_csv,
        default=tuple(StrategyType),
    )
    phase3_plan.add_argument("--max-share-bps", type=int, default=500)
    phase3_plan.add_argument("--favor-x-active", action="store_true")

    multi_pool = subparsers.add_parser(
        "phase3-multi-plan",
        help="Compare equal-notional Phase 3 research plans across pools",
    )
    multi_pool.add_argument("--file", required=True, help="JSON array of pool plans")
    multi_pool.add_argument("--equity", required=True, type=float)
    multi_pool.add_argument("--cash", required=True, type=float)
    multi_pool.add_argument("--deployed", required=True, type=float)
    multi_pool.add_argument("--drawdown-bps", required=True, type=int)
    multi_pool.add_argument("--observations", type=int, default=12)
    multi_pool.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(0, 1, 2, 5, 10),
    )
    multi_pool.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
    )
    multi_pool.add_argument(
        "--strategies",
        type=_parse_strategy_csv,
        default=tuple(StrategyType),
    )
    multi_pool.add_argument("--max-share-bps", type=int, default=500)
    multi_pool.add_argument("--favor-x-active", action="store_true")

    ingest = subparsers.add_parser(
        "ingest-chain-snapshot",
        help="Ingest JSON emitted by the Rust read-only inspect-pool command",
    )
    ingest.add_argument(
        "--file",
        default="-",
        help="JSON file path, or - for stdin",
    )

    ingest_mint = subparsers.add_parser(
        "ingest-mint-snapshot",
        help="Ingest JSON emitted by the Rust read-only inspect-mint command",
    )
    ingest_mint.add_argument(
        "--file",
        default="-",
        help="JSON file path, or - for stdin",
    )

    ingest_decision_context_cmd = subparsers.add_parser(
        "ingest-execution-decision-context",
        help="Ingest terminal Rust execution decision context for learning attribution",
    )
    ingest_decision_context_cmd.add_argument(
        "--file",
        default="-",
        help="Rust execution-decision-context JSON file, or - for stdin",
    )

    ingest_execution_receipt_cmd = subparsers.add_parser(
        "ingest-execution-receipt",
        help="Ingest a terminal receipt emitted by Rust execution-receipt",
    )
    ingest_execution_receipt_cmd.add_argument(
        "--file",
        default="-",
        help="JSON file path, or - for stdin",
    )

    live_ledger_audit_cmd = subparsers.add_parser(
        "live-execution-ledger-audit",
        help="Audit receipt/effect/position/closure/outcome live execution integrity",
    )
    live_ledger_audit_cmd.add_argument(
        "--require-clean",
        action="store_true",
    )

    live_outcome_cmd = subparsers.add_parser(
        "build-live-position-outcome",
        help="Aggregate one CLOSED live position into immutable atomic outcome evidence",
    )
    live_outcome_cmd.add_argument("--position", required=True)

    live_value_cmd = subparsers.add_parser(
        "value-live-position-outcome",
        help="Value one CLOSED live outcome from historical no-lookahead quotes",
    )
    live_value_cmd.add_argument("--position", required=True)
    live_value_cmd.add_argument(
        "--max-age-seconds",
        type=int,
        default=300,
    )

    live_label_cmd = subparsers.add_parser(
        "build-live-learning-label",
        help="Join valued live PnL to the original confirmed model decision",
    )
    live_label_cmd.add_argument("--position", required=True)

    live_close_cmd = subparsers.add_parser(
        "finalize-live-position-closure",
        help="Mark a liquidity-removed live position closed from confirmed Rust RPC proof",
    )
    live_close_cmd.add_argument("--decision", required=True)
    live_close_cmd.add_argument(
        "--file",
        default="-",
        help="Rust verify-position-closed JSON file, or - for stdin",
    )

    live_position_cmd = subparsers.add_parser(
        "apply-live-position-effect",
        help="Apply one reconciled live execution effect to position lifecycle state",
    )
    live_position_cmd.add_argument("--decision", required=True)

    live_effect_cmd = subparsers.add_parser(
        "apply-live-execution-effect",
        help="Persist atomic live wallet effects from a reconciled execution receipt",
    )
    live_effect_cmd.add_argument("--decision", required=True)

    execution_receipt_audit_cmd = subparsers.add_parser(
        "execution-receipt-audit",
        help="Audit stored live execution receipts against Solana snapshots",
    )
    execution_receipt_audit_cmd.add_argument(
        "--require-clean",
        action="store_true",
    )

    ingest_tx_events = subparsers.add_parser(
        "ingest-transaction-events",
        help="Ingest JSON emitted by Rust inspect-transaction-events",
    )
    ingest_tx_events.add_argument(
        "--file",
        default="-",
        help="JSON file path, or - for stdin",
    )

    add_execution = subparsers.add_parser(
        "add-execution",
        help="Compare requested versus actual Meteora add-liquidity execution",
    )
    add_execution.add_argument(
        "--position",
        required=True,
        help="Meteora position address",
    )

    rebalance_execution = subparsers.add_parser(
        "rebalance-execution",
        help="Check real Meteora rebalance events against transaction guard bounds",
    )
    rebalance_execution.add_argument(
        "--position",
        required=True,
        help="Meteora position address",
    )

    subparsers.add_parser(
        "phase2-evidence",
        help="Summarize exact real-data calibration evidence for Phase 2",
    )
    subparsers.add_parser(
        "phase2-work-queue",
        help="List actionable missing calibration work from the local database",
    )

    transaction_costs = subparsers.add_parser(
        "transaction-costs",
        help="Summarize real Solana fees and compute usage for one position",
    )
    transaction_costs.add_argument(
        "--position",
        required=True,
        help="Meteora position address",
    )

    composition_prestate = subparsers.add_parser(
        "composition-prestate",
        help="Find slot-bounded pre-add snapshots eligible for exact verification",
    )
    composition_prestate.add_argument("--position", required=True)

    ingest_prestate = subparsers.add_parser(
        "ingest-prestate-verification",
        help="Ingest JSON emitted by Rust verify-prestate",
    )
    ingest_prestate.add_argument("--snapshot-observed-at", required=True)
    ingest_prestate.add_argument("--pool", required=True)
    ingest_prestate.add_argument("--file", default="-")

    composition_reconcile = subparsers.add_parser(
        "reconcile-composition",
        help="Compare exact verified prestate composition math with chain events",
    )
    composition_reconcile.add_argument("--position", required=True)

    composition_labels = subparsers.add_parser(
        "composition-labels",
        help="Join position add history to decoded on-chain composition-fee events",
    )
    composition_labels.add_argument(
        "--position",
        required=True,
        help="Meteora position address",
    )

    collect_history = subparsers.add_parser(
        "collect-position-history",
        help="Fetch and persist Meteora Data API lifecycle events for one position",
    )
    collect_history.add_argument(
        "--position",
        required=True,
        help="Meteora position address",
    )

    ingest_position = subparsers.add_parser(
        "ingest-position-snapshot",
        help="Ingest JSON emitted by Rust inspect-position",
    )
    ingest_position.add_argument(
        "--file",
        default="-",
        help="JSON file path, or - for stdin",
    )

    replay = subparsers.add_parser(
        "replay-chain",
        help="Replay a small hypothetical standard-SPL LP over recent chain snapshots",
    )
    replay.add_argument("--pool", required=True, help="Meteora pool address")
    replay.add_argument("--amount-x", required=True, type=int, help="Atomic token X amount")
    replay.add_argument("--amount-y", required=True, type=int, help="Atomic token Y amount")
    replay.add_argument("--min-bin", required=True, type=int)
    replay.add_argument("--max-bin", required=True, type=int)
    replay.add_argument(
        "--strategy",
        choices=[item.value for item in StrategyType],
        default=StrategyType.SPOT.value,
    )
    replay.add_argument(
        "--observations",
        type=int,
        default=2,
        help="Number of latest chain snapshots to replay",
    )
    replay.add_argument(
        "--max-share-bps",
        type=int,
        default=500,
        help="Reject if projected share exceeds this fraction of any starting bin supply",
    )
    replay.add_argument(
        "--favor-x-active",
        action="store_true",
        help="Put the active bin on the X/ask side",
    )

    rebalance = subparsers.add_parser(
        "replay-rebalance",
        help="Replay deterministic out-of-range rebalances over chain snapshots",
    )
    rebalance.add_argument("--pool", required=True, help="Meteora pool address")
    rebalance.add_argument("--amount-x", required=True, type=int, help="Atomic token X amount")
    rebalance.add_argument("--amount-y", required=True, type=int, help="Atomic token Y amount")
    rebalance.add_argument("--half-width", required=True, type=int)
    rebalance.add_argument("--center-offset", type=int, default=0)
    rebalance.add_argument(
        "--strategy",
        choices=[item.value for item in StrategyType],
        default=StrategyType.SPOT.value,
    )
    rebalance.add_argument("--observations", type=int, default=24)
    rebalance.add_argument("--max-share-bps", type=int, default=500)
    rebalance.add_argument("--favor-x-active", action="store_true")

    reconcile = subparsers.add_parser(
        "reconcile-position",
        help="Compare Python amount/fee math with stored exact DynamicPosition snapshots",
    )
    reconcile.add_argument("--position", required=True, help="Meteora position address")

    reconcile_corpus = subparsers.add_parser(
        "reconcile-corpus",
        help="Aggregate exact DynamicPosition reconciliation across stored positions",
    )
    reconcile_corpus.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Validate only the most recently observed N positions",
    )
    reconcile_corpus.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit non-zero unless the strict deterministic math gate passes",
    )

    phase2_gate = subparsers.add_parser(
        "phase2-gate",
        help="Evaluate exact-math and sample-size promotion criteria for Phase 2",
    )
    phase2_gate.add_argument("--min-positions", required=True, type=int)
    phase2_gate.add_argument("--min-amount-bins", required=True, type=int)
    phase2_gate.add_argument("--min-fee-intervals", required=True, type=int)
    phase2_gate.add_argument("--min-fee-bins", required=True, type=int)
    phase2_gate.add_argument("--min-reward-intervals", required=True, type=int)
    phase2_gate.add_argument("--min-reward-growth-bins", required=True, type=int)
    phase2_gate.add_argument("--min-composition-samples", type=int, default=1)
    phase2_gate.add_argument("--min-add-execution-samples", type=int, default=1)
    phase2_gate.add_argument("--min-rebalance-guard-samples", type=int, default=1)
    phase2_gate.add_argument("--min-transaction-fee-samples", type=int, default=1)
    phase2_gate.add_argument(
        "--min-amount-coverage-rate",
        type=float,
        default=1.0,
        help="Required fraction of stored positions eligible for amount reconciliation",
    )
    phase2_gate.add_argument(
        "--position-limit",
        type=int,
        default=None,
        help="Evaluate only the most recently observed N positions",
    )
    phase2_gate.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero unless all math and sample criteria pass",
    )
    phase2_gate.add_argument(
        "--persist-ready",
        action="store_true",
        help="Persist Phase 2 promotion evidence when the gate passes",
    )

    scan = subparsers.add_parser(
        "scan-chain",
        help="Compare a grid of small-LP candidates over recent chain snapshots",
    )
    scan.add_argument("--pool", required=True, help="Meteora pool address")
    scan.add_argument("--amount-x", required=True, type=int, help="Atomic token X amount")
    scan.add_argument("--amount-y", required=True, type=int, help="Atomic token Y amount")
    scan.add_argument(
        "--observations",
        type=int,
        default=12,
        help="Number of latest chain snapshots to scan",
    )
    scan.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(0, 1, 2, 5, 10),
        help="Comma-separated range half-widths in bins",
    )
    scan.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
        help="Comma-separated range center offsets in bins",
    )
    scan.add_argument(
        "--strategies",
        type=_parse_strategy_csv,
        default=(
            StrategyType.SPOT,
            StrategyType.CURVE,
            StrategyType.BID_ASK,
        ),
        help="Comma-separated strategies: SPOT,CURVE,BID_ASK",
    )
    scan.add_argument(
        "--max-share-bps",
        type=int,
        default=500,
        help="Reject a candidate if its share exceeds this fraction of any observed bin supply",
    )
    scan.add_argument(
        "--favor-x-active",
        action="store_true",
        help="Put the active bin on the X/ask side",
    )

    baseline_walk = subparsers.add_parser(
        "baseline-walk-forward",
        help="Research-only deterministic baseline walk-forward on chain snapshots",
    )
    baseline_walk.add_argument("--pool", required=True, help="Meteora pool address")
    baseline_walk.add_argument("--amount-x", required=True, type=int)
    baseline_walk.add_argument("--amount-y", required=True, type=int)
    baseline_walk.add_argument("--lookback-observations", type=int, default=12)
    baseline_walk.add_argument("--forward-observations", type=int, default=2)
    baseline_walk.add_argument("--step-observations", type=int, default=None)
    baseline_walk.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(0, 1, 2, 5, 10),
    )
    baseline_walk.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
    )
    baseline_walk.add_argument(
        "--strategies",
        type=_parse_strategy_csv,
        default=(
            StrategyType.SPOT,
            StrategyType.CURVE,
            StrategyType.BID_ASK,
        ),
    )
    baseline_walk.add_argument("--max-share-bps", type=int, default=500)
    baseline_walk.add_argument("--favor-x-active", action="store_true")
    baseline_walk.add_argument(
        "--network-cost-y-atomic",
        required=True,
        type=int,
        help="Conservative per-entry network cost expressed in token-Y atomic units",
    )
    baseline_walk.add_argument(
        "--min-range-survival",
        type=float,
        default=0.75,
    )
    baseline_walk.add_argument(
        "--min-excess-vs-hold-bps",
        type=int,
        default=0,
    )
    baseline_walk.add_argument(
        "--allow-unrecovered-entry-cost",
        action="store_true",
    )

    backtest = subparsers.add_parser(
        "backtest-inventory",
        help="Compare range candidates on stored candles without simulated fee income",
    )
    backtest.add_argument("--pool", required=True, help="Meteora pool address")
    backtest.add_argument("--capital", required=True, type=float, help="Quote-value starting capital")
    backtest.add_argument("--candles", type=int, default=250, help="Number of latest stored candles")
    backtest.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(1, 2, 5, 10, 20, 30),
        help="Comma-separated half-widths in bins",
    )
    backtest.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
        help="Comma-separated center offsets in bins",
    )

    args = parser.parse_args()

    if args.command == "collect-once":
        result = collect_once(Settings.from_env())
        print(json.dumps(result.__dict__, indent=2))
        return

    if args.command == "protocol-metrics":
        settings = Settings.from_env()
        with MeteoraDataAPI(base_url=settings.meteora_data_api) as api:
            print(json.dumps(api.protocol_metrics(), indent=2)[:20000])
        return

    if args.command == "data-status":
        settings = Settings.from_env()
        print(json.dumps(Storage(settings.database_path).data_status(), indent=2))
        return

    if args.command == "ingest-chain-snapshot":
        settings = Settings.from_env()
        if args.file == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        result = ingest_chain_snapshot(Storage(settings.database_path), payload)
        print(json.dumps(result.__dict__, indent=2))
        return

    if args.command == "ingest-mint-snapshot":
        settings = Settings.from_env()
        if args.file == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        result = ingest_mint_snapshot(
            Storage(settings.database_path),
            payload,
        )
        print(json.dumps(result.__dict__, indent=2))
        return

    if args.command == "live-execution-ledger-audit":
        settings = Settings.from_env()
        result = audit_live_execution_ledger(
            Storage(settings.database_path),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_clean and not result.clean:
            raise SystemExit(2)
        return

    if args.command == "build-live-position-outcome":
        settings = Settings.from_env()
        result = build_live_position_outcome(
            Storage(settings.database_path),
            position_address=args.position,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "value-live-position-outcome":
        settings = Settings.from_env()
        result = value_live_position_outcome(
            Storage(settings.database_path),
            position_address=args.position,
            max_age_seconds=args.max_age_seconds,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "build-live-learning-label":
        settings = Settings.from_env()
        result = build_live_learning_label(
            Storage(settings.database_path),
            position_address=args.position,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "finalize-live-position-closure":
        settings = Settings.from_env()
        if args.file == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        result = finalize_live_position_closure(
            Storage(settings.database_path),
            decision_id=args.decision,
            proof=payload,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "apply-live-position-effect":
        settings = Settings.from_env()
        result = apply_live_position_effect(
            Storage(settings.database_path),
            args.decision,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "apply-live-execution-effect":
        settings = Settings.from_env()
        result = apply_live_execution_effect(
            Storage(settings.database_path),
            args.decision,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "execution-receipt-audit":
        settings = Settings.from_env()
        result = audit_execution_receipts(
            Storage(settings.database_path),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_clean and not result.clean:
            raise SystemExit(2)
        return

    if args.command == "ingest-execution-decision-context":
        settings = Settings.from_env()
        if args.file == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        result = ingest_execution_decision_context(
            Storage(settings.database_path),
            payload,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "ingest-execution-receipt":
        settings = Settings.from_env()
        if args.file == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        result = ingest_execution_receipt(
            Storage(settings.database_path),
            payload,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "ingest-transaction-events":
        settings = Settings.from_env()
        if args.file == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        result = ingest_transaction_events(
            Storage(settings.database_path),
            payload,
        )
        print(json.dumps(result.__dict__, indent=2))
        return

    if args.command == "add-execution":
        settings = Settings.from_env()
        result = build_add_execution_calibration(
            str(settings.database_path),
            position_address=args.position,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "rebalance-execution":
        settings = Settings.from_env()
        result = build_rebalance_execution_calibration(
            str(settings.database_path),
            position_address=args.position,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "phase2-evidence":
        settings = Settings.from_env()
        result = build_phase2_calibration_evidence(
            str(settings.database_path),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "phase2-work-queue":
        settings = Settings.from_env()
        result = build_calibration_work_queue(
            str(settings.database_path),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "transaction-costs":
        settings = Settings.from_env()
        result = build_transaction_cost_report(
            str(settings.database_path),
            position_address=args.position,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "composition-prestate":
        settings = Settings.from_env()
        result = build_composition_prestate_candidates(
            str(settings.database_path),
            position_address=args.position,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "ingest-prestate-verification":
        settings = Settings.from_env()
        if args.file == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        result = ingest_prestate_verification(
            Storage(settings.database_path),
            payload,
            snapshot_observed_at=args.snapshot_observed_at,
            pool_address=args.pool,
        )
        print(json.dumps(result.__dict__, indent=2))
        return

    if args.command == "reconcile-composition":
        settings = Settings.from_env()
        result = build_composition_fee_reconciliation(
            str(settings.database_path),
            position_address=args.position,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "composition-labels":
        settings = Settings.from_env()
        result = build_composition_fee_labels(
            str(settings.database_path),
            position_address=args.position,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "collect-position-history":
        settings = Settings.from_env()
        with MeteoraDataAPI(base_url=settings.meteora_data_api) as api:
            result = collect_position_history(
                Storage(settings.database_path),
                api,
                args.position,
            )
        print(json.dumps(result.__dict__, indent=2))
        return

    if args.command == "ingest-position-snapshot":
        settings = Settings.from_env()
        if args.file == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        result = ingest_position_snapshot(Storage(settings.database_path), payload)
        print(json.dumps(result.__dict__, indent=2))
        return

    if args.command == "replay-chain":
        settings = Settings.from_env()
        result = replay_small_lp_history(
            str(settings.database_path),
            pool_address=args.pool,
            amount_x=args.amount_x,
            amount_y=args.amount_y,
            min_bin_id=args.min_bin,
            max_bin_id=args.max_bin,
            strategy=StrategyType(args.strategy),
            observation_limit=args.observations,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "replay-rebalance":
        settings = Settings.from_env()
        result = replay_rebalance_lifecycle(
            str(settings.database_path),
            pool_address=args.pool,
            amount_x=args.amount_x,
            amount_y=args.amount_y,
            half_width=args.half_width,
            center_offset=args.center_offset,
            strategy=StrategyType(args.strategy),
            observation_limit=args.observations,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "reconcile-position":
        settings = Settings.from_env()
        result = reconcile_position(
            str(settings.database_path),
            position_address=args.position,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "reconcile-corpus":
        settings = Settings.from_env()
        result = build_reconciliation_corpus(
            str(settings.database_path),
            position_limit=args.limit,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_pass and not result.strict_math_gate_passed:
            raise SystemExit(2)
        return

    if args.command == "phase2-gate":
        settings = Settings.from_env()
        result = evaluate_phase2_promotion_gate(
            str(settings.database_path),
            criteria=Phase2PromotionCriteria(
                min_positions=args.min_positions,
                min_amount_bins=args.min_amount_bins,
                min_fee_intervals=args.min_fee_intervals,
                min_fee_bins=args.min_fee_bins,
                min_reward_intervals=args.min_reward_intervals,
                min_reward_growth_bins=args.min_reward_growth_bins,
                min_composition_samples=args.min_composition_samples,
                min_add_execution_samples=args.min_add_execution_samples,
                min_rebalance_guard_samples=args.min_rebalance_guard_samples,
                min_transaction_fee_samples=args.min_transaction_fee_samples,
                min_amount_coverage_rate=args.min_amount_coverage_rate,
            ),
            position_limit=args.position_limit,
        )
        output = result.to_record()
        if args.persist_ready and result.promotion_ready:
            state = persist_phase2_promotion(
                Storage(settings.database_path),
                report=result,
            )
            output["persisted_promotion"] = {
                "phase_name": state.phase_name,
                "promoted": state.promoted,
                "evidence_type": state.evidence_type,
            }
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.promotion_ready:
            raise SystemExit(2)
        return

    if args.command == "phase-status":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        phase8_state = phase_promotion_state(
            storage,
            phase_name=PHASE8,
        )
        phase8_currentness = (
            audit_persisted_phase8_promotion(storage)
            if phase8_state.promoted
            else None
        )
        phase9_state = phase_promotion_state(
            storage,
            phase_name=PHASE9,
        )
        phase9_currentness = (
            audit_persisted_phase9_promotion(storage)
            if phase9_state.promoted
            else None
        )
        phase9_policy_status = evaluate_phase9_policy_status(storage)
        output = {
            "phase2": phase_promotion_state(
                storage,
                phase_name=PHASE2,
            ).__dict__,
            "phase3": phase_promotion_state(
                storage,
                phase_name=PHASE3,
            ).__dict__,
            "phase5": phase_promotion_state(
                storage,
                phase_name=PHASE5,
            ).__dict__,
            "phase6": phase_promotion_state(
                storage,
                phase_name=PHASE6,
            ).__dict__,
            "phase7": phase_promotion_state(
                storage,
                phase_name=PHASE7,
            ).__dict__,
            "phase8": {
                **phase8_state.__dict__,
                "current": (
                    phase8_currentness.current
                    if phase8_currentness is not None
                    else False
                ),
                "currentness": (
                    phase8_currentness.to_record()
                    if phase8_currentness is not None
                    else None
                ),
            },
            "phase9": {
                **phase9_state.__dict__,
                "research_only": True,
                "policy_actionable": False,
                "current": (
                    phase9_currentness.current
                    if phase9_currentness is not None
                    else False
                ),
                "currentness": (
                    phase9_currentness.to_record()
                    if phase9_currentness is not None
                    else None
                ),
                "future_policy_simulation": (
                    phase9_policy_status.to_record()
                ),
            },
        }
        print(json.dumps(output, indent=2))
        return

    if args.command == "phase3-validate":
        settings = Settings.from_env()
        with open(args.file, "r", encoding="utf-8") as handle:
            raw_inputs = json.load(handle)
        if not isinstance(raw_inputs, list):
            raise ValueError("phase3-validate file must contain a JSON array")
        inputs = tuple(
            Phase3ValidationInput(
                pool_address=str(item["pool_address"]),
                amount_x=int(item["amount_x"]),
                amount_y=int(item["amount_y"]),
                network_cost_y_atomic=int(item["network_cost_y_atomic"]),
            )
            for item in raw_inputs
        )
        result = validate_phase3_from_chain(
            str(settings.database_path),
            inputs=inputs,
            criteria=Phase3PromotionCriteria(
                min_pools=args.min_pools,
                min_total_steps=args.min_total_steps,
                min_complete_steps=args.min_complete_steps,
                min_selection_rate=args.min_selection_rate,
                min_complete_rate=args.min_complete_rate,
                min_positive_excess_rate=args.min_positive_excess_rate,
                min_mean_excess_vs_hold_bps=args.min_mean_excess_bps,
                max_single_step_loss_bps=args.max_single_step_loss_bps,
            ),
            persist_if_ready=args.persist_ready,
            lookback_observations=args.lookback_observations,
            forward_observations=args.forward_observations,
            step_observations=args.step_observations,
            half_widths=args.half_widths,
            center_offsets=args.center_offsets,
            strategies=args.strategies,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2, default=str))
        if args.require_ready and not result.promotion.promotion_ready:
            raise SystemExit(2)
        return

    if args.command == "phase5-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase5_promotion(
            storage,
            account_id=args.account,
            criteria=Phase5PromotionCriteria(
                min_runtime_hours=args.min_runtime_hours,
                min_terminal_ticks=args.min_terminal_ticks,
                min_success_rate_pct=args.min_success_rate_pct,
                max_dependency_blocked_pct=args.max_dependency_blocked_pct,
                max_consecutive_failures=args.max_consecutive_failures,
                max_stale_running_ticks=args.max_stale_running_ticks,
                stale_running_after_seconds=args.stale_running_after_seconds,
                min_applied_chain_valuations=(
                    args.min_applied_chain_valuations
                ),
                min_distinct_positions_valued=(
                    args.min_distinct_positions_valued
                ),
                min_closed_positions=args.min_closed_positions,
                min_distinct_pools=args.min_distinct_pools,
            ),
        )
        output = result.to_record()
        if args.persist_ready and result.promotion_ready:
            output["persisted"] = persist_phase5_promotion(
                storage,
                report=result,
            ).__dict__
        else:
            output["persisted"] = None
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.promotion_ready:
            raise SystemExit(2)
        return

    if args.command == "phase6-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase6_promotion(
            storage,
            execution_db=args.execution_db,
            criteria=Phase6PromotionCriteria(
                min_passed_enter_intents=args.min_passed_enter_intents,
                min_distinct_pools=args.min_distinct_pools,
                min_blocked_intents=args.min_blocked_intents,
                max_postsimulation_intents=(
                    args.max_postsimulation_intents
                ),
            ),
        )
        output = result.to_record()
        if args.persist_ready and result.promotion_ready:
            output["persisted"] = persist_phase6_promotion(
                storage,
                report=result,
            ).__dict__
        else:
            output["persisted"] = None
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.promotion_ready:
            raise SystemExit(2)
        return

    if args.command == "phase7-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase7_promotion(
            storage,
            criteria=Phase7PromotionCriteria(
                min_closed_positions=args.min_closed_positions,
                min_distinct_pools=args.min_distinct_pools,
                min_confirmed_receipts=args.min_confirmed_receipts,
                max_failed_receipts=args.max_failed_receipts,
                max_open_positions_at_validation=(
                    args.max_open_positions_at_validation
                ),
            ),
        )
        output = result.to_record()
        if args.persist_ready and result.promotion_ready:
            output["persisted"] = persist_phase7_promotion(
                storage,
                report=result,
            ).__dict__
        else:
            output["persisted"] = None
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.promotion_ready:
            raise SystemExit(2)
        return

    if args.command == "phase8-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase8_promotion(
            storage,
            criteria=Phase8PromotionCriteria(
                min_completed_cycles=args.min_completed_cycles,
                min_live_labels=args.min_live_labels,
                min_live_pools=args.min_live_pools,
                max_realized_drawdown_bps=(
                    args.max_realized_drawdown_bps
                ),
                max_single_loss_bps=args.max_single_loss_bps,
                min_win_rate=args.min_win_rate,
                min_mean_return_bps=args.min_mean_return_bps,
                max_mean_abs_prediction_error_bps=(
                    args.max_mean_abs_prediction_error_bps
                ),
            ),
        )
        output = result.to_record()
        if args.persist_ready and result.promotion_ready:
            output["persisted"] = persist_phase8_promotion(
                storage,
                report=result,
            ).__dict__
        else:
            output["persisted"] = None
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.promotion_ready:
            raise SystemExit(2)
        return

    if args.command == "dlmm-regime-research":
        settings = Settings.from_env()
        result = classify_dlmm_regime(
            Storage(settings.database_path),
            pool_address=args.pool,
            criteria=DLMMRegimeCriteria(
                lookback_observations=args.lookback_observations,
                recent_observations=args.recent_observations,
                min_observations=args.min_observations,
                trend_efficiency_threshold=(
                    args.trend_efficiency_threshold
                ),
                activity_percentile=args.activity_percentile,
                quiet_percentile=args.quiet_percentile,
            ),
            as_of=args.as_of,
        )
        print(json.dumps(result.to_record(), indent=2))
        if (
            args.require_ready
            and result.status != "RESEARCH_READY"
        ):
            raise SystemExit(2)
        return

    if args.command == "adaptive-range-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = validate_adaptive_range_walk_forward(
            storage,
            pool_address=args.pool,
            adaptive_criteria=AdaptiveRangeCriteria(
                lookback_observations=args.lookback_observations,
                holding_observations=args.holding_observations,
                target_coverage=args.target_coverage,
                min_half_width_bins=args.min_half_width_bins,
                max_half_width_bins=args.max_half_width_bins,
                min_historical_windows=args.min_historical_windows,
            ),
            validation_criteria=AdaptiveRangeValidationCriteria(
                fixed_half_width_bins=args.fixed_half_width_bins,
                min_decisions=args.min_decisions,
                min_adaptive_survival_rate=(
                    args.min_adaptive_survival_rate
                ),
                min_survival_uplift_vs_fixed=(
                    args.min_survival_uplift_vs_fixed
                ),
                max_mean_width_multiple_vs_fixed=(
                    args.max_mean_width_multiple_vs_fixed
                ),
                max_cap_exceeded_rate=args.max_cap_exceeded_rate,
            ),
            as_of=args.as_of,
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_adaptive_range_validation(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_qualified and not result.research_qualified:
            raise SystemExit(2)
        return

    if args.command == "adaptive-range-research":
        settings = Settings.from_env()
        result = research_adaptive_range(
            Storage(settings.database_path),
            pool_address=args.pool,
            criteria=AdaptiveRangeCriteria(
                lookback_observations=args.lookback_observations,
                holding_observations=args.holding_observations,
                target_coverage=args.target_coverage,
                min_half_width_bins=args.min_half_width_bins,
                max_half_width_bins=args.max_half_width_bins,
                min_historical_windows=args.min_historical_windows,
            ),
            as_of=args.as_of,
        )
        print(json.dumps(result.to_record(), indent=2))
        if (
            args.require_ready
            and result.status != "RESEARCH_READY"
        ):
            raise SystemExit(2)
        return

    if args.command == "static-hedge-research":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = research_static_inventory_hedge(
            storage,
            pool_address=args.pool,
            amount_x=args.amount_x,
            amount_y=args.amount_y,
            instrument=HedgeInstrumentAssumptions(
                instrument_id=args.hedge_instrument_id,
                venue=args.hedge_venue,
                available_liquidity_y_atomic=(
                    args.hedge_available_liquidity_y_atomic
                ),
                max_liquidity_share_bps=(
                    args.hedge_max_liquidity_share_bps
                ),
                max_leverage=args.hedge_max_leverage,
                funding_bps_per_holding_window=(
                    args.hedge_funding_bps_per_window
                ),
            ),
            criteria=StaticHedgeCriteria(
                observation_limit=args.observation_limit,
                holding_observations=args.holding_observations,
                hedge_fraction=args.hedge_fraction,
                hedge_round_trip_cost_bps=(
                    args.hedge_round_trip_cost_bps
                ),
                min_windows=args.min_windows,
                min_mean_abs_return_reduction_bps=(
                    args.min_mean_abs_return_reduction_bps
                ),
                min_worst_loss_improvement_bps=(
                    args.min_worst_loss_improvement_bps
                ),
                max_mean_return_drag_bps=(
                    args.max_mean_return_drag_bps
                ),
            ),
            as_of=args.as_of,
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_static_hedge_research(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_qualified and not result.research_qualified:
            raise SystemExit(2)
        return

    if args.command == "mint-snapshot-ingest":
        settings = Settings.from_env()
        with open(args.file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        result = ingest_mint_snapshot(
            Storage(settings.database_path),
            payload,
            observed_at=args.observed_at,
        )
        print(json.dumps(result.__dict__, indent=2))
        return

    if args.command == "mint-risk-research":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = research_pool_mint_risk(
            storage,
            pool_address=args.pool,
            criteria=MintRiskCriteria(
                max_snapshot_age_seconds=(
                    args.max_snapshot_age_seconds
                ),
                max_decimals=args.max_decimals,
                require_initialized=True,
                require_mint_authority_revoked=(
                    not args.allow_active_mint_authority
                ),
                require_freeze_authority_revoked=(
                    not args.allow_active_freeze_authority
                ),
                allow_token_2022=(
                    not args.disallow_token_2022
                ),
                allow_token_2022_extension_data=(
                    args.allow_token_2022_extension_data
                ),
                include_reward_mints=(
                    not args.exclude_reward_mints
                ),
            ),
            as_of=args.as_of,
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_pool_mint_risk(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_qualified and not result.research_qualified:
            raise SystemExit(2)
        return

    if args.command == "wallet-flow-research":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = research_wallet_flow(
            storage,
            pool_address=args.pool,
            criteria=WalletFlowCriteria(
                lookback_events=args.lookback_events,
                min_events=args.min_events,
                min_unique_users=args.min_unique_users,
                max_top_user_share_bps=(
                    args.max_top_user_share_bps
                ),
            ),
            as_of=args.as_of,
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_wallet_flow_research(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_qualified and not result.research_qualified:
            raise SystemExit(2)
        return

    if args.command == "multi-pool-research":
        settings = Settings.from_env()
        with open(args.file, "r", encoding="utf-8") as handle:
            raw_inputs = json.load(handle)
        if not isinstance(raw_inputs, list):
            raise ValueError(
                "multi-pool-research file must contain a JSON array"
            )
        inputs = tuple(
            PoolResearchInput(
                pool_address=str(item["pool_address"]),
                amount_x=int(item["amount_x"]),
                amount_y=int(item["amount_y"]),
                requested_quote=float(item["requested_quote"]),
                network_cost_y_atomic=int(
                    item["network_cost_y_atomic"]
                ),
            )
            for item in raw_inputs
        )
        result = build_multi_pool_research(
            str(settings.database_path),
            inputs=inputs,
            account_equity_quote=args.equity,
            cash_quote=args.cash,
            current_deployed_quote=args.deployed,
            portfolio_drawdown_bps=args.drawdown_bps,
            phase2_gate=None,
            observation_limit=args.observations,
            half_widths=args.half_widths,
            center_offsets=args.center_offsets,
            strategies=args.strategies,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        output = {
            "comparison": result.to_record(),
            "candidate_evidence_id": None,
            "candidate_evidence_sha256": None,
        }
        if args.persist_phase9_candidates:
            evidence_id, digest = persist_portfolio_candidate_research(
                Storage(settings.database_path),
                comparison=result.comparison,
                source_inputs=raw_inputs,
                assumptions={
                    "account_equity_quote": args.equity,
                    "cash_quote": args.cash,
                    "current_deployed_quote": args.deployed,
                    "portfolio_drawdown_bps": args.drawdown_bps,
                    "observation_limit": args.observations,
                    "half_widths": list(args.half_widths),
                    "center_offsets": list(args.center_offsets),
                    "strategies": [
                        item.value for item in args.strategies
                    ],
                    "max_share_bps": args.max_share_bps,
                    "favor_x_in_active_bin": args.favor_x_active,
                },
            )
            output["candidate_evidence_id"] = evidence_id
            output["candidate_evidence_sha256"] = digest
        print(json.dumps(output, indent=2))
        return

    if args.command == "portfolio-allocation-research":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        with open(args.file, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        raw_candidates = _portfolio_candidate_records(raw)
        candidate_lineage = None
        if isinstance(raw, dict):
            evidence_id = raw.get("candidate_evidence_id")
            digest = raw.get("candidate_evidence_sha256")
            if evidence_id is not None or digest is not None:
                if evidence_id is None or not str(digest or "").strip():
                    raise ValueError(
                        "portfolio candidate lineage requires both evidence ID and SHA-256"
                    )
                candidate_lineage = {
                    "candidate_evidence_id": int(evidence_id),
                    "candidate_evidence_sha256": str(digest),
                }
        candidates = tuple(
            CrossPoolResearchCandidate(
                rank=int(item["rank"]),
                pool_address=str(item["pool_address"]),
                strategy=str(item["strategy"]),
                min_bin_id=int(item["min_bin_id"]),
                max_bin_id=int(item["max_bin_id"]),
                half_width=int(item["half_width"]),
                center_offset=int(item["center_offset"]),
                net_return_bps=int(item["net_return_bps"]),
                hold_return_bps=int(item["hold_return_bps"]),
                excess_vs_hold_initial_bps=int(
                    item["excess_vs_hold_initial_bps"]
                ),
                range_survival_ratio=float(
                    item["range_survival_ratio"]
                ),
                max_observed_share_bps=int(
                    item["max_observed_share_bps"]
                ),
                sized_quote=float(item["sized_quote"]),
                phase2_ready=bool(item["phase2_ready"]),
                policy_authorized=bool(
                    item["policy_authorized"]
                ),
            )
            for item in raw_candidates
        )
        comparison = CrossPoolResearchReport(
            plans_seen=len(candidates),
            comparable_plans=len(candidates),
            excluded_plans=0,
            leader_pool_address=(
                candidates[0].pool_address
                if candidates
                else None
            ),
            ranking_rule="external JSON research candidates",
            candidates=candidates,
        )
        result = research_portfolio_allocation(
            storage,
            comparison=comparison,
            budget_quote=args.budget_quote,
            candidate_lineage=candidate_lineage,
            criteria=PortfolioAllocationCriteria(
                max_positions=args.max_positions,
                min_positions=args.min_positions,
                max_pool_allocation_bps=(
                    args.max_pool_allocation_bps
                ),
                min_range_survival_ratio=(
                    args.min_range_survival_ratio
                ),
                min_excess_vs_hold_bps=(
                    args.min_excess_vs_hold_bps
                ),
                min_position_quote=args.min_position_quote,
                min_budget_utilization_rate=(
                    args.min_budget_utilization_rate
                ),
            ),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_portfolio_allocation_research(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_qualified and not result.research_qualified:
            raise SystemExit(2)
        return

    if args.command == "phase8-promotion-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        audit = audit_persisted_phase8_promotion(storage)
        print(json.dumps(audit.to_record(), indent=2))
        if args.require_current and not audit.current:
            raise SystemExit(2)
        return

    if args.command == "phase9-research-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        pools = tuple(
            item.strip()
            for item in args.pools.split(",")
            if item.strip()
        )
        result = evaluate_phase9_research(
            storage,
            pool_addresses=pools,
            criteria=Phase9ResearchCriteria(
                min_pools=args.min_pools,
                min_qualified_pools=args.min_qualified_pools,
                min_qualified_pool_rate=args.min_qualified_pool_rate,
                min_mean_survival_uplift_vs_fixed=(
                    args.min_mean_survival_uplift_vs_fixed
                ),
                max_mean_width_multiple_vs_fixed=(
                    args.max_mean_width_multiple_vs_fixed
                ),
            ),
            adaptive_criteria=AdaptiveRangeCriteria(
                lookback_observations=args.lookback_observations,
                holding_observations=args.holding_observations,
                target_coverage=args.target_coverage,
                min_half_width_bins=args.min_half_width_bins,
                max_half_width_bins=args.max_half_width_bins,
                min_historical_windows=args.min_historical_windows,
            ),
            adaptive_validation_criteria=AdaptiveRangeValidationCriteria(
                fixed_half_width_bins=args.fixed_half_width_bins,
                min_decisions=args.min_decisions,
                min_adaptive_survival_rate=(
                    args.min_adaptive_survival_rate
                ),
                min_survival_uplift_vs_fixed=(
                    args.min_survival_uplift_vs_fixed
                ),
                max_mean_width_multiple_vs_fixed=(
                    args.max_pool_width_multiple_vs_fixed
                ),
                max_cap_exceeded_rate=args.max_cap_exceeded_rate,
            ),
            regime_criteria=DLMMRegimeCriteria(
                lookback_observations=(
                    args.regime_lookback_observations
                ),
                recent_observations=args.regime_recent_observations,
                min_observations=args.regime_min_observations,
                trend_efficiency_threshold=(
                    args.trend_efficiency_threshold
                ),
                activity_percentile=args.activity_percentile,
                quiet_percentile=args.quiet_percentile,
            ),
            as_of=args.as_of,
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_phase9_research(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_qualified and not result.research_qualified:
            raise SystemExit(2)
        return

    if args.command == "phase9-research-bundle":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_research_bundle(
            storage,
            criteria=Phase9ResearchBundleCriteria(
                min_mint_risk_pools=args.min_mint_risk_pools,
                min_wallet_flow_pools=args.min_wallet_flow_pools,
                min_static_hedge_pools=args.min_static_hedge_pools,
            ),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_phase9_research_bundle(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.research_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-storage-integrity":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_storage_integrity(storage)
        print(json.dumps(result.to_record(), indent=2))
        if args.require_verified and not result.verified:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-authorization-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = audit_persisted_phase9_policy_authorization(
            storage,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_current and not result.current:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-authorization-gate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_policy_authorization(
            storage,
            criteria=Phase9PolicyAuthorizationCriteria(
                min_shadow_runs=args.min_shadow_runs,
                min_distinct_dataset_hashes=(
                    args.min_distinct_dataset_hashes
                ),
                min_distinct_cutoffs=args.min_distinct_cutoffs,
                min_decisions_per_run=args.min_decisions_per_run,
                min_pools_per_run=args.min_pools_per_run,
                min_selected_arms_per_run=(
                    args.min_selected_arms_per_run
                ),
                min_total_decisions=args.min_total_decisions,
                min_mean_uplift_vs_baseline_bps=(
                    args.min_mean_uplift_vs_baseline_bps
                ),
                max_mean_regret_vs_oracle_bps=(
                    args.max_mean_regret_vs_oracle_bps
                ),
            ),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_phase9_policy_authorization(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.authorization_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-controlled-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_policy_controlled_validation(
            storage,
            cycle_id=args.cycle_id,
            criteria=Phase9PolicyControlledValidationCriteria(
                warmup_decisions_per_context=(
                    args.warmup_decisions_per_context
                ),
                exploration_bonus_bps=args.exploration_bonus_bps,
                min_decisions=args.min_decisions,
                min_pools=args.min_pools,
                min_selected_arms=args.min_selected_arms,
                min_mean_uplift_vs_baseline_bps=(
                    args.min_mean_uplift_vs_baseline_bps
                ),
                max_mean_regret_vs_oracle_bps=(
                    args.max_mean_regret_vs_oracle_bps
                ),
                min_post_authorization_seconds=(
                    args.min_post_authorization_seconds
                ),
            ),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_phase9_policy_controlled_validation(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if (
            args.require_ready
            and not result.controlled_validation_ready
        ):
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-controlled-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = audit_persisted_phase9_policy_controlled_validation(
            storage,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_current and not result.current:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-readiness-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_policy_readiness(storage)
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-rollout-simulate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        with open(args.file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(
                "phase9-policy-rollout-simulate file must contain an object"
            )
        current_raw = payload.get("current")
        proposed_raw = payload.get("proposed")
        if not isinstance(current_raw, dict) or not isinstance(
            proposed_raw,
            dict,
        ):
            raise ValueError(
                "rollout simulation file requires current and proposed objects"
            )

        def _rollout_envelope(raw):
            pools = raw.get("allowed_pool_addresses")
            if not isinstance(pools, list):
                raise ValueError(
                    "allowed_pool_addresses must be a JSON array"
                )
            return Phase9PolicyRolloutEnvelope(
                allowed_pool_addresses=tuple(str(item) for item in pools),
                **{
                    key: value
                    for key, value in raw.items()
                    if key != "allowed_pool_addresses"
                },
            )

        result = evaluate_phase9_policy_rollout_simulation(
            storage,
            current=_rollout_envelope(current_raw),
            proposed=_rollout_envelope(proposed_raw),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_phase9_policy_rollout_simulation(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.rollout_simulation_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-rollout-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = audit_persisted_phase9_policy_rollout_simulation(
            storage,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_current and not result.current:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-rollback-simulate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        with open(args.file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(
                "phase9-policy-rollback-simulate file must contain an object"
            )
        metrics_raw = payload.get("metrics")
        criteria_raw = payload.get("criteria")
        if not isinstance(metrics_raw, dict) or not isinstance(
            criteria_raw,
            dict,
        ):
            raise ValueError(
                "rollback simulation file requires metrics and criteria objects"
            )
        result = evaluate_phase9_policy_rollback_simulation(
            storage,
            metrics=Phase9PolicyRollbackMetrics(**metrics_raw),
            criteria=Phase9PolicyRollbackCriteria(**criteria_raw),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_phase9_policy_rollback_simulation(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        return

    if args.command == "phase9-policy-rollback-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = audit_persisted_phase9_policy_rollback_simulation(
            storage,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_current and not result.current:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-prewire-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_policy_prewire_audit(storage)
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-manifest":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_policy_prewire_manifest(storage)
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_phase9_policy_prewire_manifest(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.manifest_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-policy-manifest-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = audit_persisted_phase9_policy_prewire_manifest(
            storage,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_current and not result.current:
            raise SystemExit(2)
        return

    if args.command == "phase9-shadow-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_shadow(
            storage,
            cycle_id=args.cycle_id,
            criteria=Phase9ShadowCriteria(
                warmup_decisions_per_context=(
                    args.warmup_decisions_per_context
                ),
                exploration_bonus_bps=args.exploration_bonus_bps,
                min_decisions=args.min_decisions,
                min_pools=args.min_pools,
                min_selected_arms=args.min_selected_arms,
                min_mean_uplift_vs_baseline_bps=(
                    args.min_mean_uplift_vs_baseline_bps
                ),
                max_mean_regret_vs_oracle_bps=(
                    args.max_mean_regret_vs_oracle_bps
                ),
                min_post_promotion_seconds=(
                    args.min_post_promotion_seconds
                ),
            ),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_phase9_shadow(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.shadow_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-progress":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_progress(storage)
        print(json.dumps(result.to_record(), indent=2))
        if args.require_snapshot and result.snapshot_count == 0:
            raise SystemExit(2)
        if args.require_integrity and not result.integrity_verified:
            raise SystemExit(2)
        return

    if args.command == "phase9-operational-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_operational_audit(
            storage,
            criteria=Phase9ResearchBundleCriteria(
                min_mint_risk_pools=args.min_mint_risk_pools,
                min_wallet_flow_pools=args.min_wallet_flow_pools,
                min_static_hedge_pools=args.min_static_hedge_pools,
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_verified and not result.verified:
            raise SystemExit(2)
        return

    if args.command == "phase9-replay-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_replay_audit(
            storage,
            criteria=Phase9ResearchBundleCriteria(
                min_mint_risk_pools=args.min_mint_risk_pools,
                min_wallet_flow_pools=args.min_wallet_flow_pools,
                min_static_hedge_pools=args.min_static_hedge_pools,
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_verified and not result.verified:
            raise SystemExit(2)
        return

    if args.command == "phase9-promotion-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        criteria = Phase9ResearchBundleCriteria(
            min_mint_risk_pools=args.min_mint_risk_pools,
            min_wallet_flow_pools=args.min_wallet_flow_pools,
            min_static_hedge_pools=args.min_static_hedge_pools,
        )
        current_report = evaluate_phase9_promotion(
            storage,
            criteria=criteria,
        )
        audit = audit_persisted_phase9_promotion(
            storage,
            criteria=criteria,
            current_report=current_report,
        )
        output = {
            **audit.to_record(),
            "research_only": True,
            "policy_actionable": False,
            "current_research_bundle_sha256": (
                current_report.research_bundle_sha256
            ),
            "persisted_research_bundle_sha256": (
                current_report.persisted_bundle_sha256
            ),
        }
        print(json.dumps(output, indent=2))
        if args.require_current and not audit.current:
            raise SystemExit(2)
        return

    if args.command == "phase9-chain-capture-plan":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = build_phase9_chain_capture_plan(
            storage,
            criteria=Phase9ChainCaptureCriteria(
                target_chain_pools=args.target_chain_pools,
                max_candidates=args.max_candidates,
                bin_array_radius=args.bin_array_radius,
                max_api_snapshot_age_seconds=(
                    args.max_api_snapshot_age_seconds
                ),
            ),
            rpc_url=args.rpc_url,
            executor_bin=args.executor_bin,
            as_of=args.as_of or utc_now_iso(),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.plan_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-mint-capture-plan":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        mint_pools = (
            tuple(
                item.strip()
                for item in args.pools.split(",")
                if item.strip()
            )
            if args.pools
            else None
        )
        result = build_phase9_mint_capture_plan(
            storage,
            criteria=Phase9MintCaptureCriteria(
                target_pools=args.target_pools,
                max_snapshot_age_seconds=(
                    args.max_snapshot_age_seconds
                ),
                include_reward_mints=(
                    not args.exclude_reward_mints
                ),
            ),
            pool_addresses=mint_pools,
            as_of=args.as_of,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.inputs_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-mint-capture-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        mint_pools = (
            tuple(
                item.strip()
                for item in args.pools.split(",")
                if item.strip()
            )
            if args.pools
            else None
        )
        result = run_phase9_mint_capture(
            storage,
            criteria=Phase9MintCaptureCriteria(
                target_pools=args.target_pools,
                max_snapshot_age_seconds=(
                    args.max_snapshot_age_seconds
                ),
                include_reward_mints=(
                    not args.exclude_reward_mints
                ),
            ),
            pool_addresses=mint_pools,
            rust_manifest_path=args.rust_manifest_path,
            rust_binary_path=args.rust_binary_path,
            timeout_seconds=args.timeout_seconds,
            observed_at=args.observed_at,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.inputs_ready_after:
            raise SystemExit(2)
        return

    if args.command == "phase9-position-discovery":
        result = discover_pool_positions_with_rust(
            args.pool,
            limit=args.limit,
            rust_manifest_path=args.rust_manifest_path,
            rust_binary_path=args.rust_binary_path,
            timeout_seconds=args.timeout_seconds,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "phase9-pool-activity-discovery":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        before = args.before_signature
        state_before = phase9_pool_activity_scan_state(
            storage,
            pool_address=args.pool,
        )
        if args.advance_backfill:
            if args.before_signature is not None:
                raise ValueError(
                    "--before-signature cannot be combined with "
                    "--advance-backfill"
                )
            if state_before.backfill_exhausted:
                print(json.dumps({
                    "status": "BACKFILL_EXHAUSTED",
                    "research_only": True,
                    "read_only_capture": True,
                    "policy_actionable": False,
                    "execution_wired": False,
                    "scan_state": state_before.to_record(),
                }, indent=2))
                return
            before = (
                state_before.backfill_before_signature
                if state_before.pages_scanned > 0
                else None
            )

        result = discover_historical_pool_activity_with_rust(
            args.pool,
            limit=args.limit,
            before_signature=before,
            rust_manifest_path=args.rust_manifest_path,
            rust_binary_path=args.rust_binary_path,
            timeout_seconds=args.timeout_seconds,
        )
        state_after = state_before
        if args.advance_backfill:
            state_after = record_phase9_pool_activity_page(
                storage,
                pool_address=args.pool,
                next_before_signature=result.next_before_signature,
                has_more=result.has_more,
                signatures_scanned=result.signatures_scanned,
                matching_transactions=result.matching_transactions,
                positions_discovered=result.positions_found,
            )
        print(json.dumps({
            "research_only": True,
            "read_only_capture": True,
            "policy_actionable": False,
            "execution_wired": False,
            "discovery": result.to_record(),
            "scan_state_before": state_before.to_record(),
            "scan_state_after": state_after.to_record(),
        }, indent=2))
        return

    if args.command == "phase9-wallet-flow-capture-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = run_phase9_wallet_flow_capture(
            storage,
            pool_address=args.pool,
            criteria=WalletFlowCriteria(
                lookback_events=args.lookback_events,
                min_events=args.min_events,
                min_unique_users=args.min_unique_users,
                max_top_user_share_bps=(
                    args.max_top_user_share_bps
                ),
            ),
            discovery_limit=args.discovery_limit,
            max_positions_per_run=args.max_positions_per_run,
            enable_historical_activity=(
                not args.skip_historical_pool_activity
            ),
            historical_signature_limit=(
                args.historical_signature_limit
            ),
            as_of=args.as_of,
            settings=settings,
            expand_closed_positions=(
                not args.skip_owner_position_expansion
            ),
            owner_expansion_limit=args.owner_expansion_limit,
            owner_position_max_pages=args.owner_position_max_pages,
            rust_manifest_path=args.rust_manifest_path,
            rust_binary_path=args.rust_binary_path,
            timeout_seconds=args.timeout_seconds,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.source_after.ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-source-freshness":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_source_freshness(
            storage,
            as_of=args.as_of or utc_now_iso(),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_current and not result.current:
            raise SystemExit(2)
        return

    if args.command == "phase9-evidence-status":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_evidence_status(
            storage,
            criteria=Phase9ResearchBundleCriteria(
                min_mint_risk_pools=args.min_mint_risk_pools,
                min_wallet_flow_pools=args.min_wallet_flow_pools,
                min_static_hedge_pools=args.min_static_hedge_pools,
            ),
            as_of=args.as_of or utc_now_iso(),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_source_ready and not (
            result.chain_history_ready
            and result.mint_ready_pools >= result.mint_required_pools
            and result.wallet_ready_pools >= result.wallet_required_pools
            and result.explicit_inputs_valid
            and result.research_sources_current
        ):
            raise SystemExit(2)
        if args.require_bundle_ready and not result.research_bundle_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-evidence-plan":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = build_phase9_evidence_plan(
            storage,
            criteria=Phase9ResearchBundleCriteria(
                min_mint_risk_pools=args.min_mint_risk_pools,
                min_wallet_flow_pools=args.min_wallet_flow_pools,
                min_static_hedge_pools=args.min_static_hedge_pools,
            ),
            history_interval_seconds=args.history_interval_seconds,
            as_of=args.as_of or utc_now_iso(),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_bundle_ready and not result.research_bundle_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-evidence-step-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        lease = acquire_phase9_operation_lease(
            storage,
            operation_key="phase9-research-maintenance",
            lease_seconds=args.lease_seconds,
        )
        if not lease.acquired:
            print(json.dumps({
                "status": "BUSY",
                "research_only": True,
                "policy_actionable": False,
                "execution_wired": False,
                "lease": lease.to_record(),
            }, indent=2))
            if args.require_progress or args.require_bundle_ready:
                raise SystemExit(2)
            return
        try:
            result = run_phase9_evidence_step(
                storage,
                settings=settings,
                criteria=Phase9ResearchBundleCriteria(
                    min_mint_risk_pools=args.min_mint_risk_pools,
                    min_wallet_flow_pools=args.min_wallet_flow_pools,
                    min_static_hedge_pools=args.min_static_hedge_pools,
                ),
                history_interval_seconds=args.history_interval_seconds,
            )
            print(json.dumps({
                "lease": lease.to_record(),
                "report": result.to_record(),
            }, indent=2))
            if args.require_progress and not (
                result.progressed
                or result.status == "READY"
            ):
                raise SystemExit(2)
            if (
                args.require_bundle_ready
                and not result.plan_after.research_bundle_ready
            ):
                raise SystemExit(2)
        finally:
            release_phase9_operation_lease(
                storage,
                operation_key="phase9-research-maintenance",
                owner_id=lease.owner_id,
            )
        return

    if args.command == "phase9-maintenance-status":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = phase9_operation_lease_status(
            storage,
            operation_key="phase9-research-maintenance",
            as_of=args.as_of,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "phase9-source-capture-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        lease = acquire_phase9_operation_lease(
            storage,
            operation_key="phase9-research-maintenance",
            lease_seconds=args.lease_seconds,
        )
        if not lease.acquired:
            print(json.dumps({
                "status": "BUSY",
                "research_only": True,
                "read_only_capture": True,
                "policy_actionable": False,
                "execution_wired": False,
                "lease": lease.to_record(),
            }, indent=2))
            return
        try:
            result = run_phase9_source_capture(
                storage,
                settings=settings,
                refresh_api=not args.skip_api_refresh,
                chain_pool_target=args.chain_pool_target,
                chain_max_candidates=args.chain_max_candidates,
                api_ranking_max_age_seconds=(
                    args.api_ranking_max_age_seconds
                ),
                bin_array_radius=args.bin_array_radius,
                mint_max_snapshot_age_seconds=(
                    args.mint_max_snapshot_age_seconds
                ),
                history_min_observation_interval_seconds=(
                    args.history_min_observation_interval_seconds
                ),
                wallet_discovery_limit=args.wallet_discovery_limit,
                wallet_max_positions_per_run=(
                    args.wallet_max_positions_per_run
                ),
                wallet_enable_historical_activity=(
                    not args.skip_wallet_historical_pool_activity
                ),
                wallet_historical_signature_limit=(
                    args.wallet_historical_signature_limit
                ),
                wallet_expand_closed_positions=(
                    not args.skip_wallet_owner_position_expansion
                ),
                wallet_owner_expansion_limit=(
                    args.wallet_owner_expansion_limit
                ),
                wallet_owner_position_max_pages=(
                    args.wallet_owner_position_max_pages
                ),
                rust_manifest_path=args.rust_manifest_path,
                rust_binary_path=args.rust_binary_path,
                timeout_seconds=args.timeout_seconds,
            )
            print(json.dumps({
                "status": "COMPLETE",
                "lease": lease.to_record(),
                "report": result.to_record(),
            }, indent=2))
            if (
                args.require_automatic_ready
                and not result.automatic_source_ready
            ):
                raise SystemExit(2)
        finally:
            release_phase9_operation_lease(
                storage,
                operation_key="phase9-research-maintenance",
                owner_id=lease.owner_id,
            )
        return

    if args.command == "phase9-research-refresh-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        lease = acquire_phase9_operation_lease(
            storage,
            operation_key="phase9-research-maintenance",
            lease_seconds=args.lease_seconds,
        )
        if not lease.acquired:
            print(json.dumps({
                "status": "BUSY",
                "research_only": True,
                "policy_actionable": False,
                "execution_wired": False,
                "lease": lease.to_record(),
            }, indent=2))
            return
        try:
            result = run_phase9_research_refresh(
                storage,
                criteria=Phase9ResearchBundleCriteria(
                    min_mint_risk_pools=args.min_mint_risk_pools,
                    min_wallet_flow_pools=args.min_wallet_flow_pools,
                    min_static_hedge_pools=args.min_static_hedge_pools,
                ),
                persist_bundle_when_ready=not args.no_persist_bundle,
            )
            print(json.dumps({
                "status": "COMPLETE",
                "lease": lease.to_record(),
                "report": result.to_record(),
            }, indent=2))
            if (
                args.require_automatic_ready
                and not result.automatic_families_ready
            ):
                raise SystemExit(2)
            if (
                args.require_bundle_ready
                and not result.bundle_ready_after
            ):
                raise SystemExit(2)
        finally:
            release_phase9_operation_lease(
                storage,
                operation_key="phase9-research-maintenance",
                owner_id=lease.owner_id,
            )
        return

    if args.command == "phase9-research-input-template":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        template_pools = (
            tuple(
                item.strip()
                for item in args.pools.split(",")
                if item.strip()
            )
            if args.pools
            else None
        )
        result = build_phase9_explicit_input_template(
            storage,
            pool_addresses=template_pools,
        )
        print(json.dumps(result, indent=2))
        return

    if args.command == "phase9-research-inputs-ingest":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        with open(args.file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        inputs = parse_phase9_explicit_inputs(payload)
        result = persist_phase9_explicit_inputs(
            storage,
            inputs=inputs,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "phase9-research-inputs-audit":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = audit_phase9_explicit_inputs(storage)
        print(json.dumps(result.to_record(), indent=2))
        if args.require_valid and not result.valid:
            raise SystemExit(2)
        return

    if args.command == "phase9-explicit-research-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        artifact = load_phase9_explicit_inputs(
            storage,
            evidence_id=args.input_evidence_id,
        )
        if artifact is None:
            raise ValueError(
                "no persisted Phase 9 explicit research input artifact exists"
            )
        result = run_phase9_explicit_research(
            storage,
            artifact=artifact,
            persist=args.persist,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.explicit_research_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-bandit-research-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        if args.input_evidence_id is None:
            audit = audit_phase9_explicit_inputs(storage)
            if not audit.valid or audit.evidence_id is None:
                raise ValueError(
                    "a valid persisted Phase 9 explicit input artifact is required: "
                    + "; ".join(audit.reasons)
                )
            input_evidence_id = audit.evidence_id
        else:
            input_evidence_id = args.input_evidence_id
        explicit_artifact = load_phase9_explicit_inputs(
            storage,
            evidence_id=input_evidence_id,
        )
        if explicit_artifact is None:
            raise ValueError(
                "no persisted Phase 9 explicit research input artifact exists"
            )
        dataset_payload, dataset_bytes = build_phase9_bandit_dataset(
            storage,
            artifact=explicit_artifact,
            output_directory=args.output_directory,
            cutoff=args.cutoff,
        )
        dataset_artifact = persist_phase9_bandit_dataset(
            storage,
            payload=dataset_payload,
            raw_dataset=dataset_bytes,
        )
        result = evaluate_phase9_contextual_bandit_from_dataset(
            storage,
            dataset_evidence_id=dataset_artifact.evidence_id,
        )
        bandit_evidence_id = (
            persist_phase9_contextual_bandit_from_dataset(
                storage,
                result=result,
            )
            if args.persist
            else None
        )
        output = {
            "research_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "dataset": dataset_artifact.to_record(),
            "bandit_evidence_id": bandit_evidence_id,
            "report": result.report.to_record(),
        }
        print(json.dumps(output, indent=2))
        if (
            args.require_qualified
            and not result.report.research_qualified
        ):
            raise SystemExit(2)
        return

    if args.command == "phase9-chain-history-plan":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = build_phase9_history_plan(
            storage,
            research_criteria=Phase9ResearchCriteria(
                min_pools=args.min_pools,
                min_qualified_pools=args.min_qualified_pools,
                min_qualified_pool_rate=args.min_qualified_pool_rate,
            ),
            adaptive_criteria=AdaptiveRangeCriteria(
                lookback_observations=args.lookback_observations,
                holding_observations=args.holding_observations,
                min_historical_windows=args.min_historical_windows,
            ),
            validation_criteria=AdaptiveRangeValidationCriteria(
                min_decisions=args.min_decisions,
            ),
            regime_criteria=DLMMRegimeCriteria(
                lookback_observations=(
                    args.regime_lookback_observations
                ),
                recent_observations=args.regime_recent_observations,
                min_observations=args.regime_min_observations,
            ),
            executor_bin=args.executor_bin,
            rpc_url=args.rpc_url,
            bin_array_radius=args.bin_array_radius,
            as_of=args.as_of,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.plan_ready:
            raise SystemExit(2)
        return

    if args.command == "phase9-chain-history-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = run_phase9_history_capture(
            storage,
            research_criteria=Phase9ResearchCriteria(
                min_pools=args.min_pools,
                min_qualified_pools=args.min_qualified_pools,
                min_qualified_pool_rate=args.min_qualified_pool_rate,
            ),
            adaptive_criteria=AdaptiveRangeCriteria(
                lookback_observations=args.lookback_observations,
                holding_observations=args.holding_observations,
                min_historical_windows=args.min_historical_windows,
            ),
            validation_criteria=AdaptiveRangeValidationCriteria(
                min_decisions=args.min_decisions,
            ),
            regime_criteria=DLMMRegimeCriteria(
                lookback_observations=(
                    args.regime_lookback_observations
                ),
                recent_observations=args.regime_recent_observations,
                min_observations=args.regime_min_observations,
            ),
            bin_array_radius=args.bin_array_radius,
            rust_manifest_path=args.rust_manifest_path,
            rust_binary_path=args.rust_binary_path,
            timeout_seconds=args.timeout_seconds,
            ingest_observed_at=args.observed_at,
            min_observation_interval_seconds=(
                args.min_observation_interval_seconds
            ),
            continue_sampling_when_ready=(
                args.continue_sampling_when_ready
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.history_ready_after:
            raise SystemExit(2)
        return

    if args.command == "phase9-chain-capture-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = run_phase9_chain_capture_batch(
            storage,
            criteria=Phase9ChainCaptureCriteria(
                target_chain_pools=args.target_chain_pools,
                max_candidates=args.max_candidates,
                bin_array_radius=args.bin_array_radius,
                max_api_snapshot_age_seconds=(
                    args.max_api_snapshot_age_seconds
                ),
            ),
            rust_manifest_path=args.rust_manifest_path,
            rust_binary_path=args.rust_binary_path,
            timeout_seconds=args.timeout_seconds,
            ingest_observed_at=args.observed_at,
            api_ranking_as_of=(
                args.api_ranking_as_of
                or args.observed_at
                or utc_now_iso()
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_target and not result.target_met:
            raise SystemExit(2)
        return

    if args.command == "phase9-work-queue":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        criteria = Phase9ResearchBundleCriteria(
            min_mint_risk_pools=args.min_mint_risk_pools,
            min_wallet_flow_pools=args.min_wallet_flow_pools,
            min_static_hedge_pools=args.min_static_hedge_pools,
        )
        result = build_phase9_work_queue(
            storage,
            criteria=criteria,
            rpc_url=args.rpc_url,
            as_of=args.as_of,
        )
        output = result.to_record()
        output["persisted_snapshot"] = None
        if args.persist_snapshot:
            output["persisted_snapshot"] = (
                persist_phase9_work_queue_snapshot(
                    storage,
                    queue=result,
                    criteria=criteria,
                ).to_record()
            )
        print(json.dumps(output, indent=2))
        return

    if args.command == "phase9-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_phase9_promotion(
            storage,
            criteria=Phase9ResearchBundleCriteria(
                min_mint_risk_pools=args.min_mint_risk_pools,
                min_wallet_flow_pools=args.min_wallet_flow_pools,
                min_static_hedge_pools=args.min_static_hedge_pools,
            ),
        )
        output = result.to_record()
        if args.persist_ready and result.promotion_ready:
            output["persisted"] = persist_phase9_promotion(
                storage,
                report=result,
            ).__dict__
        else:
            output["persisted"] = None
        print(json.dumps(output, indent=2))
        if args.require_ready and not result.promotion_ready:
            raise SystemExit(2)
        return

    if args.command == "contextual-bandit-cycle-research":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_cycle_contextual_bandit(
            storage,
            cycle_id=args.cycle_id,
            criteria=ContextualBanditCriteria(
                warmup_decisions_per_context=(
                    args.warmup_decisions_per_context
                ),
                exploration_bonus_bps=args.exploration_bonus_bps,
                min_decisions=args.min_decisions,
                min_pools=args.min_pools,
                min_selected_arms=args.min_selected_arms,
                min_mean_uplift_vs_baseline_bps=(
                    args.min_mean_uplift_vs_baseline_bps
                ),
                max_mean_regret_vs_oracle_bps=(
                    args.max_mean_regret_vs_oracle_bps
                ),
            ),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_cycle_contextual_bandit(
                    storage,
                    result=result,
                )
            )
        print(json.dumps(output, indent=2))
        if (
            args.require_qualified
            and not result.report.research_qualified
        ):
            raise SystemExit(2)
        return

    if args.command == "contextual-bandit-research":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        frame = pd.read_csv(args.file)
        examples = bandit_examples_from_records(
            frame.to_dict("records")
        )
        result = evaluate_contextual_bandit(
            storage,
            examples=examples,
            criteria=ContextualBanditCriteria(
                warmup_decisions_per_context=(
                    args.warmup_decisions_per_context
                ),
                exploration_bonus_bps=args.exploration_bonus_bps,
                min_decisions=args.min_decisions,
                min_pools=args.min_pools,
                min_selected_arms=args.min_selected_arms,
                min_mean_uplift_vs_baseline_bps=(
                    args.min_mean_uplift_vs_baseline_bps
                ),
                max_mean_regret_vs_oracle_bps=(
                    args.max_mean_regret_vs_oracle_bps
                ),
            ),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_contextual_bandit_research(
                    storage,
                    report=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_qualified and not result.research_qualified:
            raise SystemExit(2)
        return

    if args.command == "ml-train-csv":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        frame = pd.read_csv(args.file)
        result = train_save_register_ml_v1(
            storage,
            frame,
            model_id=args.model_id,
            dataset_version=args.dataset_version,
            artifact_directory=args.artifact_dir,
            split_fraction=args.split_fraction,
            min_rows=args.min_rows,
        )
        print(
            json.dumps(
                {
                    "model": result.registry.__dict__,
                    "artifact_path": str(result.artifact.artifact_path),
                    "metadata_path": str(result.artifact.metadata_path),
                    "artifact_sha256": result.artifact.metadata.artifact_sha256,
                },
                indent=2,
            )
        )
        return

    if args.command == "ml-offline-evaluate-csv":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        frame = pd.read_csv(args.file)
        inference_config = MLInferenceConfig(
            risk_lambda=args.risk_lambda,
            min_positive_excess_probability=args.min_positive_probability,
            min_range_survival_probability=args.min_range_survival,
            min_score_bps=args.min_score_bps,
        )
        criteria = MLChallengerCriteria(
            min_comparable_decisions=args.min_decisions,
            min_choice_coverage_rate=args.min_choice_coverage,
            min_mean_uplift_bps=args.min_uplift_bps,
            min_win_rate=args.min_win_rate,
            min_positive_excess_rate=args.min_positive_rate,
            max_single_decision_loss_bps=args.max_single_loss_bps,
        )
        if args.qualify:
            validation, record = qualify_registered_offline_challenger(
                storage,
                frame,
                model_id=args.model_id,
                inference_config=inference_config,
                criteria=criteria,
            )
            output = validation.to_record()
            output["model_status"] = record.status
        else:
            validation = evaluate_registered_offline_challenger(
                storage,
                frame,
                model_id=args.model_id,
                inference_config=inference_config,
                criteria=criteria,
            )
            output = validation.to_record()
        print(json.dumps(output, indent=2))
        if args.require_qualified and not validation.offline_qualified:
            raise SystemExit(2)
        return

    if args.command == "ml-start-paper":
        settings = Settings.from_env()
        record = start_paper_challenger(
            Storage(settings.database_path),
            model_id=args.model_id,
        )
        print(json.dumps(record.__dict__, indent=2))
        return

    if args.command == "ml-retrain-plan":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = build_continuous_learning_plan(
            storage,
            criteria=ContinuousLearningCriteria(
                min_new_chain_observations=(
                    args.min_new_chain_observations
                ),
                min_new_chain_pools=args.min_new_chain_pools,
                min_new_live_labels=args.min_new_live_labels,
                max_champion_age_days=args.max_champion_age_days,
            ),
            as_of=args.as_of,
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        if args.persist:
            output["persisted_evidence_id"] = (
                persist_continuous_learning_plan(
                    storage,
                    plan=result,
                )
            )
        print(json.dumps(output, indent=2))
        if args.require_due and not result.retrain_due:
            raise SystemExit(2)
        return

    if args.command == "ml-retrain-build":
        settings = Settings.from_env()
        with open(args.file, "r", encoding="utf-8") as handle:
            raw_pools = json.load(handle)
        if not isinstance(raw_pools, list):
            raise ValueError(
                "ml-retrain-build file must contain a JSON array"
            )
        pools = tuple(
            MLRetrainPoolSpec(
                pool_address=str(item["pool_address"]),
                amount_x=int(item["amount_x"]),
                amount_y=int(item["amount_y"]),
                network_cost_y_atomic=int(
                    item["network_cost_y_atomic"]
                ),
            )
            for item in raw_pools
        )
        result = start_retraining_cycle_with_dataset(
            Storage(settings.database_path),
            pools=pools,
            output_file=args.output,
            cycle_id=args.cycle_id,
            as_of=args.as_of,
            criteria=ContinuousLearningCriteria(
                min_new_chain_observations=(
                    args.min_new_chain_observations
                ),
                min_new_chain_pools=args.min_new_chain_pools,
                min_new_live_labels=args.min_new_live_labels,
                max_champion_age_days=args.max_champion_age_days,
            ),
            lookback_observations=args.lookback_observations,
            forward_observations=args.forward_observations,
            step_observations=args.step_observations,
            half_widths=args.half_widths,
            center_offsets=args.center_offsets,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "ml-retrain-train":
        settings = Settings.from_env()
        result = train_retraining_cycle_challenger(
            Storage(settings.database_path),
            cycle_id=args.cycle_id,
            dataset_file=args.file,
            model_id=args.model_id,
            artifact_directory=args.artifact_dir,
            split_fraction=args.split_fraction,
            min_rows=args.min_rows,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "ml-retrain-walk-forward":
        settings = Settings.from_env()
        result = evaluate_retraining_cycle_walk_forward(
            Storage(settings.database_path),
            cycle_id=args.cycle_id,
            dataset_file=args.file,
            criteria=MLWalkForwardCriteria(
                min_train_decision_times=(
                    args.min_train_decision_times
                ),
                validation_decision_times=(
                    args.validation_decision_times
                ),
                step_decision_times=args.step_decision_times,
                min_folds=args.min_folds,
                min_total_comparable_decisions=(
                    args.min_total_comparable_decisions
                ),
                min_qualified_fold_rate=(
                    args.min_qualified_fold_rate
                ),
                min_mean_fold_uplift_bps=(
                    args.min_mean_fold_uplift_bps
                ),
                min_positive_fold_rate=(
                    args.min_positive_fold_rate
                ),
                max_worst_fold_mean_uplift_loss_bps=(
                    args.max_worst_fold_uplift_loss_bps
                ),
                training_split_fraction=(
                    args.training_split_fraction
                ),
                training_min_rows=args.training_min_rows,
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        if (
            args.require_qualified
            and not result.report.walk_forward_qualified
        ):
            raise SystemExit(2)
        return

    if args.command == "ml-retrain-start":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = start_retraining_cycle(
            storage,
            target_dataset_version=args.dataset_version,
            cycle_id=args.cycle_id,
            as_of=args.as_of,
            criteria=ContinuousLearningCriteria(
                min_new_chain_observations=(
                    args.min_new_chain_observations
                ),
                min_new_chain_pools=args.min_new_chain_pools,
                min_new_live_labels=args.min_new_live_labels,
                max_champion_age_days=args.max_champion_age_days,
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "ml-retrain-status":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        if args.cycle_id:
            result = retraining_cycle(
                storage,
                cycle_id=args.cycle_id,
            )
        else:
            result = active_retraining_cycle(storage)
        print(
            json.dumps(
                result.to_record() if result is not None else None,
                indent=2,
            )
        )
        return

    if args.command == "ml-retrain-sync":
        settings = Settings.from_env()
        result = sync_retraining_cycle(
            Storage(settings.database_path),
            cycle_id=args.cycle_id,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "ml-retrain-cancel":
        settings = Settings.from_env()
        result = cancel_retraining_cycle(
            Storage(settings.database_path),
            cycle_id=args.cycle_id,
            notes=args.notes,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "ml-retrain-attach":
        settings = Settings.from_env()
        result = attach_retraining_challenger(
            Storage(settings.database_path),
            cycle_id=args.cycle_id,
            model_id=args.model_id,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "ml-continuous-validate":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_continuous_champion(
            storage,
            cycle_id=args.cycle_id,
            account_id=args.account,
            criteria=ContinuousChampionCriteria(
                min_challenger_closed_trades=(
                    args.min_challenger_closed_trades
                ),
                min_incumbent_closed_trades=(
                    args.min_incumbent_closed_trades
                ),
                min_challenger_return_bps=(
                    args.min_challenger_return_bps
                ),
                min_challenger_win_rate=(
                    args.min_challenger_win_rate
                ),
                max_challenger_drawdown_bps=(
                    args.max_challenger_drawdown_bps
                ),
                max_single_trade_loss_bps=(
                    args.max_single_trade_loss_bps
                ),
                min_return_uplift_vs_incumbent_bps=(
                    args.min_uplift_vs_incumbent_bps
                ),
            ),
        )
        output = result.to_record()
        output["promotion"] = None
        if args.promote:
            output["promotion"] = promote_continuous_challenger(
                storage,
                validation=result,
            )
        print(json.dumps(output, indent=2))
        if args.require_qualified and not result.qualified:
            raise SystemExit(2)
        return

    if args.command == "ml-live-monitor":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_live_champion(
            storage,
            model_id=args.model_id,
            criteria=LiveChampionCriteria(
                min_live_labels=args.min_live_labels,
                min_live_pools=args.min_live_pools,
                max_realized_drawdown_bps=args.max_drawdown_bps,
                max_single_loss_bps=args.max_single_loss_bps,
                min_win_rate=args.min_win_rate,
                min_mean_return_bps=args.min_mean_return_bps,
                max_mean_abs_prediction_error_bps=(
                    args.max_mean_abs_prediction_error_bps
                ),
            ),
        )
        output = result.to_record()
        output["persisted_evidence_id"] = None
        output["rolled_back_model"] = None
        if args.persist or args.rollback:
            output["persisted_evidence_id"] = (
                persist_live_champion_report(
                    storage,
                    report=result,
                )
            )
        if args.rollback:
            if not result.rollback_recommended:
                raise ValueError(
                    "rollback requested but live monitor does not recommend rollback"
                )
            rolled = rollback_live_champion(
                storage,
                model_id=result.model_id,
                notes="live champion safety rollback",
            )
            output["rolled_back_model"] = rolled.__dict__
        print(json.dumps(output, indent=2))
        if args.require_healthy and result.status != "HEALTHY":
            raise SystemExit(2)
        return

    if args.command == "ml-model-status":
        settings = Settings.from_env()
        record = model_record(
            Storage(settings.database_path),
            model_id=args.model_id,
        )
        print(json.dumps(record.__dict__, indent=2))
        return

    if args.command == "paper-scheduler-run":
        settings = Settings.from_env()
        result = run_scheduled_paper_tick(
            Storage(settings.database_path),
            account_id=args.account,
            interval_seconds=args.interval_seconds,
            lease_seconds=args.lease_seconds,
            owner_id=args.owner_id,
            settings=settings,
            chain_max_age_seconds=args.max_chain_age_seconds,
            quote_max_age_seconds=args.max_quote_age_seconds,
            array_radius=args.array_radius,
            max_positions=args.max_positions,
            safety_config=_pool_safety_config_from_args(args),
            management_config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
            retry_failed=args.retry_failed,
            refresh_jupiter_quotes=args.refresh_jupiter_quotes,
        )
        print(json.dumps(result.to_record(), indent=2))
        if result.status in {"FAILED", "MARKET_REFRESH_FAILED"}:
            raise SystemExit(2)
        return

    if args.command == "paper-health":
        settings = Settings.from_env()
        result = build_paper_health(
            Storage(settings.database_path),
            account_id=args.account,
            max_tick_age_seconds=args.max_tick_age_seconds,
            max_chain_age_seconds=args.max_chain_age_seconds,
            max_quote_age_seconds=args.max_quote_age_seconds,
            max_consecutive_failures=args.max_consecutive_failures,
            array_radius=args.array_radius,
        )
        if args.format == "prometheus":
            print(render_paper_health_prometheus(result), end="")
        else:
            print(json.dumps(result.to_record(), indent=2))
        if args.require_healthy and result.status not in {"HEALTHY", "IDLE"}:
            raise SystemExit(2)
        return

    if args.command == "paper-audit":
        settings = Settings.from_env()
        result = audit_paper_ledger(
            Storage(settings.database_path),
            account_id=args.account,
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_passing and not result.passing:
            raise SystemExit(2)
        return

    if args.command == "paper-endurance-report":
        settings = Settings.from_env()
        result = build_paper_endurance_report(
            Storage(settings.database_path),
            account_id=args.account,
            criteria=PaperEnduranceCriteria(
                min_runtime_hours=args.min_runtime_hours,
                min_terminal_ticks=args.min_terminal_ticks,
                min_success_rate_pct=args.min_success_rate_pct,
                max_dependency_blocked_pct=args.max_dependency_blocked_pct,
                max_consecutive_failures=args.max_consecutive_failures,
                max_stale_running_ticks=args.max_stale_running_ticks,
                stale_running_after_seconds=args.stale_running_after_seconds,
                min_applied_chain_valuations=(
                    args.min_applied_chain_valuations
                ),
                min_distinct_positions_valued=(
                    args.min_distinct_positions_valued
                ),
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        if args.require_passing and not result.passing:
            raise SystemExit(2)
        return

    if args.command == "paper-scheduler-status":
        settings = Settings.from_env()
        result = paper_scheduler_state(
            Storage(settings.database_path),
            account_id=args.account,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-tick":
        settings = Settings.from_env()
        result = run_paper_tick(
            Storage(settings.database_path),
            account_id=args.account,
            tick_id=args.tick_id,
            settings=settings,
            chain_max_age_seconds=args.max_chain_age_seconds,
            quote_max_age_seconds=args.max_quote_age_seconds,
            array_radius=args.array_radius,
            max_positions=args.max_positions,
            safety_config=_pool_safety_config_from_args(args),
            management_config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
            retry_failed=args.retry_failed,
            refresh_jupiter_quotes=args.refresh_jupiter_quotes,
        )
        print(json.dumps(result.to_record(), indent=2))
        if result.status == "FAILED":
            raise SystemExit(2)
        return

    if args.command == "paper-supervise":
        settings = Settings.from_env()
        result = run_paper_supervisor(
            Storage(settings.database_path),
            account_id=args.account,
            cycle_id=args.cycle_id,
            chain_max_age_seconds=args.max_chain_age_seconds,
            quote_max_age_seconds=args.max_quote_age_seconds,
            array_radius=args.array_radius,
            max_positions=args.max_positions,
            safety_config=_pool_safety_config_from_args(args),
            management_config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
            retry_failed=args.retry_failed,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-quote-ingest":
        settings = Settings.from_env()
        result = save_token_quote(
            Storage(settings.database_path),
            token_mint=args.mint,
            quote_per_atomic=args.quote_per_atomic,
            source=args.source,
            observed_at=args.observed_at,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-quote-status":
        settings = Settings.from_env()
        result = token_quote_status(
            Storage(settings.database_path),
            token_mint=args.mint,
            max_age_seconds=args.max_age_seconds,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-chain-work-queue":
        settings = Settings.from_env()
        result = build_paper_chain_collection_queue(
            Storage(settings.database_path),
            account_id=args.account,
            max_age_seconds=args.max_age_seconds,
            array_radius=args.array_radius,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-open-phase3":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = build_and_open_bound_phase3_paper_entry(
            storage,
            account_id=args.account,
            position_id=args.position,
            event_key=args.event_key,
            pool_address=args.pool,
            amount_x=args.amount_x,
            amount_y=args.amount_y,
            requested_quote=args.requested_quote,
            network_cost_y_atomic=args.network_cost_y_atomic,
            entry_cost_quote=args.entry_cost,
            safety_config=_pool_safety_config_from_args(args),
            sizing_config=CapitalSizingConfig(
                max_position_bps=args.max_position_bps,
                max_total_deployed_bps=args.max_deployed_bps,
                min_cash_reserve_bps=args.reserve_bps,
                soft_drawdown_bps=args.soft_drawdown_bps,
                hard_drawdown_bps=args.hard_drawdown_bps,
                drawdown_size_multiplier_bps=(
                    args.drawdown_size_multiplier_bps
                ),
                min_position_quote=args.min_position,
            ),
            observation_limit=args.observations,
            half_widths=args.half_widths,
            center_offsets=args.center_offsets,
            strategies=args.strategies,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2))
        if not result.entry.opened:
            raise SystemExit(2)
        return

    if args.command == "paper-create-account":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = create_paper_account(
            storage,
            account_id=args.account,
            starting_cash_quote=args.cash,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-status":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = paper_account_snapshot(storage, account_id=args.account)
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-open":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = open_paper_position(
            storage,
            event_key=args.event_key,
            account_id=args.account,
            position_id=args.position,
            pool_address=args.pool,
            policy_source=args.policy_source,
            model_id=args.model_id,
            strategy=args.strategy,
            min_bin_id=args.min_bin,
            max_bin_id=args.max_bin,
            capital_quote=args.capital,
            entry_cost_quote=args.entry_cost,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-mark":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = mark_paper_position(
            storage,
            event_key=args.event_key,
            position_id=args.position,
            mark_quote=args.mark,
            fee_delta_quote=args.fee_delta,
            reward_delta_quote=args.reward_delta,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-rebalance":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = rebalance_paper_position(
            storage,
            event_key=args.event_key,
            position_id=args.position,
            new_min_bin_id=args.min_bin,
            new_max_bin_id=args.max_bin,
            new_mark_quote=args.mark,
            rebalance_cost_quote=args.cost,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-close":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = close_paper_position(
            storage,
            event_key=args.event_key,
            position_id=args.position,
            final_mark_quote=args.mark,
            exit_cost_quote=args.exit_cost,
            fee_delta_quote=args.fee_delta,
            reward_delta_quote=args.reward_delta,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-manage":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_paper_position_policy(
            storage,
            position_id=args.position,
            active_bin_id=args.active_bin,
            holding_observations=args.holding_observations,
            pool_safe=not args.pool_unsafe,
            estimated_exit_cost_quote=args.estimated_exit_cost,
            emergency_exit=args.emergency_exit,
            config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-observe":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = apply_paper_observation(
            storage,
            event_key_prefix=args.event_key_prefix,
            position_id=args.position,
            active_bin_id=args.active_bin,
            holding_observations=args.holding_observations,
            mark_quote=args.mark,
            fee_delta_quote=args.fee_delta,
            reward_delta_quote=args.reward_delta,
            pool_safe=not args.pool_unsafe,
            emergency_exit=args.emergency_exit,
            estimated_exit_cost_quote=args.estimated_exit_cost,
            rebalance_cost_quote=args.rebalance_cost,
            config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-chain-bind":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = bind_paper_position_to_chain(
            storage,
            position_id=args.position,
            observed_at=args.observed_at,
            amount_x=args.amount_x,
            amount_y=args.amount_y,
            token_y_quote_per_atomic=args.token_y_quote_per_atomic,
            max_notional_error_bps=args.max_notional_error_bps,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-chain-value":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = value_paper_position_from_chain(
            storage,
            position_id=args.position,
            observed_at=args.observed_at,
            token_y_quote_per_atomic=args.token_y_quote_per_atomic,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-chain-observe":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = apply_chain_paper_observation(
            storage,
            position_id=args.position,
            observed_at=args.observed_at,
            token_y_quote_per_atomic=args.token_y_quote_per_atomic,
            pool_safe=not args.pool_unsafe,
            estimated_exit_cost_quote=args.estimated_exit_cost,
            rebalance_cost_quote=args.rebalance_cost,
            emergency_exit=args.emergency_exit,
            config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-live-observe":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = apply_live_chain_paper_observation(
            storage,
            position_id=args.position,
            observed_at=args.observed_at,
            token_y_quote_per_atomic=args.token_y_quote_per_atomic,
            estimated_exit_cost_quote=args.estimated_exit_cost,
            rebalance_cost_quote=args.rebalance_cost,
            emergency_exit=args.emergency_exit,
            safety_config=_pool_safety_config_from_args(args),
            management_config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-live-latest-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        with open(args.file, "r", encoding="utf-8") as handle:
            raw_items = json.load(handle)
        if not isinstance(raw_items, list):
            raise ValueError("paper-live-latest-run file must contain a JSON array")
        items = tuple(
            LatestPaperCycleItem(
                position_id=str(item["position_id"]),
                token_y_quote_per_atomic=float(
                    item["token_y_quote_per_atomic"]
                ),
                emergency_exit=bool(item.get("emergency_exit", False)),
                estimated_exit_cost_quote=float(
                    item.get("estimated_exit_cost_quote", 0.0)
                ),
                rebalance_cost_quote=(
                    float(item["rebalance_cost_quote"])
                    if item.get("rebalance_cost_quote") is not None
                    else None
                ),
            )
            for item in raw_items
        )
        result = run_latest_live_paper_cycle(
            storage,
            cycle_id=args.cycle_id,
            items=items,
            safety_config=_pool_safety_config_from_args(args),
            management_config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
            retry_failed=args.retry_failed,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-portfolio-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        quotes = None
        if args.quotes_file:
            with open(args.quotes_file, "r", encoding="utf-8") as handle:
                raw_quotes = json.load(handle)
            if not isinstance(raw_quotes, dict):
                raise ValueError(
                    "paper-portfolio-run quotes file must contain a JSON object"
                )
            quotes = {
                str(mint): float(value)
                for mint, value in raw_quotes.items()
            }
        result = run_portfolio_live_paper_cycle(
            storage,
            account_id=args.account,
            cycle_id=args.cycle_id,
            token_y_quotes=quotes,
            quote_max_age_seconds=args.max_quote_age_seconds,
            max_positions=args.max_positions,
            safety_config=_pool_safety_config_from_args(args),
            management_config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
            retry_failed=args.retry_failed,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-live-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        with open(args.file, "r", encoding="utf-8") as handle:
            raw_items = json.load(handle)
        if not isinstance(raw_items, list):
            raise ValueError("paper-live-run file must contain a JSON array")
        items = tuple(
            LivePaperChainBatchItem(
                position_id=str(item["position_id"]),
                token_y_quote_per_atomic=float(
                    item["token_y_quote_per_atomic"]
                ),
                emergency_exit=bool(item.get("emergency_exit", False)),
                estimated_exit_cost_quote=float(
                    item.get("estimated_exit_cost_quote", 0.0)
                ),
                rebalance_cost_quote=(
                    float(item["rebalance_cost_quote"])
                    if item.get("rebalance_cost_quote") is not None
                    else None
                ),
            )
            for item in raw_items
        )
        result = run_live_chain_paper_batch(
            storage,
            run_id=args.run_id,
            observed_at=args.observed_at,
            items=items,
            safety_config=_pool_safety_config_from_args(args),
            management_config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
            retry_failed=args.retry_failed,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-chain-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        with open(args.file, "r", encoding="utf-8") as handle:
            raw_items = json.load(handle)
        if not isinstance(raw_items, list):
            raise ValueError("paper-chain-run file must contain a JSON array")
        items = tuple(
            PaperChainBatchItem(
                position_id=str(item["position_id"]),
                token_y_quote_per_atomic=float(
                    item["token_y_quote_per_atomic"]
                ),
                pool_safe=bool(item.get("pool_safe", True)),
                emergency_exit=bool(item.get("emergency_exit", False)),
                estimated_exit_cost_quote=float(
                    item.get("estimated_exit_cost_quote", 0.0)
                ),
                rebalance_cost_quote=(
                    float(item["rebalance_cost_quote"])
                    if item.get("rebalance_cost_quote") is not None
                    else None
                ),
            )
            for item in raw_items
        )
        result = run_chain_paper_batch(
            storage,
            run_id=args.run_id,
            observed_at=args.observed_at,
            items=items,
            retry_failed=args.retry_failed,
            config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-run":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        with open(args.file, "r", encoding="utf-8") as handle:
            raw_items = json.load(handle)
        if not isinstance(raw_items, list):
            raise ValueError("paper-run file must contain a JSON array")
        observations = tuple(
            PaperBatchObservation(
                position_id=str(item["position_id"]),
                active_bin_id=int(item["active_bin_id"]),
                holding_observations=int(item["holding_observations"]),
                mark_quote=float(item["mark_quote"]),
                fee_delta_quote=float(item.get("fee_delta_quote", 0.0)),
                reward_delta_quote=float(item.get("reward_delta_quote", 0.0)),
                pool_safe=bool(item.get("pool_safe", True)),
                emergency_exit=bool(item.get("emergency_exit", False)),
                estimated_exit_cost_quote=float(
                    item.get("estimated_exit_cost_quote", 0.0)
                ),
                rebalance_cost_quote=(
                    float(item["rebalance_cost_quote"])
                    if item.get("rebalance_cost_quote") is not None
                    else None
                ),
            )
            for item in raw_items
        )
        result = run_paper_observation_batch(
            storage,
            run_id=args.run_id,
            observed_at=args.observed_at,
            observations=observations,
            retry_failed=args.retry_failed,
            config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-performance":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = build_paper_performance(
            storage,
            account_id=args.account,
            policy_source=args.policy_source,
            model_id=args.model_id,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "paper-challenger":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_paper_challenger(
            storage,
            account_id=args.account,
            model_id=args.model_id,
            criteria=PaperChallengerCriteria(
                min_closed_trades=args.min_closed_trades,
                min_realized_return_bps=args.min_return_bps,
                min_win_rate=args.min_win_rate,
                max_realized_drawdown_bps=args.max_drawdown_bps,
                min_return_uplift_vs_baseline_bps=args.min_uplift_bps,
            ),
        )
        output = result.to_record()
        if args.promote:
            promoted = promote_paper_challenger(
                storage,
                validation=result,
            )
            output["promoted_model"] = {
                "model_id": promoted.model_id,
                "status": promoted.status,
            }
        print(json.dumps(output, indent=2))
        if args.require_qualified and not result.paper_qualified:
            raise SystemExit(2)
        return

    if args.command == "phase3-multi-plan":
        settings = Settings.from_env()
        with open(args.file, "r", encoding="utf-8") as handle:
            raw_inputs = json.load(handle)
        if not isinstance(raw_inputs, list):
            raise ValueError("phase3-multi-plan file must contain a JSON array")
        inputs = tuple(
            PoolResearchInput(
                pool_address=str(item["pool_address"]),
                amount_x=int(item["amount_x"]),
                amount_y=int(item["amount_y"]),
                requested_quote=float(item["requested_quote"]),
                network_cost_y_atomic=int(item["network_cost_y_atomic"]),
            )
            for item in raw_inputs
        )
        result = build_multi_pool_research(
            str(settings.database_path),
            inputs=inputs,
            account_equity_quote=args.equity,
            cash_quote=args.cash,
            current_deployed_quote=args.deployed,
            portfolio_drawdown_bps=args.drawdown_bps,
            phase2_gate=None,
            observation_limit=args.observations,
            half_widths=args.half_widths,
            center_offsets=args.center_offsets,
            strategies=args.strategies,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "phase3-plan":
        settings = Settings.from_env()
        result = build_phase3_research_plan(
            str(settings.database_path),
            pool_address=args.pool,
            amount_x=args.amount_x,
            amount_y=args.amount_y,
            requested_quote=args.requested_quote,
            account_equity_quote=args.equity,
            cash_quote=args.cash,
            current_deployed_quote=args.deployed,
            portfolio_drawdown_bps=args.drawdown_bps,
            phase2_gate=None,
            baseline_config=BaselinePolicyConfig(
                estimated_network_cost_y_atomic=args.network_cost_y_atomic,
            ),
            observation_limit=args.observations,
            half_widths=args.half_widths,
            center_offsets=args.center_offsets,
            strategies=args.strategies,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "manage-position":
        result = decide_position_action(
            active_bin_id=args.active_bin,
            min_bin_id=args.min_bin,
            max_bin_id=args.max_bin,
            holding_observations=args.holding_observations,
            rebalances_done=args.rebalances_done,
            pool_safe=not args.pool_unsafe,
            emergency_exit=args.emergency_exit,
            net_pnl_bps=args.net_pnl_bps,
            config=PositionManagementConfig(
                stop_loss_bps=args.stop_loss_bps,
                take_profit_bps=args.take_profit_bps,
                max_rebalances=args.max_rebalances,
                max_holding_observations=args.max_holding_observations,
                proactive_rebalance_buffer_bins=(
                    args.proactive_rebalance_buffer_bins
                ),
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "size-position":
        result = size_position(
            account_equity_quote=args.equity,
            cash_quote=args.cash,
            current_deployed_quote=args.deployed,
            portfolio_drawdown_bps=args.drawdown_bps,
            requested_quote=args.requested,
            config=CapitalSizingConfig(
                max_position_bps=args.max_position_bps,
                max_total_deployed_bps=args.max_deployed_bps,
                min_cash_reserve_bps=args.reserve_bps,
                soft_drawdown_bps=args.soft_drawdown_bps,
                hard_drawdown_bps=args.hard_drawdown_bps,
                soft_drawdown_size_multiplier_bps=args.drawdown_size_multiplier_bps,
                min_position_quote=args.min_position,
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "screen-pools":
        settings = Settings.from_env()
        result = screen_pool_universe(
            str(settings.database_path),
            config=PoolSafetyConfig(
                min_tvl_usd=args.min_tvl_usd,
                min_volume_24h_usd=args.min_volume_24h_usd,
                min_pool_age_hours=args.min_pool_age_hours,
                min_chain_observations=args.min_chain_observations,
                max_dynamic_fee_pct=args.max_dynamic_fee_pct,
            ),
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "scan-chain":
        settings = Settings.from_env()
        result = scan_chain_candidates(
            str(settings.database_path),
            pool_address=args.pool,
            amount_x=args.amount_x,
            amount_y=args.amount_y,
            observation_limit=args.observations,
            half_widths=args.half_widths,
            center_offsets=args.center_offsets,
            strategies=args.strategies,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "baseline-walk-forward":
        settings = Settings.from_env()
        result = walk_forward_baseline(
            str(settings.database_path),
            pool_address=args.pool,
            amount_x=args.amount_x,
            amount_y=args.amount_y,
            phase2_gate=None,
            config=BaselinePolicyConfig(
                min_range_survival_ratio=args.min_range_survival,
                min_excess_vs_hold_bps=args.min_excess_vs_hold_bps,
                require_fee_cost_recovery=not args.allow_unrecovered_entry_cost,
                estimated_network_cost_y_atomic=args.network_cost_y_atomic,
            ),
            lookback_observations=args.lookback_observations,
            forward_observations=args.forward_observations,
            step_observations=args.step_observations,
            half_widths=args.half_widths,
            center_offsets=args.center_offsets,
            strategies=args.strategies,
            max_share_bps=args.max_share_bps,
            favor_x_in_active_bin=args.favor_x_active,
        )
        print(json.dumps(result.to_record(), indent=2))
        return

    if args.command == "backtest-inventory":
        settings = Settings.from_env()
        results = inventory_backtest_from_store(
            settings.database_path,
            pool_address=args.pool,
            capital_quote=args.capital,
            candle_limit=args.candles,
            half_widths=args.half_widths,
            center_offsets=args.center_offsets,
        )
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
