from __future__ import annotations

import argparse
import json
from typing import Callable

from .ml_inference import MLInferenceConfig
from .paper_ml_pair_entry import (
    PairedMLPaperEntryResult,
    open_paired_ml_paper_entries,
)
from .settings import Settings
from .storage import Storage
from .strategy import StrategyType


PairEntryFn = Callable[..., PairedMLPaperEntryResult]


def _parse_int_csv(raw: str) -> tuple[int, ...]:
    values = tuple(
        int(item.strip())
        for item in raw.split(",")
        if item.strip()
    )
    if not values:
        raise argparse.ArgumentTypeError(
            "comma-separated integer list cannot be empty"
        )
    return values


def _parse_strategy_csv(raw: str) -> tuple[StrategyType, ...]:
    try:
        values = tuple(
            StrategyType(item.strip())
            for item in raw.split(",")
            if item.strip()
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not values:
        raise argparse.ArgumentTypeError(
            "comma-separated strategy list cannot be empty"
        )
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pio-paper-ml-pair-entry",
        description=(
            "Open equal-capital, chain-bound ML_CHAMPION and "
            "ML_CHALLENGER PAPER positions from the exact same "
            "no-lookahead current candidate frame."
        ),
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--cycle-id", required=True)
    parser.add_argument("--pool", required=True)
    parser.add_argument("--amount-x", required=True, type=int)
    parser.add_argument("--amount-y", required=True, type=int)
    parser.add_argument(
        "--network-cost-y-atomic",
        required=True,
        type=int,
    )
    parser.add_argument("--capital", required=True, type=float)
    parser.add_argument("--entry-cost", required=True, type=float)
    parser.add_argument("--incumbent-position", required=True)
    parser.add_argument("--challenger-position", required=True)
    parser.add_argument("--incumbent-event-key", required=True)
    parser.add_argument("--challenger-event-key", required=True)
    parser.add_argument(
        "--lookback-observations",
        type=int,
        default=12,
    )
    parser.add_argument(
        "--half-widths",
        type=_parse_int_csv,
        default=(0, 1, 2, 5, 10),
    )
    parser.add_argument(
        "--center-offsets",
        type=_parse_int_csv,
        default=(0,),
    )
    parser.add_argument(
        "--strategies",
        type=_parse_strategy_csv,
        default=tuple(StrategyType),
    )
    parser.add_argument("--max-share-bps", type=int, default=500)
    parser.add_argument("--favor-x-active", action="store_true")
    parser.add_argument(
        "--near-liquidity-radius",
        type=int,
        default=5,
    )
    parser.add_argument("--risk-lambda", type=float, default=1.5)
    parser.add_argument(
        "--min-positive-probability",
        type=float,
        default=0.55,
    )
    parser.add_argument(
        "--min-range-survival",
        type=float,
        default=0.50,
    )
    parser.add_argument("--min-score-bps", type=float, default=0.0)
    parser.add_argument("--as-of")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    pair_entry: PairEntryFn = open_paired_ml_paper_entries,
) -> dict:
    args = build_parser().parse_args(argv)
    cfg = settings or Settings.from_env()
    result = pair_entry(
        Storage(cfg.database_path),
        account_id=args.account,
        cycle_id=args.cycle_id,
        pool_address=args.pool,
        amount_x=args.amount_x,
        amount_y=args.amount_y,
        network_cost_y_atomic=args.network_cost_y_atomic,
        capital_quote=args.capital,
        incumbent_position_id=args.incumbent_position,
        challenger_position_id=args.challenger_position,
        incumbent_event_key=args.incumbent_event_key,
        challenger_event_key=args.challenger_event_key,
        entry_cost_quote=args.entry_cost,
        lookback_observations=args.lookback_observations,
        half_widths=args.half_widths,
        center_offsets=args.center_offsets,
        strategies=args.strategies,
        max_share_bps=args.max_share_bps,
        favor_x_in_active_bin=args.favor_x_active,
        near_liquidity_radius=args.near_liquidity_radius,
        inference_config=MLInferenceConfig(
            risk_lambda=args.risk_lambda,
            min_positive_excess_probability=(
                args.min_positive_probability
            ),
            min_range_survival_probability=args.min_range_survival,
            min_score_bps=args.min_score_bps,
        ),
        as_of=args.as_of,
    )
    record = result.to_record()
    if (
        record.get("paper_only") is not True
        or record.get("policy_actionable") is not False
        or record.get("live_authorized") is not False
    ):
        raise RuntimeError(
            "paired ML PAPER entry crossed its safety boundary"
        )
    return record


def main() -> None:
    print(json.dumps(run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
