from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from .pool_lp_mint_report_artifact import (
    save_mint_feature_research_report,
)
from .pool_lp_mint_research import (
    run_mint_feature_research_from_dataset_file,
)
from .settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-mint-feature-research",
        description=(
            "Evaluate decision-time token/mint context on Pio's "
            "canonical LP research dataset."
        ),
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Canonical Pio LP/retraining CSV dataset.",
    )
    parser.add_argument(
        "--database",
        help="Override PIO_DATABASE_PATH for this research run.",
    )
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
    database_path = (
        Path(args.database)
        if args.database
        else settings.database_path
    )

    report = run_mint_feature_research_from_dataset_file(
        str(database_path),
        args.dataset,
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
                f"mint-feature-{timestamp}-{uuid4().hex[:8]}"
            )
        artifact = save_mint_feature_research_report(
            report,
            directory=args.output_dir,
            report_id=report_id,
        )
        output["artifact"] = artifact.to_record()

    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
