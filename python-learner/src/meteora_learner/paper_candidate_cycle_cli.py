from __future__ import annotations

import argparse
import json
from typing import Callable

from .paper_candidate_cycle import (
    EmpiricalPaperCandidateCycleReport,
    persist_empirical_paper_candidate_cycle,
    run_empirical_paper_candidate_cycle,
)
from .settings import Settings
from .storage import Storage


CycleFn = Callable[..., EmpiricalPaperCandidateCycleReport]
PersistFn = Callable[..., int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-paper-candidate-cycle",
        description=(
            "Run one bounded no-lookahead PAPER candidate decision from "
            "stored chain evidence."
        ),
    )
    parser.add_argument("--pool", required=True)
    parser.add_argument("--amount-x", required=True, type=int)
    parser.add_argument("--amount-y", required=True, type=int)
    parser.add_argument(
        "--network-cost-y-atomic",
        required=True,
        type=int,
    )
    parser.add_argument("--lookback-observations", type=int, default=12)
    parser.add_argument("--forward-observations", type=int, default=2)
    parser.add_argument("--step-observations", type=int)
    parser.add_argument("--as-of")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist non-qualified PAPER research evidence.",
    )
    return parser


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    cycle: CycleFn = run_empirical_paper_candidate_cycle,
    persist_fn: PersistFn = persist_empirical_paper_candidate_cycle,
) -> dict:
    args = build_parser().parse_args(argv)
    cfg = settings or Settings.from_env()

    report = cycle(
        cfg.database_path,
        pool_address=args.pool,
        amount_x=args.amount_x,
        amount_y=args.amount_y,
        network_cost_y_atomic=args.network_cost_y_atomic,
        lookback_observations=args.lookback_observations,
        forward_observations=args.forward_observations,
        step_observations=args.step_observations,
        as_of=args.as_of,
    )
    record = report.to_record()

    if (
        record.get("paper_only") is not True
        or record.get("policy_actionable") is not False
        or record.get("live_authorized") is not False
    ):
        raise RuntimeError("PAPER candidate cycle crossed safety boundary")

    output = dict(record)
    output["persisted_evidence_id"] = None
    if args.persist:
        storage = Storage(cfg.database_path)
        output["persisted_evidence_id"] = persist_fn(
            storage,
            report=report,
        )
    return output


def main() -> None:
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
