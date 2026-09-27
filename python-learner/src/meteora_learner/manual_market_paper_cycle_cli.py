from __future__ import annotations

import argparse
import json

from .manual_market_paper_cycle import run_manual_market_paper_cycle
from .settings import Settings
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-manual-market-paper-cycle",
        description=(
            "Run one manual research -> PAPER management -> bounded new "
            "virtual-entry cycle. No live capital."
        ),
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--capital-per-position", required=True, type=float)
    parser.add_argument("--network-cost-quote", required=True, type=float)
    parser.add_argument("--max-new-positions", type=int, default=1)
    parser.add_argument("--max-pools-considered", type=int, default=25)
    parser.add_argument("--minimum-chain-observations", type=int, default=12)
    parser.add_argument("--intake-max-pools", type=int, default=500)
    parser.add_argument("--seed-batch-limit", type=int, default=25)
    parser.add_argument("--refresh-batch-limit", type=int, default=10)
    parser.add_argument("--discovery-page-size", type=int, default=1000)
    parser.add_argument("--discovery-max-pages", type=int, default=100)
    parser.add_argument("--discovery-sort-by", default="tvl:desc")
    parser.add_argument("--bin-array-radius", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--quote-max-age-seconds", type=int, default=300)
    parser.add_argument("--max-share-bps", type=int, default=500)
    parser.add_argument("--scheduler-max-positions", type=int)
    parser.add_argument("--observed-at")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    args = build_parser().parse_args(argv)
    cfg = settings or Settings.from_env()
    report = run_manual_market_paper_cycle(
        Storage(cfg.database_path),
        account_id=args.account,
        run_id=args.run_id,
        per_position_capital_quote=args.capital_per_position,
        network_cost_quote=args.network_cost_quote,
        max_new_positions=args.max_new_positions,
        max_pools_considered=args.max_pools_considered,
        minimum_chain_observations=args.minimum_chain_observations,
        intake_max_pools=args.intake_max_pools,
        seed_batch_limit=args.seed_batch_limit,
        refresh_batch_limit=args.refresh_batch_limit,
        discovery_page_size=args.discovery_page_size,
        discovery_max_pages=args.discovery_max_pages,
        discovery_sort_by=args.discovery_sort_by,
        bin_array_radius=args.bin_array_radius,
        timeout_seconds=args.timeout_seconds,
        quote_max_age_seconds=args.quote_max_age_seconds,
        max_share_bps=args.max_share_bps,
        scheduler_max_positions=args.scheduler_max_positions,
        observed_at=args.observed_at,
        settings=cfg,
    )
    record = report.to_record()
    if (
        record.get("manual_only") is not True
        or record.get("paper_only") is not True
        or record.get("policy_actionable") is not False
        or record.get("live_authorized") is not False
    ):
        raise RuntimeError("manual market PAPER cycle crossed safety boundary")
    return record


def main() -> None:
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
