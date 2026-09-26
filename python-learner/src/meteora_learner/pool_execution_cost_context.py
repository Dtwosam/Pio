from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from .pool_lp_learning import ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS
from .pool_lp_mint_ablation import MINT_ENRICHED_LP_FEATURE_COLUMNS
from .pool_lp_training import ENRICHED_LP_MODEL_TARGET_COLUMNS
from .research_store import ResearchStore


POOL_EXECUTION_COST_FEATURE_COLUMNS = (
    "execution_history_samples",
    "execution_history_age_seconds",
    "execution_receipt_coverage_rate",
    "execution_success_rate",
    "execution_median_network_fee_lamports",
    "execution_p95_network_fee_lamports",
    "execution_median_compute_units",
    "execution_p95_compute_units",
    "execution_add_fraction",
    "execution_rebalance_fraction",
    "execution_add_request_coverage_rate",
    "execution_median_add_underfill_bps",
    "execution_p95_add_underfill_bps",
    "execution_composition_fee_sample_sides",
    "execution_median_composition_fee_bps",
    "execution_p95_composition_fee_bps",
)

EXECUTION_COST_ENRICHED_LP_FEATURE_COLUMNS = (
    *MINT_ENRICHED_LP_FEATURE_COLUMNS,
    *POOL_EXECUTION_COST_FEATURE_COLUMNS,
)


@dataclass(frozen=True)
class ExecutionCostContextReport:
    rows_seen: int
    rows_with_history: int
    rows_with_complete_context: int
    rows_missing_history: int
    rows_incomplete_context: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionCostTrainingFrameReport:
    rows_seen: int
    rows_ready: int
    rows_dropped_missing_history: int
    rows_dropped_incomplete_context: int
    feature_columns: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionCostAblationTarget:
    target: str
    base_mae: float
    execution_mae: float
    execution_mae_improvement: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionCostAblationFold:
    fold_index: int
    validation_start: str
    validation_end: str
    train_rows: int
    purged_rows: int
    validation_rows: int
    targets: tuple[ExecutionCostAblationTarget, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionCostAblationAggregate:
    target: str
    folds: int
    mean_base_mae: float
    mean_execution_mae: float
    mean_execution_mae_improvement: float
    execution_better_folds: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionCostAblationReport:
    research_only: bool
    policy_actionable: bool
    execution_wired: bool
    decision_times: int
    base_feature_count: int
    execution_feature_count: int
    combined_feature_count: int
    folds: tuple[ExecutionCostAblationFold, ...]
    aggregates: tuple[ExecutionCostAblationAggregate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)])


def _group_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["signature"]), int(row["event_index"]))
        item = grouped.setdefault(
            key,
            {
                **row,
                "composition_fee_x_total": 0,
                "composition_fee_y_total": 0,
                "_composition_seen": set(),
            },
        )
        comp_index = row.get("composition_event_index")
        if comp_index is not None and comp_index not in item["_composition_seen"]:
            item["_composition_seen"].add(comp_index)
            item["composition_fee_x_total"] += int(
                str(row.get("composition_fee_x") or 0)
            )
            item["composition_fee_y_total"] += int(
                str(row.get("composition_fee_y") or 0)
            )
    output = []
    for item in grouped.values():
        item.pop("_composition_seen", None)
        output.append(item)
    return sorted(
        output,
        key=lambda row: (
            int(row["block_time"]),
            str(row["signature"]),
            int(row["event_index"]),
        ),
    )


def _summarize(
    actions: list[dict[str, Any]],
    decision_time: pd.Timestamp,
) -> dict[str, float]:
    prior = [
        row
        for row in actions
        if int(row["block_time"]) <= int(decision_time.timestamp())
    ]
    if not prior:
        return {column: np.nan for column in POOL_EXECUTION_COST_FEATURE_COLUMNS}

    latest_time = max(int(row["block_time"]) for row in prior)

    receipt_by_signature: dict[str, dict[str, Any]] = {}
    for row in prior:
        receipt_by_signature.setdefault(str(row["signature"]), row)
    receipt_rows = [
        row
        for row in receipt_by_signature.values()
        if row.get("network_fee_lamports") is not None
        and row.get("compute_units_consumed") is not None
        and row.get("succeeded") is not None
    ]

    fees = [float(row["network_fee_lamports"]) for row in receipt_rows]
    compute = [float(row["compute_units_consumed"]) for row in receipt_rows]

    adds = [row for row in prior if row["event_type"] == "AddLiquidity"]
    rebalances = [row for row in prior if row["event_type"] == "Rebalancing"]

    request_adds = [
        row
        for row in adds
        if row.get("requested_amount_x") is not None
        and row.get("requested_amount_y") is not None
    ]

    underfill: list[float] = []
    composition_ratios: list[float] = []
    for row in adds:
        actual_x = int(str(row.get("amount_x") or 0))
        actual_y = int(str(row.get("amount_y") or 0))
        comp_x = int(str(row.get("composition_fee_x_total") or 0))
        comp_y = int(str(row.get("composition_fee_y_total") or 0))

        if actual_x > 0:
            composition_ratios.append(comp_x * 10_000.0 / actual_x)
        if actual_y > 0:
            composition_ratios.append(comp_y * 10_000.0 / actual_y)

        if row.get("requested_amount_x") is not None:
            requested_x = int(str(row["requested_amount_x"]))
            if requested_x > 0:
                underfill.append(
                    (requested_x - actual_x) * 10_000.0 / requested_x
                )
        if row.get("requested_amount_y") is not None:
            requested_y = int(str(row["requested_amount_y"]))
            if requested_y > 0:
                underfill.append(
                    (requested_y - actual_y) * 10_000.0 / requested_y
                )

    required_lists = (fees, compute, underfill, composition_ratios)
    if not all(required_lists) or not adds:
        return {
            column: (
                float(len(prior))
                if column == "execution_history_samples"
                else float(decision_time.timestamp() - latest_time)
                if column == "execution_history_age_seconds"
                else np.nan
            )
            for column in POOL_EXECUTION_COST_FEATURE_COLUMNS
        }

    return {
        "execution_history_samples": float(len(prior)),
        "execution_history_age_seconds": float(
            decision_time.timestamp() - latest_time
        ),
        "execution_receipt_coverage_rate": (
            len(receipt_rows) / len(receipt_by_signature)
        ),
        "execution_success_rate": (
            sum(bool(row["succeeded"]) for row in receipt_rows)
            / len(receipt_rows)
        ),
        "execution_median_network_fee_lamports": float(median(fees)),
        "execution_p95_network_fee_lamports": _p95(fees),
        "execution_median_compute_units": float(median(compute)),
        "execution_p95_compute_units": _p95(compute),
        "execution_add_fraction": len(adds) / len(prior),
        "execution_rebalance_fraction": len(rebalances) / len(prior),
        "execution_add_request_coverage_rate": (
            len(request_adds) / len(adds)
        ),
        "execution_median_add_underfill_bps": float(median(underfill)),
        "execution_p95_add_underfill_bps": _p95(underfill),
        "execution_composition_fee_sample_sides": float(
            len(composition_ratios)
        ),
        "execution_median_composition_fee_bps": float(
            median(composition_ratios)
        ),
        "execution_p95_composition_fee_bps": _p95(
            composition_ratios
        ),
    }


def attach_execution_cost_context_from_store(
    database_path: str,
    decision_frame: pd.DataFrame,
) -> tuple[pd.DataFrame, ExecutionCostContextReport]:
    required = {"pool_address", "decision_observed_at"}
    missing = sorted(required - set(decision_frame.columns))
    if missing:
        raise ValueError(
            f"missing execution-cost decision columns: {missing}"
        )

    frame = decision_frame.copy()
    frame["decision_observed_at"] = pd.to_datetime(
        frame["decision_observed_at"],
        utc=True,
        errors="coerce",
    )
    if frame["decision_observed_at"].isna().any():
        raise ValueError(
            "decision_observed_at contains invalid timestamps"
        )

    store = ResearchStore(database_path)
    histories = {
        pool: _group_actions(store.pool_execution_action_rows(pool))
        for pool in sorted(frame["pool_address"].astype(str).unique())
    }

    feature_rows = []
    rows_with_history = 0
    rows_complete = 0
    for row in frame.itertuples(index=False):
        pool = str(getattr(row, "pool_address"))
        history = histories.get(pool, [])
        features = _summarize(
            history,
            pd.Timestamp(getattr(row, "decision_observed_at")),
        )
        if np.isfinite(features["execution_history_samples"]):
            rows_with_history += 1
        if all(np.isfinite(value) for value in features.values()):
            rows_complete += 1
        feature_rows.append(features)

    features = pd.DataFrame(feature_rows, index=frame.index)
    enriched = pd.concat([frame, features], axis=1)
    return enriched, ExecutionCostContextReport(
        rows_seen=len(frame),
        rows_with_history=rows_with_history,
        rows_with_complete_context=rows_complete,
        rows_missing_history=len(frame) - rows_with_history,
        rows_incomplete_context=rows_with_history - rows_complete,
    )


def build_execution_cost_enriched_lp_frame(
    database_path: str,
    mint_enriched_lp_frame: pd.DataFrame,
) -> tuple[pd.DataFrame, ExecutionCostTrainingFrameReport]:
    enriched, context = attach_execution_cost_context_from_store(
        database_path,
        mint_enriched_lp_frame,
    )
    for column in EXECUTION_COST_ENRICHED_LP_FEATURE_COLUMNS:
        enriched[column] = pd.to_numeric(
            enriched[column],
            errors="coerce",
        )

    missing_history = enriched["execution_history_samples"].isna()
    incomplete = (
        ~missing_history
        & enriched[list(POOL_EXECUTION_COST_FEATURE_COLUMNS)]
        .isna()
        .any(axis=1)
    )
    base_incomplete = enriched[
        list(MINT_ENRICHED_LP_FEATURE_COLUMNS)
    ].isna().any(axis=1)

    ready = ~(missing_history | incomplete | base_incomplete)
    output = enriched.loc[ready].copy()
    return output, ExecutionCostTrainingFrameReport(
        rows_seen=len(enriched),
        rows_ready=len(output),
        rows_dropped_missing_history=int(missing_history.sum()),
        rows_dropped_incomplete_context=int(incomplete.sum()),
        feature_columns=EXECUTION_COST_ENRICHED_LP_FEATURE_COLUMNS,
    )


def evaluate_execution_cost_ablation(
    frame: pd.DataFrame,
    *,
    min_train_decision_times: int = 30,
    validation_decision_times: int = 10,
    step_decision_times: int = 10,
    min_train_rows: int = 50,
) -> ExecutionCostAblationReport:
    required = {
        "pool_address",
        "decision_observed_at",
        "forward_end_observed_at",
        *EXECUTION_COST_ENRICHED_LP_FEATURE_COLUMNS,
        *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"missing execution-cost ablation columns: {missing}"
        )

    work = frame.copy()
    for column in ("decision_observed_at", "forward_end_observed_at"):
        work[column] = pd.to_datetime(column=work[column], utc=True, errors="coerce")
    if work[["decision_observed_at", "forward_end_observed_at"]].isna().any(axis=None):
        raise ValueError("execution-cost ablation has invalid timestamps")
    if (work["forward_end_observed_at"] <= work["decision_observed_at"]).any():
        raise ValueError("forward_end_observed_at must be after decision_observed_at")

    numeric = (
        *EXECUTION_COST_ENRICHED_LP_FEATURE_COLUMNS,
        *ENRICHED_LP_CONTINUOUS_TARGET_COLUMNS,
    )
    for column in numeric:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    if work[list(numeric)].isna().any(axis=None):
        raise ValueError("execution-cost ablation contains missing numeric values")
    if not np.isfinite(work[list(numeric)].to_numpy(dtype=float)).all():
        raise ValueError("execution-cost ablation contains non-finite values")

    work["target_downside_bps"] = np.maximum(
        0.0, -work["target_net_return_bps"].to_numpy(dtype=float)
    )
    work = work.sort_values(
        ["decision_observed_at", "pool_address"], kind="stable"
    ).reset_index(drop=True)

    times = work["decision_observed_at"].drop_duplicates().sort_values().tolist()
    if len(times) < min_train_decision_times + validation_decision_times:
        raise ValueError(
            "not enough unique decision timestamps for execution-cost ablation"
        )

    base_features = list(MINT_ENRICHED_LP_FEATURE_COLUMNS)
    full_features = list(EXECUTION_COST_ENRICHED_LP_FEATURE_COLUMNS)
    folds = []
    start = min_train_decision_times
    fold_index = 0
    while start + validation_decision_times <= len(times):
        validation_times = times[start:start + validation_decision_times]
        validation_start = pd.Timestamp(validation_times[0])
        validation_end = pd.Timestamp(validation_times[-1])

        prior = work[work["decision_observed_at"] < validation_start].copy()
        train = prior[prior["forward_end_observed_at"] < validation_start].copy()
        valid = work[
            (work["decision_observed_at"] >= validation_start)
            & (work["decision_observed_at"] <= validation_end)
        ].copy()
        purged_rows = len(prior) - len(train)

        if len(train) < min_train_rows:
            raise ValueError(
                f"fold {fold_index + 1} has only {len(train)} "
                f"purged training rows; need {min_train_rows}"
            )
        if valid.empty:
            raise ValueError(f"fold {fold_index + 1} has no validation rows")

        targets = []
        for target_index, target in enumerate(ENRICHED_LP_MODEL_TARGET_COLUMNS):
            y_train = train[target].to_numpy(dtype=float)
            y_valid = valid[target].to_numpy(dtype=float)
            seed = 6000 + fold_index * len(ENRICHED_LP_MODEL_TARGET_COLUMNS) + target_index

            base_model = HistGradientBoostingRegressor(random_state=seed)
            exec_model = HistGradientBoostingRegressor(random_state=seed)
            base_model.fit(train[base_features], y_train)
            exec_model.fit(train[full_features], y_train)

            base_mae = float(
                mean_absolute_error(y_valid, base_model.predict(valid[base_features]))
            )
            execution_mae = float(
                mean_absolute_error(y_valid, exec_model.predict(valid[full_features]))
            )
            targets.append(
                ExecutionCostAblationTarget(
                    target=target,
                    base_mae=base_mae,
                    execution_mae=execution_mae,
                    execution_mae_improvement=base_mae - execution_mae,
                )
            )

        fold_index += 1
        folds.append(
            ExecutionCostAblationFold(
                fold_index=fold_index,
                validation_start=validation_start.isoformat(),
                validation_end=validation_end.isoformat(),
                train_rows=len(train),
                purged_rows=purged_rows,
                validation_rows=len(valid),
                targets=tuple(targets),
            )
        )
        start += step_decision_times

    aggregates = []
    for target in ENRICHED_LP_MODEL_TARGET_COLUMNS:
        rows = [
            next(item for item in fold.targets if item.target == target)
            for fold in folds
        ]
        aggregates.append(
            ExecutionCostAblationAggregate(
                target=target,
                folds=len(rows),
                mean_base_mae=float(np.mean([item.base_mae for item in rows])),
                mean_execution_mae=float(
                    np.mean([item.execution_mae for item in rows])
                ),
                mean_execution_mae_improvement=float(
                    np.mean([item.execution_mae_improvement for item in rows])
                ),
                execution_better_folds=sum(
                    item.execution_mae_improvement > 0 for item in rows
                ),
            )
        )

    return ExecutionCostAblationReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        decision_times=len(times),
        base_feature_count=len(base_features),
        execution_feature_count=len(POOL_EXECUTION_COST_FEATURE_COLUMNS),
        combined_feature_count=len(full_features),
        folds=tuple(folds),
        aggregates=tuple(aggregates),
    )
