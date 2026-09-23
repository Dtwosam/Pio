from __future__ import annotations

import argparse
import json
import sys

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
from .paper_performance import build_paper_performance
from .paper_challenger import (
    PaperChallengerCriteria,
    evaluate_paper_challenger,
    promote_paper_challenger,
)
from .phase3_plan import build_phase3_research_plan
from .multi_pool_research import PoolResearchInput, build_multi_pool_research
from .reconciliation import reconcile_position
from .reconciliation_corpus import build_reconciliation_corpus
from .rebalance_execution import build_rebalance_execution_calibration
from .rebalance_replay import replay_rebalance_lifecycle
from .research import inventory_backtest_from_store
from .settings import Settings
from .storage import Storage
from .strategy import StrategyType
from .transaction_event_ingest import ingest_transaction_events
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
    paper_challenger.add_argument("--phase3-ready", action="store_true")
    paper_challenger.add_argument("--min-closed-trades", type=int, default=20)
    paper_challenger.add_argument("--min-return-bps", type=int, default=0)
    paper_challenger.add_argument("--min-win-rate", type=float, default=0.5)
    paper_challenger.add_argument("--max-drawdown-bps", type=int, default=1500)
    paper_challenger.add_argument("--min-uplift-bps", type=int, default=0)
    paper_challenger.add_argument("--require-qualified", action="store_true")
    paper_challenger.add_argument("--promote", action="store_true")

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
        print(json.dumps(result.to_record(), indent=2))
        if args.require_ready and not result.promotion_ready:
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
            phase3_ready=args.phase3_ready,
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
