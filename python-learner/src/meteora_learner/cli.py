from __future__ import annotations

import argparse
import json
import sys

from .add_execution import build_add_execution_calibration
from .baseline_policy import BaselinePolicyConfig
from .baseline_walk_forward import walk_forward_baseline
from .calibration_queue import build_calibration_work_queue
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
