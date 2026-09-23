from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from .ml_dataset import ML_FEATURE_COLUMNS
from .ml_training import MLV1Bundle


@dataclass(frozen=True)
class MLInferenceConfig:
    risk_lambda: float = 1.5
    min_positive_excess_probability: float = 0.55
    min_range_survival_probability: float = 0.50
    min_score_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.risk_lambda < 0:
            raise ValueError("risk_lambda cannot be negative")
        if not 0.0 <= self.min_positive_excess_probability <= 1.0:
            raise ValueError(
                "min_positive_excess_probability must be between 0 and 1"
            )
        if not 0.0 <= self.min_range_survival_probability <= 1.0:
            raise ValueError(
                "min_range_survival_probability must be between 0 and 1"
            )


@dataclass(frozen=True)
class MLCandidatePrediction:
    row_index: int
    pool_address: str
    decision_observed_at: str
    strategy: str
    half_width: int
    center_offset: int
    predicted_net_return_bps: float
    predicted_excess_vs_hold_bps: float
    predicted_downside_bps: float
    predicted_range_survival: float
    predicted_positive_excess_probability: float
    risk_adjusted_score_bps: float
    eligible: bool
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class MLInferenceReport:
    candidates_seen: int
    candidates_eligible: int
    research_choice: MLCandidatePrediction | None
    policy_actionable: bool
    ranking_rule: str
    predictions: tuple[MLCandidatePrediction, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def score_ml_candidates(
    bundle: MLV1Bundle,
    frame: pd.DataFrame,
    *,
    config: MLInferenceConfig = MLInferenceConfig(),
) -> MLInferenceReport:
    required = {
        "pool_address",
        "decision_observed_at",
        "strategy",
        "half_width",
        "center_offset",
        *ML_FEATURE_COLUMNS,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing inference columns: {missing}")
    if frame.empty:
        raise ValueError("inference frame is empty")

    x = frame[list(bundle.feature_columns)].apply(
        pd.to_numeric,
        errors="coerce",
    )
    net = bundle.net_return_model.predict(x)
    excess = bundle.excess_vs_hold_model.predict(x)
    downside = np.maximum(0.0, bundle.downside_model.predict(x))
    survival = np.clip(bundle.range_survival_model.predict(x), 0.0, 1.0)
    positive = bundle.positive_excess_model.predict_proba(x)[:, 1]

    predictions: list[MLCandidatePrediction] = []
    for offset, (index, row) in enumerate(frame.iterrows()):
        score = float(excess[offset] - config.risk_lambda * downside[offset])
        reasons: list[str] = []
        if positive[offset] < config.min_positive_excess_probability:
            reasons.append(
                f"positive-edge probability {positive[offset]:.6f} < required "
                f"{config.min_positive_excess_probability:.6f}"
            )
        if survival[offset] < config.min_range_survival_probability:
            reasons.append(
                f"range survival {survival[offset]:.6f} < required "
                f"{config.min_range_survival_probability:.6f}"
            )
        if score < config.min_score_bps:
            reasons.append(
                f"risk-adjusted score {score:.6f} bps < required "
                f"{config.min_score_bps:.6f} bps"
            )

        predictions.append(
            MLCandidatePrediction(
                row_index=int(index),
                pool_address=str(row["pool_address"]),
                decision_observed_at=str(row["decision_observed_at"]),
                strategy=str(row["strategy"]),
                half_width=int(row["half_width"]),
                center_offset=int(row["center_offset"]),
                predicted_net_return_bps=float(net[offset]),
                predicted_excess_vs_hold_bps=float(excess[offset]),
                predicted_downside_bps=float(downside[offset]),
                predicted_range_survival=float(survival[offset]),
                predicted_positive_excess_probability=float(positive[offset]),
                risk_adjusted_score_bps=score,
                eligible=not reasons,
                rejection_reasons=tuple(reasons),
            )
        )

    ordered = sorted(
        predictions,
        key=lambda item: (
            item.eligible,
            item.risk_adjusted_score_bps,
            item.predicted_positive_excess_probability,
            item.predicted_range_survival,
            -item.half_width,
            -abs(item.center_offset),
            item.strategy,
        ),
        reverse=True,
    )
    eligible = [item for item in ordered if item.eligible]
    return MLInferenceReport(
        candidates_seen=len(predictions),
        candidates_eligible=len(eligible),
        research_choice=eligible[0] if eligible else None,
        policy_actionable=False,
        ranking_rule=(
            "predicted_excess_vs_hold_bps - risk_lambda * "
            "predicted_downside_bps, then positive-edge probability "
            "and range survival"
        ),
        predictions=tuple(ordered),
    )
