from __future__ import annotations

import pandas as pd
import pytest

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.mint_risk import TOKEN_2022_PROGRAM
from meteora_learner.pool_token_mint_context import (
    attach_token_mint_context_from_store,
)
from meteora_learner.storage import Storage


def _save_pool(
    storage: Storage,
    observed_at: str,
    *,
    x_program: str = STANDARD_SPL_TOKEN_PROGRAM,
    y_program: str = TOKEN_2022_PROGRAM,
) -> None:
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "POOL",
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": "MINT_X",
            "token_y_mint": "MINT_Y",
            "token_x_program": x_program,
            "token_y_program": y_program,
            "base_fee_rate": "0",
            "variable_fee_rate": "0",
            "total_fee_rate": "0",
            "deposit_total_fee_rate": "0",
            "protocol_share_bps": 0,
            "collect_fee_mode": 0,
            "supports_limit_order": True,
            "bin_arrays": [],
        },
        observed_at=observed_at,
    )


def _save_mint(
    storage: Storage,
    mint: str,
    observed_at: str,
    *,
    program: str,
    supply: str,
    mint_authority=None,
    freeze_authority=None,
    extension_len: int = 0,
) -> None:
    storage.save_token_mint_snapshot(
        {
            "mint_address": mint,
            "token_program": program,
            "capture_slot_start": 100,
            "capture_slot_end": 101,
            "supply": supply,
            "decimals": 6,
            "is_initialized": True,
            "mint_authority": mint_authority,
            "freeze_authority": freeze_authority,
            "data_len": 82 + extension_len,
            "token_2022_extension_data_len": extension_len,
            "has_token_2022_extension_data": extension_len > 0,
        },
        observed_at=observed_at,
    )


def _seed(storage: Storage) -> None:
    _save_pool(
        storage,
        "2026-01-01T04:00:00+00:00",
    )
    _save_mint(
        storage,
        "MINT_X",
        "2026-01-01T04:30:00+00:00",
        program=STANDARD_SPL_TOKEN_PROGRAM,
        supply="1000000000000",
    )
    _save_mint(
        storage,
        "MINT_Y",
        "2026-01-01T04:45:00+00:00",
        program=TOKEN_2022_PROGRAM,
        supply="500000000000",
        mint_authority="AUTH",
        freeze_authority="FREEZE",
        extension_len=20,
    )


def test_mint_context_attaches_latest_prior_state(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T05:00:00Z",
            }
        ]
    )

    enriched, report = attach_token_mint_context_from_store(
        str(storage.path),
        decisions,
    )
    row = enriched.iloc[0]

    assert row["mint_chain_snapshot_age_seconds"] == 3600.0
    assert row["mint_x_snapshot_age_seconds"] == 1800.0
    assert row["mint_y_snapshot_age_seconds"] == 900.0
    assert row["mint_x_mint_authority_active"] == 0.0
    assert row["mint_y_mint_authority_active"] == 1.0
    assert row["mint_y_freeze_authority_active"] == 1.0
    assert row["mint_y_program_token2022"] == 1.0
    assert row["mint_y_has_token2022_extension_data"] == 1.0
    assert row["mint_x_program_matches_pool"] == 1.0
    assert row["mint_y_program_matches_pool"] == 1.0
    assert row["mint_x_observation_count"] == 1.0
    assert row["mint_x_has_previous_snapshot"] == 0.0
    assert row["mint_x_seconds_since_previous_snapshot"] == 0.0
    assert row["mint_x_log10_supply_change_since_previous"] == 0.0
    assert row["mint_x_mint_authority_change_count"] == 0.0
    assert report.rows_with_both_mints == 1


def test_future_mint_snapshot_cannot_leak_backward(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)
    _save_mint(
        storage,
        "MINT_X",
        "2026-01-01T06:00:00+00:00",
        program=STANDARD_SPL_TOKEN_PROGRAM,
        supply="999999999999999",
        mint_authority="FUTURE_AUTH",
    )

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T05:00:00Z",
            }
        ]
    )

    enriched, _ = attach_token_mint_context_from_store(
        str(storage.path),
        decisions,
    )

    assert enriched.iloc[0]["mint_x_mint_authority_active"] == 0.0
    assert enriched.iloc[0]["mint_x_snapshot_age_seconds"] == 1800.0


def test_missing_chain_state_is_retained_as_missing_context(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    decisions = pd.DataFrame(
        [
            {
                "pool_address": "UNKNOWN",
                "decision_observed_at": "2026-01-01T05:00:00Z",
            }
        ]
    )

    enriched, report = attach_token_mint_context_from_store(
        str(storage.path),
        decisions,
    )

    assert pd.isna(
        enriched.iloc[0]["mint_chain_snapshot_age_seconds"]
    )
    assert report.rows_missing_chain_pool_state == 1


def test_program_mismatch_is_feature_not_hard_rejection(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    _save_pool(
        storage,
        "2026-01-01T04:00:00+00:00",
        x_program=TOKEN_2022_PROGRAM,
    )
    _save_mint(
        storage,
        "MINT_X",
        "2026-01-01T04:30:00+00:00",
        program=STANDARD_SPL_TOKEN_PROGRAM,
        supply="1000000000000",
    )
    _save_mint(
        storage,
        "MINT_Y",
        "2026-01-01T04:30:00+00:00",
        program=TOKEN_2022_PROGRAM,
        supply="1000000000000",
    )

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T05:00:00Z",
            }
        ]
    )

    enriched, report = attach_token_mint_context_from_store(
        str(storage.path),
        decisions,
    )

    assert len(enriched) == 1
    assert enriched.iloc[0]["mint_x_program_matches_pool"] == 0.0
    assert report.rows_with_both_mints == 1


def test_mint_context_exposes_longitudinal_changes_as_of_decision(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)
    _save_mint(
        storage,
        "MINT_X",
        "2026-01-01T05:30:00+00:00",
        program=STANDARD_SPL_TOKEN_PROGRAM,
        supply="2000000000000",
        mint_authority="NEW_AUTH",
        extension_len=0,
    )
    _save_mint(
        storage,
        "MINT_X",
        "2026-01-01T06:30:00+00:00",
        program=STANDARD_SPL_TOKEN_PROGRAM,
        supply="4000000000000",
        mint_authority=None,
        extension_len=0,
    )

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T06:00:00Z",
            }
        ]
    )

    enriched, _ = attach_token_mint_context_from_store(
        str(storage.path),
        decisions,
    )
    row = enriched.iloc[0]

    assert row["mint_x_observation_count"] == 2.0
    assert row["mint_x_has_previous_snapshot"] == 1.0
    assert row["mint_x_seconds_since_previous_snapshot"] == 3600.0
    assert row["mint_x_log10_supply_change_since_previous"] > 0.0
    assert row["mint_x_log10_supply_change_since_first"] > 0.0
    assert row["mint_x_mint_authority_changed_since_previous"] == 1.0
    assert row["mint_x_mint_authority_change_count"] == 1.0

    # The 06:30 snapshot is in the future relative to the decision and must
    # not affect the as-of features.
    assert row["mint_x_observation_count"] == 2.0
    assert row["mint_x_mint_authority_active"] == 1.0


def test_future_mint_history_change_cannot_alter_prior_features(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    _seed(storage)

    decisions = pd.DataFrame(
        [
            {
                "pool_address": "POOL",
                "decision_observed_at": "2026-01-01T05:00:00Z",
            }
        ]
    )

    before, _ = attach_token_mint_context_from_store(
        str(storage.path),
        decisions,
    )

    _save_mint(
        storage,
        "MINT_X",
        "2026-01-01T07:00:00+00:00",
        program=STANDARD_SPL_TOKEN_PROGRAM,
        supply="999999999999999",
        mint_authority="FUTURE_AUTH",
        freeze_authority="FUTURE_FREEZE",
        extension_len=0,
    )

    after, _ = attach_token_mint_context_from_store(
        str(storage.path),
        decisions,
    )

    dynamic_columns = [
        "mint_x_observation_count",
        "mint_x_has_previous_snapshot",
        "mint_x_seconds_since_previous_snapshot",
        "mint_x_log10_supply_change_since_previous",
        "mint_x_log10_supply_change_since_first",
        "mint_x_mint_authority_changed_since_previous",
        "mint_x_freeze_authority_changed_since_previous",
        "mint_x_mint_authority_change_count",
        "mint_x_freeze_authority_change_count",
    ]
    for column in dynamic_columns:
        assert before.iloc[0][column] == pytest.approx(
            after.iloc[0][column]
        )
