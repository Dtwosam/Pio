from __future__ import annotations

import argparse
import json

from .market_research_cycle import run_market_research_cycle
from .settings import Settings
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-market-research-cycle",
        description=(
            "Discover Meteora pools, grow neutral chain history, and report "
            "PAPER-ready pools without opening positions."
        ),
    )
    parser.add_argument("--discovery-page-size", type=int, default=1000)
    parser.add_argument("--discovery-max-pages", type=int, default=100)
    parser.add_argument("--discovery-sort-by", default="tvl:desc")
    parser.add_argument("--seed-batch-limit", type=int, default=25)
    parser.add_argument("--refresh-batch-limit", type=int, default=10)
    parser.add_argument("--bin-array-radius", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--minimum-chain-observations", type=int, default=12)
    parser.add_argument("--intake-max-pools", type=int, default=500)
    parser.add_argument("--observed-at")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    args = build_parser().parse_args(argv)
    cfg = settings or Settings.from_env()
    report = run_market_research_cycle(
        Storage(cfg.database_path),
        settings=cfg,
        discovery_page_size=args.discovery_page_size,
        discovery_max_pages=args.discovery_max_pages,
        discovery_sort_by=args.discovery_sort_by,
        seed_batch_limit=args.seed_batch_limit,
        refresh_batch_limit=args.refresh_batch_limit,
        bin_array_radius=args.bin_array_radius,
        timeout_seconds=args.timeout_seconds,
        minimum_chain_observations=args.minimum_chain_observations,
        intake_max_pools=args.intake_max_pools,
        observed_at=args.observed_at,
    )
    record = report.to_record()
    if (
        record.get("research_only") is not True
        or record.get("paper_only") is not True
        or record.get("policy_actionable") is not False
        or record.get("execution_wired") is not False
    ):
        raise RuntimeError("market research cycle crossed safety boundary")
    return record


def main() -> None:
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
