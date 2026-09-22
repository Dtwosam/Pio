from __future__ import annotations

import argparse
import json
import sys

from .chain_ingest import ingest_chain_snapshot
from .chain_replay import replay_small_lp_history
from .chain_scan import scan_chain_candidates
from .collector import collect_once
from .meteora_api import MeteoraDataAPI
from .position_ingest import ingest_position_snapshot
from .position_history import collect_position_history
from .phase2_gate import Phase2PromotionCriteria, evaluate_phase2_promotion_gate
from .reconciliation import reconcile_position
from .reconciliation_corpus import build_reconciliation_corpus
from .research import inventory_backtest_from_store
from .settings import Settings
from .storage import Storage
from .strategy import StrategyType


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
