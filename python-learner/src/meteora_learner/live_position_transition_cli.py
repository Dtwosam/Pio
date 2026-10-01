from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .live_position_transition import record_live_position_transition
from .settings import Settings
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-live-transition-link",
        description=(
            "Record one explicit research-reviewed relationship between a "
            "closed LIVE predecessor position and a later LIVE successor "
            "position. This writes only the immutable research transition "
            "annotation table; it does not execute, submit, or alter a LIVE "
            "position."
        ),
    )
    parser.add_argument(
        "--database",
        help="Override PIO_DATABASE_PATH for this research annotation.",
    )
    parser.add_argument(
        "--previous-position",
        required=True,
        help="Explicit predecessor LIVE position address.",
    )
    parser.add_argument(
        "--next-position",
        required=True,
        help="Explicit successor LIVE position address.",
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
    result = record_live_position_transition(
        Storage(database_path),
        previous_position_address=args.previous_position,
        next_position_address=args.next_position,
    )
    output = {
        "research_only": True,
        "policy_actionable": False,
        "execution_wired": False,
        "transition_pairs_inferred": False,
        "live_position_state_modified": False,
        "execution_state_modified": False,
        **result.to_record(),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
