from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from .add_execution import build_add_execution_calibration
from .baseline_policy import BaselinePolicyConfig
from .baseline_walk_forward import walk_forward_baseline
from .calibration_queue import build_calibration_work_queue
from .capital_sizing import CapitalSizingConfig, size_position
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
    persist_phase2_promotion,
    persist_phase5_promotion,
    persist_phase6_promotion,
    persist_phase7_promotion,
    phase_promotion_state,
)
from .phase3_validation import Phase3PromotionCriteria
from .phase5_validation import Phase5PromotionCriteria, evaluate_phase5_promotion
from .phase6_validation import Phase6PromotionCriteria, evaluate_phase6_promotion
from .phase7_validation import Phase7PromotionCriteria, evaluate_phase7_promotion
from .phase3_workflow import (
    Phase3ValidationInput,
    validate_phase3_from_chain,
)
from .pool_safety import PoolSafetyConfig, screen_pool_universe
from .position_policy import PositionManagementConfig, decide_position_action
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
from .ml_challenger import MLChallengerCriteria
from .ml_inference import MLInferenceConfig
from .ml_registry import model_record, start_paper_challenger
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
from .storage import Storage
from .strategy import StrategyType
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
from .live_champion_monitor import (
    LiveChampionCriteria,
    evaluate_live_champion,
    persist_live_champion_report,
    rollback_live_champion,
)
from .transaction_costs import build_transaction_cost_report


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
        help="Print persisted Phase 2, Phase 3, Phase 5, Phase 6 and Phase 7 promotion state",
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

    ml_live_monitor = subparsers.add_parser(
        "ml-live-monitor",
        help="Evaluate persisted live labels for champion rollback safety",
    )
    ml_live_monitor.add_argument("--model-id")
    ml_live_monitor.add_argument("--min-live-labels", type=int, default=10)
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

    if args.command == "ml-live-monitor":
        settings = Settings.from_env()
        storage = Storage(settings.database_path)
        result = evaluate_live_champion(
            storage,
            model_id=args.model_id,
            criteria=LiveChampionCriteria(
                min_live_labels=args.min_live_labels,
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
