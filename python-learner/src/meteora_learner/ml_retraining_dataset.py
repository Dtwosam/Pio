from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Any, Sequence

import pandas as pd

from .ml_action_dataset import (
    MLActionDatasetReport,
    build_ml_action_dataset,
)
from .ml_dataset import MLTrainingExample
from .strategy import StrategyType


@dataclass(frozen=True)
class MLRetrainPoolSpec:
    pool_address: str
    amount_x: int
    amount_y: int
    network_cost_y_atomic: int

    def validate(self) -> None:
        if not self.pool_address.strip():
            raise ValueError("pool_address is required")
        if self.amount_x < 0 or self.amount_y < 0:
            raise ValueError("token amounts cannot be negative")
        if self.amount_x == 0 and self.amount_y == 0:
            raise ValueError("at least one token amount must be positive")
        if self.network_cost_y_atomic < 0:
            raise ValueError("network_cost_y_atomic cannot be negative")


@dataclass(frozen=True)
class MultiPoolMLDatasetReport:
    pools_requested: int
    pools_built: int
    decision_points: int
    candidates_seen: int
    examples_built: int
    candidates_dropped: int
    dataset_sha256: str
    dataset_version: str
    pool_summaries: tuple[dict[str, Any], ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MultiPoolMLDataset:
    report: MultiPoolMLDatasetReport
    frame: pd.DataFrame


def _frame_from_examples(
    examples: Sequence[MLTrainingExample],
) -> pd.DataFrame:
    if not examples:
        raise ValueError("combined ML action dataset is empty")
    frame = pd.DataFrame(asdict(item) for item in examples)
    keys = [
        "pool_address",
        "decision_observed_at",
        "strategy",
        "half_width",
        "center_offset",
    ]
    duplicates = frame.duplicated(keys, keep=False)
    if duplicates.any():
        duplicate_rows = frame.loc[duplicates, keys].to_dict("records")
        raise ValueError(
            f"combined ML action dataset contains duplicate actions: "
            f"{duplicate_rows[:5]}"
        )
    return frame.sort_values(
        [
            "decision_observed_at",
            "pool_address",
            "strategy",
            "half_width",
            "center_offset",
        ],
        kind="stable",
    ).reset_index(drop=True)


def combine_ml_action_reports(
    reports: Sequence[MLActionDatasetReport],
) -> MultiPoolMLDataset:
    if not reports:
        raise ValueError("at least one ML action report is required")
    pool_addresses = [item.pool_address for item in reports]
    if len(set(pool_addresses)) != len(pool_addresses):
        raise ValueError(
            "multi-pool ML dataset requires unique pool reports"
        )

    examples = tuple(
        example
        for report in reports
        for example in report.examples
    )
    frame = _frame_from_examples(examples)
    canonical = frame.to_csv(
        index=False,
        lineterminator="\n",
        float_format="%.12g",
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    version = f"ML_ACTION_DATASET_V1:{digest[:16]}"

    return MultiPoolMLDataset(
        report=MultiPoolMLDatasetReport(
            pools_requested=len(reports),
            pools_built=len(reports),
            decision_points=sum(
                item.decision_points for item in reports
            ),
            candidates_seen=sum(
                item.candidates_seen for item in reports
            ),
            examples_built=len(frame),
            candidates_dropped=sum(
                item.candidates_dropped for item in reports
            ),
            dataset_sha256=digest,
            dataset_version=version,
            pool_summaries=tuple(
                {
                    "pool_address": item.pool_address,
                    "decision_points": item.decision_points,
                    "candidates_seen": item.candidates_seen,
                    "examples_built": item.examples_built,
                    "candidates_dropped": item.candidates_dropped,
                    "drop_reasons": item.drop_reasons,
                }
                for item in reports
            ),
        ),
        frame=frame,
    )


def build_multi_pool_ml_action_dataset(
    database_path: str,
    *,
    pools: Sequence[MLRetrainPoolSpec],
    lookback_observations: int = 12,
    forward_observations: int = 2,
    step_observations: int | None = None,
    half_widths: Sequence[int] = (0, 1, 2, 5, 10),
    center_offsets: Sequence[int] = (0,),
    strategies: Sequence[StrategyType | str] = (
        StrategyType.SPOT,
        StrategyType.CURVE,
        StrategyType.BID_ASK,
    ),
    max_share_bps: int = 500,
    favor_x_in_active_bin: bool = False,
    max_observed_at: str | None = None,
) -> MultiPoolMLDataset:
    if not pools:
        raise ValueError("at least one pool spec is required")
    addresses = [item.pool_address for item in pools]
    if len(set(addresses)) != len(addresses):
        raise ValueError("pool specs must use unique pool addresses")

    reports: list[MLActionDatasetReport] = []
    for spec in pools:
        spec.validate()
        reports.append(
            build_ml_action_dataset(
                database_path,
                pool_address=spec.pool_address,
                amount_x=spec.amount_x,
                amount_y=spec.amount_y,
                network_cost_y_atomic=(
                    spec.network_cost_y_atomic
                ),
                lookback_observations=lookback_observations,
                forward_observations=forward_observations,
                step_observations=step_observations,
                half_widths=half_widths,
                center_offsets=center_offsets,
                strategies=strategies,
                max_share_bps=max_share_bps,
                favor_x_in_active_bin=favor_x_in_active_bin,
                max_observed_at=max_observed_at,
            )
        )
    return combine_ml_action_reports(reports)
