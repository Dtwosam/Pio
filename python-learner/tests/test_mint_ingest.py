import pytest

from meteora_learner.mint_ingest import ingest_mint_snapshot
from meteora_learner.storage import Storage


SPL = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def snapshot(**overrides):
    value = {
        "mint_address": "mint",
        "token_program": SPL,
        "capture_slot_start": 100,
        "capture_slot_end": 101,
        "supply": "1000000",
        "decimals": 6,
        "is_initialized": True,
        "mint_authority": None,
        "freeze_authority": None,
        "data_len": 82,
        "token_2022_extension_data_len": 0,
        "has_token_2022_extension_data": False,
    }
    value.update(overrides)
    return value


def test_mint_snapshot_ingest_persists_authoritative_fields(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    result = ingest_mint_snapshot(
        storage,
        snapshot(),
        observed_at="2026-09-23T10:00:00+00:00",
    )

    assert result.mint_address == "mint"
    assert result.snapshot_id > 0
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT observed_at, token_program,
                   capture_slot_start, capture_slot_end,
                   supply, decimals, is_initialized,
                   mint_authority, freeze_authority,
                   data_len, token_2022_extension_data_len,
                   has_token_2022_extension_data
            FROM token_mint_snapshots
            WHERE id = ?
            """,
            (result.snapshot_id,),
        ).fetchone()

    assert row == (
        "2026-09-23T10:00:00+00:00",
        SPL,
        100,
        101,
        "1000000",
        6,
        1,
        None,
        None,
        82,
        0,
        0,
    )


def test_mint_snapshot_ingest_rejects_missing_fields(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    payload = snapshot()
    del payload["capture_slot_end"]

    with pytest.raises(ValueError, match="missing fields"):
        ingest_mint_snapshot(storage, payload)


def test_mint_snapshot_ingest_rejects_reversed_capture_slots(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="capture slot range"):
        ingest_mint_snapshot(
            storage,
            snapshot(
                capture_slot_start=200,
                capture_slot_end=199,
            ),
        )


def test_mint_snapshot_ingest_rejects_invalid_data_lengths(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="data length"):
        ingest_mint_snapshot(
            storage,
            snapshot(data_len=81),
        )

    with pytest.raises(ValueError, match="data length"):
        ingest_mint_snapshot(
            storage,
            snapshot(token_2022_extension_data_len=-1),
        )


def test_mint_snapshot_raw_payload_is_preserved(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    payload = snapshot(extra_field="preserved")
    result = ingest_mint_snapshot(
        storage,
        payload,
        observed_at="2026-09-23T10:00:00+00:00",
    )

    with storage.connect() as conn:
        raw = conn.execute(
            "SELECT raw_json FROM token_mint_snapshots WHERE id = ?",
            (result.snapshot_id,),
        ).fetchone()[0]

    assert '"extra_field":"preserved"' in raw
