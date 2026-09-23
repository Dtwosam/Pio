from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class ExecutionReceiptAuditReport:
    receipts: int
    confirmed_receipts: int
    failed_receipts: int
    reconciled_chain_snapshots: int
    missing_chain_snapshots: tuple[str, ...]
    mismatched_chain_snapshots: tuple[str, ...]
    clean: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def audit_execution_receipts(
    storage: Storage,
) -> ExecutionReceiptAuditReport:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                r.signature,
                r.intent_status,
                r.slot,
                r.network_fee_lamports,
                r.compute_units_consumed,
                r.succeeded,
                s.slot,
                s.network_fee_lamports,
                s.compute_units_consumed,
                s.succeeded
            FROM live_execution_receipts r
            LEFT JOIN chain_transaction_snapshots s
              ON s.signature = r.signature
            ORDER BY r.decision_id ASC
            """
        ).fetchall()

    missing: list[str] = []
    mismatched: list[str] = []
    reconciled = 0
    confirmed = 0
    failed = 0

    for row in rows:
        (
            signature,
            status,
            receipt_slot,
            receipt_fee,
            receipt_compute,
            receipt_succeeded,
            snapshot_slot,
            snapshot_fee,
            snapshot_compute,
            snapshot_succeeded,
        ) = row

        signature = str(signature)
        if status == "CONFIRMED":
            confirmed += 1
        elif status == "FAILED":
            failed += 1

        if snapshot_slot is None:
            missing.append(signature)
            continue

        mismatch = (
            int(snapshot_slot) != int(receipt_slot)
            or snapshot_succeeded is None
            or int(snapshot_succeeded) != int(receipt_succeeded)
        )
        if receipt_fee is not None:
            mismatch = mismatch or snapshot_fee is None or (
                int(snapshot_fee) != int(receipt_fee)
            )
        if receipt_compute is not None:
            mismatch = mismatch or snapshot_compute is None or (
                int(snapshot_compute) != int(receipt_compute)
            )

        if mismatch:
            mismatched.append(signature)
        else:
            reconciled += 1

    return ExecutionReceiptAuditReport(
        receipts=len(rows),
        confirmed_receipts=confirmed,
        failed_receipts=failed,
        reconciled_chain_snapshots=reconciled,
        missing_chain_snapshots=tuple(missing),
        mismatched_chain_snapshots=tuple(mismatched),
        clean=not missing and not mismatched,
    )
