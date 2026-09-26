from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .market_context_collection_cycle import (
    run_market_context_collection_cycle,
)
from .settings import Settings
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-market-context-capture",
        description=(
            "Run one research-only pool discovery / chain context / "
            "mint context collection cycle."
        ),
    )
    parser.add_argument(
        "--database",
        help="Override PIO_DATABASE_PATH for this research run.",
    )
    parser.add_argument(
        "--no-discovery",
        action="store_true",
        help="Skip Meteora pool-universe discovery.",
    )
    parser.add_argument(
        "--no-chain",
        action="store_true",
        help="Skip missing chain pool context capture.",
    )
    parser.add_argument(
        "--refresh-chain",
        action="store_true",
        help=(
            "Also refresh already-observed pools oldest-chain-snapshot "
            "first to build longitudinal chain/liquidity history."
        ),
    )
    parser.add_argument(
        "--no-mints",
        action="store_true",
        help="Skip missing token mint context capture.",
    )
    parser.add_argument(
        "--refresh-mints",
        action="store_true",
        help=(
            "Also refresh already-observed required mints oldest-snapshot "
            "first to build longitudinal token/mint history."
        ),
    )
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument(
        "--chain-batch-limit",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--chain-refresh-batch-limit",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--mint-batch-limit",
        type=int,
        default=20,
    )
    parser.add_argument(
        "--mint-refresh-batch-limit",
        type=int,
        default=20,
    )
    parser.add_argument(
        "--bin-array-radius",
        type=int,
        default=0,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    capture_universe = not args.no_discovery
    capture_chain = not args.no_chain
    refresh_chain = args.refresh_chain
    capture_mints = not args.no_mints
    refresh_mints = args.refresh_mints
    if not (
        capture_universe
        or capture_chain
        or refresh_chain
        or capture_mints
        or refresh_mints
    ):
        raise ValueError(
            "at least one collection stage must remain enabled"
        )

    settings = Settings.from_env()
    database_path = (
        Path(args.database)
        if args.database
        else settings.database_path
    )
    storage = Storage(database_path)

    report = run_market_context_collection_cycle(
        storage,
        capture_universe=capture_universe,
        capture_chain_context=capture_chain,
        refresh_chain_context=refresh_chain,
        capture_mint_context=capture_mints,
        refresh_mint_context=refresh_mints,
        settings=settings,
        page_size=args.page_size,
        max_pages=args.max_pages,
        chain_batch_limit=args.chain_batch_limit,
        chain_refresh_batch_limit=args.chain_refresh_batch_limit,
        mint_batch_limit=args.mint_batch_limit,
        mint_refresh_batch_limit=args.mint_refresh_batch_limit,
        bin_array_radius=args.bin_array_radius,
        timeout_seconds=args.timeout_seconds,
    )
    print(
        json.dumps(
            report.to_record(),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
