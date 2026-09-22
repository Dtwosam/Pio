from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import mean
from typing import Sequence


@dataclass(frozen=True)
class ValidationPair:
    position_address: str
    predicted_pnl_usd: float
    actual_pnl_usd: float
    predicted_return_pct: float | None = None
    actual_return_pct: float | None = None


@dataclass(frozen=True)
class ValidationSummary:
    positions: int
    pnl_mae_usd: float
    pnl_rmse_usd: float
    pnl_bias_usd: float
    pnl_mean_absolute_pct_of_actual: float | None
    return_mae_pct_points: float | None


def summarize_validation(pairs: Sequence[ValidationPair]) -> ValidationSummary:
    if not pairs:
        raise ValueError("at least one validation pair is required")

    pnl_errors = [item.predicted_pnl_usd - item.actual_pnl_usd for item in pairs]
    pnl_mae = mean(abs(error) for error in pnl_errors)
    pnl_rmse = math.sqrt(mean(error * error for error in pnl_errors))
    pnl_bias = mean(pnl_errors)

    relative_errors = [
        abs(item.predicted_pnl_usd - item.actual_pnl_usd) / abs(item.actual_pnl_usd) * 100.0
        for item in pairs
        if item.actual_pnl_usd != 0
    ]

    return_errors = [
        abs(float(item.predicted_return_pct) - float(item.actual_return_pct))
        for item in pairs
        if item.predicted_return_pct is not None and item.actual_return_pct is not None
    ]

    return ValidationSummary(
        positions=len(pairs),
        pnl_mae_usd=float(pnl_mae),
        pnl_rmse_usd=float(pnl_rmse),
        pnl_bias_usd=float(pnl_bias),
        pnl_mean_absolute_pct_of_actual=(
            float(mean(relative_errors)) if relative_errors else None
        ),
        return_mae_pct_points=float(mean(return_errors)) if return_errors else None,
    )
