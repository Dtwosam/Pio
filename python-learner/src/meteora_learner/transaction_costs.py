from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import mean, median
from typing import Any

from .research_store import ResearchStore


@dataclass(frozen=True)
class TransactionCostSample:
    signature: str
    event_types: tuple[str, ...]
    network_fee_lamports: int | None
    compute_units_consumed: int | None
    succeeded: bool | None


@dataclass(frozen=True)
class TransactionCostReport:
    position_address: str
    transactions_seen: int
    receipts_available: int
    successful_receipts: int
    fee_samples: int
    compute_unit_samples: int
    total_network_fee_lamports: int
    mean_network_fee_lamports: float | None
    median_network_fee_lamports: float | None
    p95_network_fee_lamports: int | None
    min_network_fee_lamports: int | None
    max_network_fee_lamports: int | None
    mean_compute_units: float | None
    median_compute_units: float | None
    p95_compute_units: int | None
    missing_receipt_signatures: tuple[str, ...]
    samples: tuple[TransactionCostSample, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def build_transaction_cost_report(
    database_path: str,
    *,
    position_address: str,
) -> TransactionCostReport:
    """
    Summarize real Solana receipt costs for a position's lifecycle transactions.

    Position-history rows are deduplicated by transaction signature so one
    transaction is never charged twice merely because it emitted multiple
    lifecycle/API events.
    """
    store = ResearchStore(database_path)
    history = store.load_position_history_events(position_address)
    if not history:
        raise ValueError(f"no stored position history for {position_address}")

    event_types_by_signature: dict[str, set[str]] = {}
    for row in history:
        signature = str(row["signature"])
        event_types_by_signature.setdefault(signature, set()).add(
            str(row["event_type"])
        )

    samples: list[TransactionCostSample] = []
    missing: list[str] = []
    for signature, event_types in event_types_by_signature.items():
        receipt = store.transaction_snapshot(signature)
        if receipt is None:
            missing.append(signature)
            samples.append(
                TransactionCostSample(
                    signature=signature,
                    event_types=tuple(sorted(event_types)),
                    network_fee_lamports=None,
                    compute_units_consumed=None,
                    succeeded=None,
                )
            )
            continue

        succeeded_raw = receipt.get("succeeded")
        samples.append(
            TransactionCostSample(
                signature=signature,
                event_types=tuple(sorted(event_types)),
                network_fee_lamports=(
                    int(receipt["network_fee_lamports"])
                    if receipt.get("network_fee_lamports") is not None
                    else None
                ),
                compute_units_consumed=(
                    int(receipt["compute_units_consumed"])
                    if receipt.get("compute_units_consumed") is not None
                    else None
                ),
                succeeded=(
                    bool(succeeded_raw)
                    if succeeded_raw is not None
                    else None
                ),
            )
        )

    fees = [
        item.network_fee_lamports
        for item in samples
        if item.network_fee_lamports is not None
    ]
    compute_units = [
        item.compute_units_consumed
        for item in samples
        if item.compute_units_consumed is not None
    ]

    return TransactionCostReport(
        position_address=position_address,
        transactions_seen=len(samples),
        receipts_available=len(samples) - len(missing),
        successful_receipts=sum(item.succeeded is True for item in samples),
        fee_samples=len(fees),
        compute_unit_samples=len(compute_units),
        total_network_fee_lamports=sum(fees),
        mean_network_fee_lamports=float(mean(fees)) if fees else None,
        median_network_fee_lamports=float(median(fees)) if fees else None,
        p95_network_fee_lamports=_p95(fees),
        min_network_fee_lamports=min(fees) if fees else None,
        max_network_fee_lamports=max(fees) if fees else None,
        mean_compute_units=float(mean(compute_units)) if compute_units else None,
        median_compute_units=float(median(compute_units)) if compute_units else None,
        p95_compute_units=_p95(compute_units),
        missing_receipt_signatures=tuple(sorted(missing)),
        samples=tuple(samples),
    )
