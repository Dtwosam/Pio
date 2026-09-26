from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from .pool_market_report_artifact import (
    save_pool_market_research_report,
)
from .pool_market_research_cycle import (
    run_pool_market_research_cycle,
)
from .settings import Settings
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-market-research",
        description=(
            "Run one research-only Meteora market discovery and "
            "learning-evaluation cycle."
        ),
    )
    parser.add_argument(
        "--database",
        help="Override PIO_DATABASE_PATH for this research run.",
    )
    parser.add_argument(
        "--no-capture",
        action="store_true",
        help=(
            "Do not call the Meteora pool API; evaluate only "
            "already-stored observations."
        ),
    )
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=100)
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
    parser.add_argument(
        "--output-dir",
        help=(
            "Optionally save a checksum-verified research report "
            "artifact in this directory."
        ),
    )
    parser.add_argument(
        "--report-id",
        help=(
            "Artifact identifier used with --output-dir. "
            "If omitted, a UTC timestamp plus random suffix is used."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    settings = Settings.from_env()
    if args.database:
        settings = replace(
            settings,
            database_path=Path(args.database),
        )

    storage = Storage(settings.database_path)
    report = run_pool_market_research_cycle(
        storage,
        capture_universe=not args.no_capture,
        settings=settings,
        page_size=args.page_size,
        max_pages=args.max_pages,
        horizon_rows=args.horizon_rows,
        volatility_window=args.volatility_window,
        drawdown_window=args.drawdown_window,
        activity_window=args.activity_window,
        min_train_decision_times=args.min_train_decision_times,
        validation_decision_times=args.validation_decision_times,
        step_decision_times=args.step_decision_times,
        min_train_rows=args.min_train_rows,
    )

    output = report.to_record()

    if args.report_id and not args.output_dir:
        raise ValueError("--report-id requires --output-dir")

    if args.output_dir:
        report_id = args.report_id
        if report_id is None:
            timestamp = datetime.now(timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ"
            )
            report_id = (
                f"pool-market-{timestamp}-{uuid4().hex[:8]}"
            )
        artifact = save_pool_market_research_report(
            report,
            directory=args.output_dir,
            report_id=report_id,
        )
        output["artifact"] = artifact.to_record()

    print(
        json.dumps(
            output,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
