from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .research_store import ResearchStore
from .storage import Storage


@dataclass(frozen=True)
class RotationTokenFlowGap:
    signature: str
    slot: int
    block_time: int | None
    pool_addresses: tuple[str, ...]
    position_addresses: tuple[str, ...]
    receipt_present: bool
    token_flow_captured: bool
    token_delta_rows: int
    reason: str


@dataclass(frozen=True)
class RotationTokenFlowGapReport:
    pool_address: str | None
    rebalance_transactions_seen: int
    receipt_present_transactions: int
    receipt_missing_transactions: int
    token_flow_captured_transactions: int
    token_flow_missing_transactions: int
    token_delta_rows: int
    gaps: tuple[RotationTokenFlowGap, ...]
    read_only: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_rotation_token_flow_gap_report(
    storage: Storage,
    *,
    pool_address: str | None = None,
) -> RotationTokenFlowGapReport:
    """
    Report rebalance transactions that still lack trustworthy token-flow capture.

    This is a data-provenance report only. It assigns no economic meaning to
    observed token deltas and performs no transaction reinspection itself.
    """
    if pool_address is not None and not pool_address.strip():
        raise ValueError("pool_address cannot be blank")

    store = ResearchStore(str(storage.path))
    rows = store.rebalance_token_flow_capture_rows(
        pool_address=pool_address,
    )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["signature"]), []).append(row)

    gaps: list[RotationTokenFlowGap] = []
    receipt_present = 0
    receipt_missing = 0
    captured = 0
    missing_capture = 0
    total_delta_rows = 0

    for signature, tx_rows in grouped.items():
        first = tx_rows[0]
        receipt = bool(int(first["receipt_present"]))
        token_flow = bool(
            int(first["token_balance_deltas_captured"])
        )
        delta_rows = int(first["token_delta_rows"])
        total_delta_rows += delta_rows

        if receipt:
            receipt_present += 1
        else:
            receipt_missing += 1

        if token_flow:
            captured += 1
        else:
            missing_capture += 1

        if receipt and token_flow:
            continue

        reason = (
            "TRANSACTION_RECEIPT_MISSING"
            if not receipt
            else "TOKEN_FLOW_CAPTURE_MISSING"
        )
        gaps.append(
            RotationTokenFlowGap(
                signature=signature,
                slot=min(int(row["slot"]) for row in tx_rows),
                block_time=(
                    int(first["block_time"])
                    if first.get("block_time") is not None
                    else None
                ),
                pool_addresses=tuple(sorted({
                    str(row["pool_address"])
                    for row in tx_rows
                    if row.get("pool_address") is not None
                })),
                position_addresses=tuple(sorted({
                    str(row["position_address"])
                    for row in tx_rows
                    if row.get("position_address") is not None
                })),
                receipt_present=receipt,
                token_flow_captured=token_flow,
                token_delta_rows=delta_rows,
                reason=reason,
            )
        )

    return RotationTokenFlowGapReport(
        pool_address=pool_address,
        rebalance_transactions_seen=len(grouped),
        receipt_present_transactions=receipt_present,
        receipt_missing_transactions=receipt_missing,
        token_flow_captured_transactions=captured,
        token_flow_missing_transactions=missing_capture,
        token_delta_rows=total_delta_rows,
        gaps=tuple(gaps),
        read_only=True,
    )
