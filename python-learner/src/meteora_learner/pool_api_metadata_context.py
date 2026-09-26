from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from .research_store import ResearchStore


POOL_API_METADATA_FEATURE_COLUMNS = (
    "api_snapshot_age_seconds",
    "api_pool_age_seconds",
    "api_bin_step",
    "api_apr",
    "api_apy",
    "api_dynamic_fee_pct",
    "api_base_fee_pct",
    "api_max_fee_pct",
    "api_protocol_fee_pct",
    "api_collect_fee_mode",
    "api_is_blacklisted",
    "api_token_x_decimals",
    "api_token_y_decimals",
)


@dataclass(frozen=True)
class PoolAPIMetadataContextReport:
    rows_seen: int
    rows_with_snapshot: int
    rows_without_snapshot: int
    rows_with_complete_metadata: int
    rows_with_incomplete_metadata: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: Any, *, field: str) -> pd.Timestamp:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid {field}: {value}")
    return pd.Timestamp(parsed)


def _numeric_or_nan(value: Any) -> float:
    if value is None:
        return np.nan
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result if np.isfinite(result) else np.nan


def attach_pool_api_metadata_from_store(
    database_path: str,
    decision_frame: pd.DataFrame,
) -> tuple[pd.DataFrame, PoolAPIMetadataContextReport]:
    """
    Attach the latest normalized pool metadata available at each decision time.

    These fields are model context only. Missing metadata remains missing; no
    economic pass/fail rule is applied to age, fees, APR/APY or blacklist state.
    """
    required = {"pool_address", "decision_observed_at"}
    missing = sorted(required - set(decision_frame.columns))
    if missing:
        raise ValueError(
            f"missing API-metadata decision columns: {missing}"
        )

    frame = decision_frame.copy()
    frame["pool_address"] = (
        frame["pool_address"].astype(str).str.strip()
    )
    if (frame["pool_address"] == "").any():
        raise ValueError("pool_address cannot be empty")

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
    feature_rows: list[dict[str, float]] = []
    rows_with_snapshot = 0
    complete = 0

    for row in frame.itertuples(index=False):
        pool_address = str(getattr(row, "pool_address"))
        decision_time = pd.Timestamp(
            getattr(row, "decision_observed_at")
        )
        snapshot = store.latest_api_pool_snapshot(
            pool_address,
            as_of=decision_time.isoformat(),
        )

        if snapshot is None:
            feature_rows.append(
                {
                    column: np.nan
                    for column in POOL_API_METADATA_FEATURE_COLUMNS
                }
            )
            continue

        rows_with_snapshot += 1
        observed_at = _parse_time(
            snapshot["observed_at"],
            field="pool snapshot observed_at",
        )
        snapshot_age = float(
            (decision_time - observed_at).total_seconds()
        )
        if snapshot_age < 0:
            raise AssertionError(
                "as-of API pool lookup returned a future snapshot"
            )

        pool_age = np.nan
        if snapshot.get("pool_created_at") is not None:
            created_at = pd.to_datetime(
                snapshot["pool_created_at"],
                utc=True,
                errors="coerce",
            )
            if not pd.isna(created_at):
                pool_age = float(
                    (
                        decision_time
                        - pd.Timestamp(created_at)
                    ).total_seconds()
                )
                if pool_age < 0:
                    pool_age = np.nan

        is_blacklisted = snapshot.get("is_blacklisted")
        blacklist_value = (
            float(bool(is_blacklisted))
            if is_blacklisted is not None
            else np.nan
        )

        features = {
            "api_snapshot_age_seconds": snapshot_age,
            "api_pool_age_seconds": pool_age,
            "api_bin_step": _numeric_or_nan(
                snapshot.get("bin_step")
            ),
            "api_apr": _numeric_or_nan(snapshot.get("apr")),
            "api_apy": _numeric_or_nan(snapshot.get("apy")),
            "api_dynamic_fee_pct": _numeric_or_nan(
                snapshot.get("dynamic_fee_pct")
            ),
            "api_base_fee_pct": _numeric_or_nan(
                snapshot.get("base_fee_pct")
            ),
            "api_max_fee_pct": _numeric_or_nan(
                snapshot.get("max_fee_pct")
            ),
            "api_protocol_fee_pct": _numeric_or_nan(
                snapshot.get("protocol_fee_pct")
            ),
            "api_collect_fee_mode": _numeric_or_nan(
                snapshot.get("collect_fee_mode")
            ),
            "api_is_blacklisted": blacklist_value,
            "api_token_x_decimals": _numeric_or_nan(
                snapshot.get("token_x_decimals")
            ),
            "api_token_y_decimals": _numeric_or_nan(
                snapshot.get("token_y_decimals")
            ),
        }
        if all(
            np.isfinite(value)
            for value in features.values()
        ):
            complete += 1
        feature_rows.append(features)

    metadata = pd.DataFrame(
        feature_rows,
        index=frame.index,
    )
    enriched = pd.concat([frame, metadata], axis=1)

    return enriched, PoolAPIMetadataContextReport(
        rows_seen=len(frame),
        rows_with_snapshot=rows_with_snapshot,
        rows_without_snapshot=len(frame) - rows_with_snapshot,
        rows_with_complete_metadata=complete,
        rows_with_incomplete_metadata=(
            rows_with_snapshot - complete
        ),
    )
