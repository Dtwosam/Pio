from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.metrics import brier_score_loss, mean_absolute_error

from .ml_dataset import ML_FEATURE_COLUMNS, ML_TARGET_COLUMNS


@dataclass(frozen=True)
class MLV1Metrics:
    net_return_mae_bps: float
    excess_vs_hold_mae_bps: float
    downside_mae_bps: float
    range_survival_mae: float
    positive_excess_brier: float


@dataclass
class MLV1Bundle:
    feature_columns: tuple[str, ...]
    net_return_model: HistGradientBoostingRegressor
    excess_vs_hold_model: HistGradientBoostingRegressor
    downside_model: HistGradientBoostingRegressor
    range_survival_model: HistGradientBoostingRegressor
    positive_excess_model: HistGradientBoostingClassifier
    train_rows: int
    validation_rows: int
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    metrics: MLV1Metrics

    def metadata(self) -> dict[str, Any]:
        return {
            "feature_columns": list(self.feature_columns),
            "train_rows": self.train_rows,
            "validation_rows": self.validation_rows,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "validation_start": self.validation_start,
            "validation_end": self.validation_end,
            "metrics": asdict(self.metrics),
        }


def _prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    required = {
        "pool_address",
        "decision_observed_at",
        *ML_FEATURE_COLUMNS,
        *ML_TARGET_COLUMNS,
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"missing ML dataset columns: {missing}")

    frame = df.copy()
    frame["decision_observed_at"] = pd.to_datetime(
        frame["decision_observed_at"],
        utc=True,
        errors="coerce",
    )
    if frame["decision_observed_at"].isna().any():
        raise ValueError("decision_observed_at contains invalid timestamps")

    for column in ML_FEATURE_COLUMNS + ML_TARGET_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    target_nulls = frame[list(ML_TARGET_COLUMNS)].isna().any(axis=1)
    if target_nulls.any():
        raise ValueError(
            f"{int(target_nulls.sum())} rows have missing ML targets"
        )

    return frame.sort_values(
        ["decision_observed_at", "pool_address"],
        kind="stable",
    ).reset_index(drop=True)


def _timestamp_split(
    frame: pd.DataFrame,
    split_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0.5 <= split_fraction < 1.0:
        raise ValueError("split_fraction must be between 0.5 and 1.0")

    timestamps = frame["decision_observed_at"].drop_duplicates().tolist()
    if len(timestamps) < 2:
        raise ValueError("need at least two unique decision timestamps")

    cut = max(1, min(len(timestamps) - 1, int(len(timestamps) * split_fraction)))
    validation_start = timestamps[cut]
    train = frame[frame["decision_observed_at"] < validation_start]
    valid = frame[frame["decision_observed_at"] >= validation_start]
    if train.empty or valid.empty:
        raise ValueError("timestamp split produced an empty train or validation set")
    return train, valid


def train_ml_v1_frame(
    df: pd.DataFrame,
    *,
    split_fraction: float = 0.8,
    min_rows: int = 50,
) -> MLV1Bundle:
    """
    Train experimental Phase 4 models with a strict time-ordered holdout.

    No ML model is promoted or made actionable by this function.
    """
    frame = _prepare_frame(df)
    if len(frame) < min_rows:
        raise ValueError(
            f"need at least {min_rows} ML examples; received {len(frame)}"
        )

    train, valid = _timestamp_split(frame, split_fraction)
    features = list(ML_FEATURE_COLUMNS)
    x_train = train[features]
    x_valid = valid[features]

    y_net_train = train["target_net_return_bps"]
    y_net_valid = valid["target_net_return_bps"]
    y_excess_train = train["target_excess_vs_hold_bps"]
    y_excess_valid = valid["target_excess_vs_hold_bps"]
    y_down_train = np.maximum(0.0, -y_net_train.to_numpy(dtype=float))
    y_down_valid = np.maximum(0.0, -y_net_valid.to_numpy(dtype=float))
    y_survival_train = train["target_range_survival_ratio"]
    y_survival_valid = valid["target_range_survival_ratio"]
    y_positive_train = train["target_positive_excess"].astype(int)
    y_positive_valid = valid["target_positive_excess"].astype(int)

    if y_positive_train.nunique() < 2:
        raise ValueError(
            "training split needs both positive and non-positive excess examples"
        )

    net_model = HistGradientBoostingRegressor(random_state=42)
    excess_model = HistGradientBoostingRegressor(random_state=43)
    downside_model = HistGradientBoostingRegressor(random_state=44)
    survival_model = HistGradientBoostingRegressor(
        random_state=45,
        loss="squared_error",
    )
    positive_model = HistGradientBoostingClassifier(random_state=46)

    net_model.fit(x_train, y_net_train)
    excess_model.fit(x_train, y_excess_train)
    downside_model.fit(x_train, y_down_train)
    survival_model.fit(x_train, y_survival_train)
    positive_model.fit(x_train, y_positive_train)

    net_pred = net_model.predict(x_valid)
    excess_pred = excess_model.predict(x_valid)
    downside_pred = downside_model.predict(x_valid)
    survival_pred = np.clip(survival_model.predict(x_valid), 0.0, 1.0)
    positive_prob = positive_model.predict_proba(x_valid)[:, 1]

    metrics = MLV1Metrics(
        net_return_mae_bps=float(mean_absolute_error(y_net_valid, net_pred)),
        excess_vs_hold_mae_bps=float(
            mean_absolute_error(y_excess_valid, excess_pred)
        ),
        downside_mae_bps=float(
            mean_absolute_error(y_down_valid, downside_pred)
        ),
        range_survival_mae=float(
            mean_absolute_error(y_survival_valid, survival_pred)
        ),
        positive_excess_brier=float(
            brier_score_loss(y_positive_valid, positive_prob)
        ),
    )

    return MLV1Bundle(
        feature_columns=ML_FEATURE_COLUMNS,
        net_return_model=net_model,
        excess_vs_hold_model=excess_model,
        downside_model=downside_model,
        range_survival_model=survival_model,
        positive_excess_model=positive_model,
        train_rows=len(train),
        validation_rows=len(valid),
        train_start=train["decision_observed_at"].iloc[0].isoformat(),
        train_end=train["decision_observed_at"].iloc[-1].isoformat(),
        validation_start=valid["decision_observed_at"].iloc[0].isoformat(),
        validation_end=valid["decision_observed_at"].iloc[-1].isoformat(),
        metrics=metrics,
    )
