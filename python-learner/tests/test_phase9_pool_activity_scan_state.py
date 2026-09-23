import pytest

from meteora_learner.phase9_pool_activity_scan_state import (
    phase9_pool_activity_scan_state,
    record_phase9_pool_activity_page,
)
from meteora_learner.storage import Storage


def test_pool_activity_scan_state_advances_and_exhausts(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    empty = phase9_pool_activity_scan_state(
        storage,
        pool_address="pool-a",
    )
    assert empty.backfill_before_signature is None
    assert empty.backfill_exhausted is False
    assert empty.pages_scanned == 0

    first = record_phase9_pool_activity_page(
        storage,
        pool_address="pool-a",
        next_before_signature="sig-100",
        has_more=True,
        signatures_scanned=25,
        matching_transactions=4,
        positions_discovered=3,
    )
    assert first.backfill_before_signature == "sig-100"
    assert first.backfill_exhausted is False
    assert first.pages_scanned == 1
    assert first.signatures_scanned == 25
    assert first.matching_transactions == 4
    assert first.positions_discovered == 3

    terminal = record_phase9_pool_activity_page(
        storage,
        pool_address="pool-a",
        next_before_signature="sig-125",
        has_more=False,
        signatures_scanned=12,
        matching_transactions=2,
        positions_discovered=1,
    )
    assert terminal.backfill_before_signature is None
    assert terminal.backfill_exhausted is True
    assert terminal.pages_scanned == 2
    assert terminal.signatures_scanned == 37
    assert terminal.matching_transactions == 6
    assert terminal.positions_discovered == 4


def test_pool_activity_scan_state_rejects_invalid_page_counts(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="next_before_signature"):
        record_phase9_pool_activity_page(
            storage,
            pool_address="pool-a",
            next_before_signature=None,
            has_more=True,
            signatures_scanned=25,
            matching_transactions=1,
            positions_discovered=1,
        )

    with pytest.raises(ValueError, match="matching_transactions"):
        record_phase9_pool_activity_page(
            storage,
            pool_address="pool-a",
            next_before_signature=None,
            has_more=False,
            signatures_scanned=2,
            matching_transactions=3,
            positions_discovered=1,
        )


def test_pool_activity_scan_state_is_pool_scoped(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    record_phase9_pool_activity_page(
        storage,
        pool_address="pool-a",
        next_before_signature="sig-a",
        has_more=True,
        signatures_scanned=10,
        matching_transactions=1,
        positions_discovered=1,
    )

    other = phase9_pool_activity_scan_state(
        storage,
        pool_address="pool-b",
    )
    assert other.pages_scanned == 0
    assert other.backfill_before_signature is None
