from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .live_transition_annotation_backlog import (
    build_live_transition_annotation_backlog,
)
from .settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-live-transition-backlog",
        description=(
            "List unlinked LIVE predecessor and successor transition "
            "annotation candidates in separate chronological lists. This is "
            "read-only research evidence: it emits no pair suggestions, "
            "economic ranking, or inferred transition."
        ),
    )
    parser.add_argument(
        "--database",
        help="Override PIO_DATABASE_PATH for this read-only research run.",
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
    report = build_live_transition_annotation_backlog(
        str(database_path)
    )
    print(json.dumps(report.to_record(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
