from __future__ import annotations

import argparse
import json
from typing import Callable

from .market_paper_intake import (
    MarketPaperIntakeReport,
    build_market_paper_intake,
)
from .settings import Settings


IntakeFn = Callable[..., MarketPaperIntakeReport]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-market-paper-intake",
        description=(
            "List discovered pools that have enough stored chain history "
            "for PAPER candidate evaluation."
        ),
    )
    parser.add_argument(
        "--minimum-chain-observations",
        type=int,
        default=12,
    )
    parser.add_argument("--max-pools", type=int, default=500)
    return parser


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    intake: IntakeFn = build_market_paper_intake,
) -> dict:
    args = build_parser().parse_args(argv)
    cfg = settings or Settings.from_env()

    report = intake(
        cfg.database_path,
        minimum_chain_observations=args.minimum_chain_observations,
        max_pools=args.max_pools,
    )
    record = report.to_record()

    if (
        record.get("research_only") is not True
        or record.get("paper_only") is not True
        or record.get("policy_actionable") is not False
        or record.get("execution_wired") is not False
    ):
        raise RuntimeError("market-to-PAPER intake crossed safety boundary")

    return record


def main() -> None:
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
