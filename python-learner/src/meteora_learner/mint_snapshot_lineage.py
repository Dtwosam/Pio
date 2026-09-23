from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence


POOL_SOURCE_COLUMNS = (
    "id",
    "observed_at",
    "pool_address",
    "active_bin_id",
    "bin_step",
    "token_x_mint",
    "token_y_mint",
    "token_x_program",
    "token_y_program",
    "base_fee_rate",
    "variable_fee_rate",
    "total_fee_rate",
    "deposit_total_fee_rate",
    "protocol_share_bps",
    "collect_fee_mode",
    "supports_limit_order",
    "reward_mint_0",
    "reward_mint_1",
    "reward_rate_0",
    "reward_rate_1",
    "reward_duration_end_0",
    "reward_duration_end_1",
    "reward_last_update_time_0",
    "reward_last_update_time_1",
    "raw_json",
)

MINT_SOURCE_COLUMNS = (
    "id",
    "observed_at",
    "mint_address",
    "token_program",
    "capture_slot_start",
    "capture_slot_end",
    "supply",
    "decimals",
    "is_initialized",
    "mint_authority",
    "freeze_authority",
    "data_len",
    "token_2022_extension_data_len",
    "has_token_2022_extension_data",
    "raw_json",
)


def _record(
    row: Sequence[Any],
    columns: tuple[str, ...],
) -> dict[str, Any]:
    if len(row) != len(columns):
        raise ValueError(
            f"lineage row has {len(row)} columns; expected {len(columns)}"
        )
    return {
        column: row[index]
        for index, column in enumerate(columns)
    }


def mint_risk_pool_source_record(
    row: Sequence[Any],
) -> dict[str, Any]:
    return _record(row, POOL_SOURCE_COLUMNS)


def mint_risk_mint_source_record(
    row: Sequence[Any],
) -> dict[str, Any]:
    return _record(row, MINT_SOURCE_COLUMNS)


def mint_risk_source_sha256(record: dict[str, Any]) -> str:
    canonical = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
