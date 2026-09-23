from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class Phase9PoolActivityScanState:
    pool_address: str
    backfill_before_signature: str | None
    backfill_exhausted: bool
    pages_scanned: int
    signatures_scanned: int
    matching_transactions: int
    positions_discovered: int
    updated_at: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def phase9_pool_activity_scan_state(
    storage: Storage,
    *,
    pool_address: str,
) -> Phase9PoolActivityScanState:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT backfill_before_signature, backfill_exhausted,
                   pages_scanned, signatures_scanned,
                   matching_transactions, positions_discovered,
                   updated_at
            FROM phase9_pool_activity_scan_state
            WHERE pool_address = ?
            """,
            (pool_address,),
        ).fetchone()
    if row is None:
        return Phase9PoolActivityScanState(
            pool_address=pool_address,
            backfill_before_signature=None,
            backfill_exhausted=False,
            pages_scanned=0,
            signatures_scanned=0,
            matching_transactions=0,
            positions_discovered=0,
            updated_at=None,
        )
    return Phase9PoolActivityScanState(
        pool_address=pool_address,
        backfill_before_signature=(
            str(row[0]) if row[0] is not None else None
        ),
        backfill_exhausted=bool(row[1]),
        pages_scanned=int(row[2]),
        signatures_scanned=int(row[3]),
        matching_transactions=int(row[4]),
        positions_discovered=int(row[5]),
        updated_at=str(row[6]),
    )


def record_phase9_pool_activity_page(
    storage: Storage,
    *,
    pool_address: str,
    next_before_signature: str | None,
    has_more: bool,
    signatures_scanned: int,
    matching_transactions: int,
    positions_discovered: int,
) -> Phase9PoolActivityScanState:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if signatures_scanned < 0:
        raise ValueError("signatures_scanned cannot be negative")
    if not 0 <= matching_transactions <= signatures_scanned:
        raise ValueError(
            "matching_transactions must fit signatures_scanned"
        )
    if positions_discovered < 0:
        raise ValueError("positions_discovered cannot be negative")
    if has_more and not next_before_signature:
        raise ValueError("has_more requires next_before_signature")

    updated_at = utc_now_iso()
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT pages_scanned, signatures_scanned,
                   matching_transactions, positions_discovered
            FROM phase9_pool_activity_scan_state
            WHERE pool_address = ?
            """,
            (pool_address,),
        ).fetchone()
        pages = int(row[0]) if row is not None else 0
        signatures = int(row[1]) if row is not None else 0
        matches = int(row[2]) if row is not None else 0
        positions = int(row[3]) if row is not None else 0
        conn.execute(
            """
            INSERT INTO phase9_pool_activity_scan_state(
                pool_address, backfill_before_signature,
                backfill_exhausted, pages_scanned,
                signatures_scanned, matching_transactions,
                positions_discovered, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pool_address) DO UPDATE SET
                backfill_before_signature =
                    excluded.backfill_before_signature,
                backfill_exhausted = excluded.backfill_exhausted,
                pages_scanned = excluded.pages_scanned,
                signatures_scanned = excluded.signatures_scanned,
                matching_transactions =
                    excluded.matching_transactions,
                positions_discovered =
                    excluded.positions_discovered,
                updated_at = excluded.updated_at
            """,
            (
                pool_address,
                next_before_signature if has_more else None,
                0 if has_more else 1,
                pages + 1,
                signatures + signatures_scanned,
                matches + matching_transactions,
                positions + positions_discovered,
                updated_at,
            ),
        )
    return phase9_pool_activity_scan_state(
        storage,
        pool_address=pool_address,
    )
