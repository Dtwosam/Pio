from __future__ import annotations

import argparse
import json

from .market_paper_exploration import run_market_paper_exploration
from .settings import Settings
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-market-paper-explore",
        description=(
            "Open a bounded number of virtual empirical PAPER positions from "
            "the neutral market-ready pool queue."
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
    parser.add_argument("--quote-max-age-seconds", type=int, default=300)
    parser.add_argument("--max-share-bps", type=int, default=500)
    parser.add_argument("--observed-at")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    args = build_parser().parse_args(argv)
    cfg = settings or Settings.from_env()
    report = run_market_paper_exploration(
        Storage(cfg.database_path),
        account_id=args.account,
        run_id=args.run_id,
        per_position_capital_quote=args.capital_per_position,
        network_cost_quote=args.network_cost_quote,
        max_new_positions=args.max_new_positions,
        max_pools_considered=args.max_pools_considered,
        minimum_chain_observations=args.minimum_chain_observations,
        intake_max_pools=args.intake_max_pools,
        quote_max_age_seconds=args.quote_max_age_seconds,
        max_share_bps=args.max_share_bps,
        observed_at=args.observed_at,
    )
    record = report.to_record()
    if (
        record.get("paper_only") is not True
        or record.get("policy_actionable") is not False
        or record.get("live_authorized") is not False
    ):
        raise RuntimeError("market PAPER exploration crossed safety boundary")
    return record


def main() -> None:
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
