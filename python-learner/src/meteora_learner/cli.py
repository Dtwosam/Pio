from __future__ import annotations

import argparse
import json
import sys

from .chain_ingest import ingest_chain_snapshot
from .collector import collect_once
from .meteora_api import MeteoraDataAPI
from .position_ingest import ingest_position_snapshot
from .research import inventory_backtest_from_store
from .settings import Settings
from .storage import Storage


def _parse_int_csv(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not parsed:
        raise argparse.ArgumentTypeError("at least one integer is required")
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

    ingest_position = subparsers.add_parser(
        "ingest-position-snapshot",
        help="Ingest JSON emitted by Rust inspect-position",
    )
    ingest_position.add_argument(
        "--file",
        default="-",
        help="JSON file path, or - for stdin",
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
