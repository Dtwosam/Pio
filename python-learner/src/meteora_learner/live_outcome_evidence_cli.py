from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from .live_outcome_evidence import build_live_outcome_evidence
from .live_outcome_evidence_artifact import (
    save_live_outcome_evidence_report,
)
from .settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-live-outcome-evidence",
        description=(
            "Summarize immutable quote-backed controlled-live "
            "learning evidence without changing live state."
        ),
    )
    parser.add_argument(
        "--database",
        help="Override PIO_DATABASE_PATH for this read-only research run.",
    )
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

    report = build_live_outcome_evidence(str(database_path))
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
                f"live-outcome-{timestamp}-{uuid4().hex[:8]}"
            )
        artifact = save_live_outcome_evidence_report(
            report,
            directory=args.output_dir,
            report_id=report_id,
        )
        output["artifact"] = artifact.to_record()

    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
