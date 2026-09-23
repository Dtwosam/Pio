from meteora_learner.quote_registry import (
    load_fresh_quote_map,
    save_token_quote,
    token_quote_status,
)
from meteora_learner.storage import Storage


def test_quote_registry_persists_and_checks_freshness(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    saved = save_token_quote(
        storage,
        token_mint="mint",
        quote_per_atomic=0.000001,
        source="TEST",
        observed_at="2026-09-23T10:00:00+00:00",
    )
    assert saved.quote_per_atomic == 0.000001

    fresh = token_quote_status(
        storage,
        token_mint="mint",
        max_age_seconds=300,
        as_of="2026-09-23T10:04:00+00:00",
    )
    assert fresh.available is True
    assert fresh.fresh is True
    assert fresh.age_seconds == 240

    stale = token_quote_status(
        storage,
        token_mint="mint",
        max_age_seconds=300,
        as_of="2026-09-23T10:06:00+00:00",
    )
    assert stale.fresh is False
    assert stale.age_seconds == 360


def test_quote_map_excludes_missing_and_stale_quotes(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_token_quote(
        storage,
        token_mint="fresh",
        quote_per_atomic=1.0,
        source="TEST",
        observed_at="2026-09-23T10:00:00+00:00",
    )
    save_token_quote(
        storage,
        token_mint="stale",
        quote_per_atomic=2.0,
        source="TEST",
        observed_at="2026-09-23T09:00:00+00:00",
    )

    quotes, statuses = load_fresh_quote_map(
        storage,
        token_mints=("fresh", "stale", "missing"),
        max_age_seconds=300,
        as_of="2026-09-23T10:02:00+00:00",
    )

    assert quotes == {"fresh": 1.0}
    by_mint = {item.token_mint: item for item in statuses}
    assert by_mint["stale"].fresh is False
    assert by_mint["missing"].available is False


def test_quote_registry_rejects_non_positive_value(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    import pytest
    with pytest.raises(ValueError, match="positive"):
        save_token_quote(
            storage,
            token_mint="mint",
            quote_per_atomic=0.0,
            source="TEST",
        )
