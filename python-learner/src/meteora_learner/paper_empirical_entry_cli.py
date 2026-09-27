from __future__ import annotations

import argparse
import json

from .paper_empirical_entry_workflow import run_empirical_paper_entry_workflow
from .settings import Settings
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-paper-empirical-entry",
        description=(
            "Run one empirical candidate cycle and open only the selected "
            "virtual PAPER position."
        ),
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--position-id", required=True)
    parser.add_argument("--event-key", required=True)
    parser.add_argument("--pool", required=True)
    parser.add_argument("--amount-x", required=True, type=int)
    parser.add_argument("--amount-y", required=True, type=int)
    parser.add_argument("--capital-quote", required=True, type=float)
    parser.add_argument("--network-cost-y-atomic", required=True, type=int)
    parser.add_argument("--entry-cost-quote", type=float, default=0.0)
    parser.add_argument("--lookback-observations", type=int, default=12)
    parser.add_argument("--forward-observations", type=int, default=2)
    parser.add_argument("--step-observations", type=int)
    parser.add_argument("--max-share-bps", type=int, default=500)
    parser.add_argument("--favor-x-in-active-bin", action="store_true")
    parser.add_argument("--as-of")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    args = build_parser().parse_args(argv)
    cfg = settings or Settings.from_env()
    storage = Storage(cfg.database_path)
    report = run_empirical_paper_entry_workflow(
        storage,
        account_id=args.account,
        position_id=args.position_id,
        event_key=args.event_key,
        pool_address=args.pool,
        amount_x=args.amount_x,
        amount_y=args.amount_y,
        capital_quote=args.capital_quote,
        network_cost_y_atomic=args.network_cost_y_atomic,
        entry_cost_quote=args.entry_cost_quote,
        lookback_observations=args.lookback_observations,
        forward_observations=args.forward_observations,
        step_observations=args.step_observations,
        max_share_bps=args.max_share_bps,
        favor_x_in_active_bin=args.favor_x_in_active_bin,
        as_of=args.as_of,
    )
    record = report.to_record()
    if (
        record.get("paper_only") is not True
        or record.get("live_authorized") is not False
    ):
        raise RuntimeError("empirical PAPER entry CLI crossed safety boundary")
    return record


def main() -> None:
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
