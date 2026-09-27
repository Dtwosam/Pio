from __future__ import annotations

import argparse
import json
from typing import Callable

from .market_pool_universe import (
    PoolUniverseDiscoveryReport,
    discover_pool_universe,
)
from .settings import Settings
from .storage import Storage


DiscoverFn = Callable[..., PoolUniverseDiscoveryReport]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-market-universe-discover",
        description="Capture the broad Meteora pool universe for research only.",
    )
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--sort-by", default="tvl:desc")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    discover: DiscoverFn = discover_pool_universe,
) -> dict:
    args = build_parser().parse_args(argv)
    cfg = settings or Settings.from_env()
    storage = Storage(cfg.database_path)

    report = discover(
        storage,
        settings=cfg,
        page_size=args.page_size,
        max_pages=args.max_pages,
        sort_by=args.sort_by,
    )
    record = report.to_record()

    if (
        record.get("research_only") is not True
        or record.get("policy_actionable") is not False
        or record.get("execution_wired") is not False
    ):
        raise RuntimeError("market-universe discovery crossed research-only boundary")

    return record


def main() -> None:
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
