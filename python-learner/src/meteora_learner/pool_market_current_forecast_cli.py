from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .pool_market_current_forecast import (
    build_current_pool_market_forecast_from_store,
)
from .settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-current-pool-forecast",
        description=(
            "Produce research-only continuous forecasts for the latest "
            "observed Meteora pool universe. No pool is selected."
        ),
    )
    parser.add_argument(
        "--database",
        help="Override PIO_DATABASE_PATH for this research run.",
    )
    parser.add_argument(
        "--as-of",
        help=(
            "Optional ISO timestamp cutoff for reproducible historical "
            "research inference."
        ),
    )
    parser.add_argument("--horizon-rows", type=int, default=6)
    parser.add_argument("--volatility-window", type=int, default=6)
    parser.add_argument("--drawdown-window", type=int, default=12)
    parser.add_argument("--activity-window", type=int, default=6)
    parser.add_argument(
        "--min-train-decision-times",
        type=int,
        default=30,
    )
    parser.add_argument(
        "--validation-decision-times",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--step-decision-times",
        type=int,
        default=10,
    )
    parser.add_argument("--min-train-rows", type=int, default=50)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    database_path = (
        Path(args.database)
        if args.database
        else settings.database_path
    )

    report = build_current_pool_market_forecast_from_store(
        str(database_path),
        as_of=args.as_of,
        horizon_rows=args.horizon_rows,
        volatility_window=args.volatility_window,
        drawdown_window=args.drawdown_window,
        activity_window=args.activity_window,
        min_train_decision_times=args.min_train_decision_times,
        validation_decision_times=args.validation_decision_times,
        step_decision_times=args.step_decision_times,
        min_train_rows=args.min_train_rows,
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
