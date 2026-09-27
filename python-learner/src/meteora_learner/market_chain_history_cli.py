from __future__ import annotations

import argparse
import json

from .market_chain_history_cycle import run_market_chain_history_cycle
from .settings import Settings
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-market-chain-history",
        description=(
            "Seed and refresh bounded read-only chain history for the "
            "discovered Meteora pool universe."
        ),
    )
    parser.add_argument("--seed-batch-limit", type=int, default=25)
    parser.add_argument("--refresh-batch-limit", type=int, default=10)
    parser.add_argument("--bin-array-radius", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--observed-at")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    args = build_parser().parse_args(argv)
    cfg = settings or Settings.from_env()
    report = run_market_chain_history_cycle(
        Storage(cfg.database_path),
        seed_batch_limit=args.seed_batch_limit,
        refresh_batch_limit=args.refresh_batch_limit,
        bin_array_radius=args.bin_array_radius,
        timeout_seconds=args.timeout_seconds,
        observed_at=args.observed_at,
    )
    record = report.to_record()
    if (
        record.get("research_only") is not True
        or record.get("read_only_capture") is not True
        or record.get("policy_actionable") is not False
        or record.get("execution_wired") is not False
    ):
        raise RuntimeError("market chain history CLI crossed safety boundary")
    return record


def main() -> None:
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
