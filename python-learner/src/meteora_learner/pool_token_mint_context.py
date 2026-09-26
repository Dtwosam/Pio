from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

from .chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from .mint_risk import TOKEN_2022_PROGRAM
from .research_store import ResearchStore


TOKEN_MINT_CONTEXT_FEATURE_COLUMNS = (
    "mint_chain_snapshot_age_seconds",
    "mint_x_snapshot_age_seconds",
    "mint_x_log10_supply_tokens",
    "mint_x_decimals",
    "mint_x_initialized",
    "mint_x_mint_authority_active",
    "mint_x_freeze_authority_active",
    "mint_x_token2022_extension_data_len",
    "mint_x_has_token2022_extension_data",
    "mint_x_program_standard_spl",
    "mint_x_program_token2022",
    "mint_x_program_matches_pool",
    "mint_x_observation_count",
    "mint_x_has_previous_snapshot",
    "mint_x_seconds_since_previous_snapshot",
    "mint_x_log10_supply_change_since_previous",
    "mint_x_log10_supply_change_since_first",
    "mint_x_mint_authority_changed_since_previous",
    "mint_x_freeze_authority_changed_since_previous",
    "mint_x_program_changed_since_previous",
    "mint_x_extension_data_len_change_since_previous",
    "mint_x_initialized_changed_since_previous",
    "mint_x_mint_authority_change_count",
    "mint_x_freeze_authority_change_count",
    "mint_x_program_change_count",
    "mint_x_extension_change_count",
    "mint_y_snapshot_age_seconds",
    "mint_y_log10_supply_tokens",
    "mint_y_decimals",
    "mint_y_initialized",
    "mint_y_mint_authority_active",
    "mint_y_freeze_authority_active",
    "mint_y_token2022_extension_data_len",
    "mint_y_has_token2022_extension_data",
    "mint_y_program_standard_spl",
    "mint_y_program_token2022",
    "mint_y_program_matches_pool",
    "mint_y_observation_count",
    "mint_y_has_previous_snapshot",
    "mint_y_seconds_since_previous_snapshot",
    "mint_y_log10_supply_change_since_previous",
    "mint_y_log10_supply_change_since_first",
    "mint_y_mint_authority_changed_since_previous",
    "mint_y_freeze_authority_changed_since_previous",
    "mint_y_program_changed_since_previous",
    "mint_y_extension_data_len_change_since_previous",
    "mint_y_initialized_changed_since_previous",
    "mint_y_mint_authority_change_count",
    "mint_y_freeze_authority_change_count",
    "mint_y_program_change_count",
    "mint_y_extension_change_count",
)


@dataclass(frozen=True)
class TokenMintContextReport:
    rows_seen: int
    rows_with_chain_pool_state: int
    rows_with_both_mints: int
    rows_missing_chain_pool_state: int
    rows_missing_mint_x: int
    rows_missing_mint_y: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid timestamp: {value}")
    return pd.Timestamp(parsed)


def _mint_features(
    mint: dict[str, Any] | None,
    *,
    decision_time: pd.Timestamp,
    expected_program: str | None,
    prefix: str,
) -> dict[str, float]:
    names = {
        "snapshot_age_seconds": np.nan,
        "log10_supply_tokens": np.nan,
        "decimals": np.nan,
        "initialized": np.nan,
        "mint_authority_active": np.nan,
        "freeze_authority_active": np.nan,
        "token2022_extension_data_len": np.nan,
        "has_token2022_extension_data": np.nan,
        "program_standard_spl": np.nan,
        "program_token2022": np.nan,
        "program_matches_pool": np.nan,
    }
    if mint is None:
        return {
            f"{prefix}_{key}": value
            for key, value in names.items()
        }

    observed = _parse_time(mint["observed_at"])
    age = float((decision_time - observed).total_seconds())
    if age < 0:
        raise AssertionError(
            "as-of mint lookup returned a future snapshot"
        )

    decimals = int(mint["decimals"])
    supply_atomic = int(str(mint["supply"]))
    supply_tokens = supply_atomic / (10 ** decimals)
    program = str(mint["token_program"])

    values = {
        "snapshot_age_seconds": age,
        "log10_supply_tokens": math.log10(1.0 + supply_tokens),
        "decimals": float(decimals),
        "initialized": float(bool(mint["is_initialized"])),
        "mint_authority_active": float(
            mint["mint_authority"] is not None
        ),
        "freeze_authority_active": float(
            mint["freeze_authority"] is not None
        ),
        "token2022_extension_data_len": float(
            int(mint["token_2022_extension_data_len"])
        ),
        "has_token2022_extension_data": float(
            bool(mint["has_token_2022_extension_data"])
        ),
        "program_standard_spl": float(
            program == STANDARD_SPL_TOKEN_PROGRAM
        ),
        "program_token2022": float(
            program == TOKEN_2022_PROGRAM
        ),
        "program_matches_pool": (
            float(program == expected_program)
            if expected_program is not None
            else np.nan
        ),
    }
    return {
        f"{prefix}_{key}": value
        for key, value in values.items()
    }


def _supply_log10(snapshot: dict[str, Any]) -> float:
    decimals = int(snapshot["decimals"])
    supply_atomic = int(str(snapshot["supply"]))
    supply_tokens = supply_atomic / (10 ** decimals)
    return math.log10(1.0 + supply_tokens)


def _changed(
    left: dict[str, Any],
    right: dict[str, Any],
    key: str,
) -> bool:
    return left.get(key) != right.get(key)


def _mint_history_features(
    history: list[dict[str, Any]],
    *,
    decision_time: pd.Timestamp,
    prefix: str,
) -> dict[str, float]:
    if not history:
        names = (
            "observation_count",
            "has_previous_snapshot",
            "seconds_since_previous_snapshot",
            "log10_supply_change_since_previous",
            "log10_supply_change_since_first",
            "mint_authority_changed_since_previous",
            "freeze_authority_changed_since_previous",
            "program_changed_since_previous",
            "extension_data_len_change_since_previous",
            "initialized_changed_since_previous",
            "mint_authority_change_count",
            "freeze_authority_change_count",
            "program_change_count",
            "extension_change_count",
        )
        return {
            f"{prefix}_{name}": np.nan
            for name in names
        }

    current = history[-1]
    current_time = _parse_time(current["observed_at"])
    if current_time > decision_time:
        raise AssertionError(
            "as-of mint history contains a future snapshot"
        )

    has_previous = len(history) >= 2
    previous = history[-2] if has_previous else current
    previous_time = _parse_time(previous["observed_at"])

    current_supply = _supply_log10(current)
    previous_supply = _supply_log10(previous)
    first_supply = _supply_log10(history[0])

    mint_authority_changes = 0
    freeze_authority_changes = 0
    program_changes = 0
    extension_changes = 0
    for left, right in zip(history, history[1:]):
        mint_authority_changes += int(
            _changed(left, right, "mint_authority")
        )
        freeze_authority_changes += int(
            _changed(left, right, "freeze_authority")
        )
        program_changes += int(
            _changed(left, right, "token_program")
        )
        extension_changes += int(
            _changed(
                left,
                right,
                "token_2022_extension_data_len",
            )
            or _changed(
                left,
                right,
                "has_token_2022_extension_data",
            )
        )

    values = {
        "observation_count": float(len(history)),
        "has_previous_snapshot": float(has_previous),
        "seconds_since_previous_snapshot": (
            float((current_time - previous_time).total_seconds())
            if has_previous
            else 0.0
        ),
        "log10_supply_change_since_previous": (
            current_supply - previous_supply
            if has_previous
            else 0.0
        ),
        "log10_supply_change_since_first": (
            current_supply - first_supply
        ),
        "mint_authority_changed_since_previous": float(
            has_previous
            and _changed(
                previous,
                current,
                "mint_authority",
            )
        ),
        "freeze_authority_changed_since_previous": float(
            has_previous
            and _changed(
                previous,
                current,
                "freeze_authority",
            )
        ),
        "program_changed_since_previous": float(
            has_previous
            and _changed(
                previous,
                current,
                "token_program",
            )
        ),
        "extension_data_len_change_since_previous": (
            float(
                int(current["token_2022_extension_data_len"])
                - int(previous["token_2022_extension_data_len"])
            )
            if has_previous
            else 0.0
        ),
        "initialized_changed_since_previous": float(
            has_previous
            and _changed(
                previous,
                current,
                "is_initialized",
            )
        ),
        "mint_authority_change_count": float(
            mint_authority_changes
        ),
        "freeze_authority_change_count": float(
            freeze_authority_changes
        ),
        "program_change_count": float(program_changes),
        "extension_change_count": float(extension_changes),
    }
    return {
        f"{prefix}_{name}": value
        for name, value in values.items()
    }


def attach_token_mint_context_from_store(
    database_path: str,
    decision_frame: pd.DataFrame,
) -> tuple[pd.DataFrame, TokenMintContextReport]:
    """
    Attach as-of token/mint state to pool decision rows.

    Mint properties are exposed as model features, not converted into economic
    safe/unsafe labels or hand-written allocation rules. Missing evidence stays
    missing and is counted in the returned report.
    """
    required = {"pool_address", "decision_observed_at"}
    missing = sorted(required - set(decision_frame.columns))
    if missing:
        raise ValueError(
            f"missing mint-context decision columns: {missing}"
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
    rows_with_chain = 0
    rows_with_both = 0
    missing_chain = 0
    missing_x = 0
    missing_y = 0
    feature_rows: list[dict[str, float]] = []

    for row in frame.itertuples(index=False):
        decision_time = pd.Timestamp(
            getattr(row, "decision_observed_at")
        )
        pool_address = str(getattr(row, "pool_address"))
        as_of = decision_time.isoformat()

        pool = store.latest_chain_pool_snapshot(
            pool_address,
            as_of=as_of,
        )
        if pool is None:
            missing_chain += 1
            empty = {
                column: np.nan
                for column in TOKEN_MINT_CONTEXT_FEATURE_COLUMNS
            }
            feature_rows.append(empty)
            continue

        rows_with_chain += 1
        chain_observed = _parse_time(pool["observed_at"])
        chain_age = float(
            (decision_time - chain_observed).total_seconds()
        )
        if chain_age < 0:
            raise AssertionError(
                "as-of chain pool lookup returned a future snapshot"
            )

        x_history = store.mint_snapshot_history(
            str(pool["token_x_mint"]),
            as_of=as_of,
        )
        y_history = store.mint_snapshot_history(
            str(pool["token_y_mint"]),
            as_of=as_of,
        )
        x_mint = x_history[-1] if x_history else None
        y_mint = y_history[-1] if y_history else None
        if x_mint is None:
            missing_x += 1
        if y_mint is None:
            missing_y += 1
        if x_mint is not None and y_mint is not None:
            rows_with_both += 1

        features = {
            "mint_chain_snapshot_age_seconds": chain_age,
        }
        features.update(
            _mint_features(
                x_mint,
                decision_time=decision_time,
                expected_program=(
                    str(pool["token_x_program"])
                    if pool.get("token_x_program") is not None
                    else None
                ),
                prefix="mint_x",
            )
        )
        features.update(
            _mint_features(
                y_mint,
                decision_time=decision_time,
                expected_program=(
                    str(pool["token_y_program"])
                    if pool.get("token_y_program") is not None
                    else None
                ),
                prefix="mint_y",
            )
        )
        features.update(
            _mint_history_features(
                x_history,
                decision_time=decision_time,
                prefix="mint_x",
            )
        )
        features.update(
            _mint_history_features(
                y_history,
                decision_time=decision_time,
                prefix="mint_y",
            )
        )
        feature_rows.append(features)

    features = pd.DataFrame(
        feature_rows,
        index=frame.index,
    )
    enriched = pd.concat([frame, features], axis=1)

    report = TokenMintContextReport(
        rows_seen=len(frame),
        rows_with_chain_pool_state=rows_with_chain,
        rows_with_both_mints=rows_with_both,
        rows_missing_chain_pool_state=missing_chain,
        rows_missing_mint_x=missing_x,
        rows_missing_mint_y=missing_y,
    )
    return enriched, report
