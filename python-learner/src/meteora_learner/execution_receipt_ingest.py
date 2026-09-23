from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class ExecutionReceiptIngestResult:
    decision_id: str
    signature: str
    reused_existing: bool
    chain_snapshot_reconciled: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _required_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_int(payload: dict[str, Any], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return int(value)


def _non_negative_int(payload: dict[str, Any], name: str) -> int:
    value = _optional_int(payload, name)
    if value is None or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _normalize_receipt(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("execution receipt must be a JSON object")

    receipt = {
        "decision_id": _required_string(payload, "decision_id"),
        "signature": _required_string(payload, "signature"),
        "mode": _required_string(payload, "mode"),
        "action": _required_string(payload, "action"),
        "pool_address": _required_string(payload, "pool_address"),
        "intent_status": _required_string(payload, "intent_status"),
        "slot": _non_negative_int(payload, "slot"),
        "block_time": _optional_int(payload, "block_time"),
        "network_fee_lamports": _optional_int(
            payload,
            "network_fee_lamports",
        ),
        "compute_units_consumed": _optional_int(
            payload,
            "compute_units_consumed",
        ),
        "event_count": _non_negative_int(payload, "event_count"),
        "add_request_count": _non_negative_int(
            payload,
            "add_request_count",
        ),
        "rebalance_request_count": _non_negative_int(
            payload,
            "rebalance_request_count",
        ),
    }

    succeeded = payload.get("succeeded")
    if not isinstance(succeeded, bool):
        raise ValueError("succeeded must be a boolean")
    receipt["succeeded"] = succeeded

    if receipt["mode"] != "LIVE":
        raise ValueError("execution receipt mode must be LIVE")
    if receipt["intent_status"] not in {"CONFIRMED", "FAILED"}:
        raise ValueError(
            "execution receipt intent_status must be CONFIRMED or FAILED"
        )
    if receipt["intent_status"] == "CONFIRMED" and not succeeded:
        raise ValueError("CONFIRMED execution receipt must have succeeded=true")
    if receipt["intent_status"] == "FAILED" and succeeded:
        raise ValueError("FAILED execution receipt must have succeeded=false")

    for name in ("network_fee_lamports", "compute_units_consumed"):
        value = receipt[name]
        if value is not None and value < 0:
            raise ValueError(f"{name} cannot be negative")

    return receipt


def ingest_execution_receipt(
    storage: Storage,
    payload: Any,
    *,
    observed_at: str | None = None,
) -> ExecutionReceiptIngestResult:
    receipt = _normalize_receipt(payload)
    canonical = json.dumps(
        receipt,
        sort_keys=True,
        separators=(",", ":"),
    )
    observed_at = observed_at or utc_now_iso()

    with storage.connect() as conn:
        existing = conn.execute(
            """
            SELECT signature, raw_json
            FROM live_execution_receipts
            WHERE decision_id = ?
            """,
            (receipt["decision_id"],),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != receipt["signature"] or str(existing[1]) != canonical:
                raise ValueError(
                    "decision_id already has a different execution receipt"
                )
            snapshot = conn.execute(
                """
                SELECT slot, network_fee_lamports,
                       compute_units_consumed, succeeded
                FROM chain_transaction_snapshots
                WHERE signature = ?
                """,
                (receipt["signature"],),
            ).fetchone()
            reconciled = _snapshot_matches(receipt, snapshot)
            return ExecutionReceiptIngestResult(
                decision_id=receipt["decision_id"],
                signature=receipt["signature"],
                reused_existing=True,
                chain_snapshot_reconciled=reconciled,
            )

        signature_owner = conn.execute(
            """
            SELECT decision_id
            FROM live_execution_receipts
            WHERE signature = ?
            """,
            (receipt["signature"],),
        ).fetchone()
        if signature_owner is not None:
            raise ValueError(
                "transaction signature is already linked to another decision_id"
            )

        snapshot = conn.execute(
            """
            SELECT slot, network_fee_lamports,
                   compute_units_consumed, succeeded
            FROM chain_transaction_snapshots
            WHERE signature = ?
            """,
            (receipt["signature"],),
        ).fetchone()
        reconciled = _snapshot_matches(receipt, snapshot)

        conn.execute(
            """
            INSERT INTO live_execution_receipts(
                decision_id, signature, observed_at, mode, action,
                pool_address, intent_status, slot, block_time,
                network_fee_lamports, compute_units_consumed, succeeded,
                event_count, add_request_count, rebalance_request_count,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt["decision_id"],
                receipt["signature"],
                observed_at,
                receipt["mode"],
                receipt["action"],
                receipt["pool_address"],
                receipt["intent_status"],
                receipt["slot"],
                receipt["block_time"],
                receipt["network_fee_lamports"],
                receipt["compute_units_consumed"],
                int(receipt["succeeded"]),
                receipt["event_count"],
                receipt["add_request_count"],
                receipt["rebalance_request_count"],
                canonical,
            ),
        )

    return ExecutionReceiptIngestResult(
        decision_id=receipt["decision_id"],
        signature=receipt["signature"],
        reused_existing=False,
        chain_snapshot_reconciled=reconciled,
    )


def _snapshot_matches(
    receipt: dict[str, Any],
    snapshot: Any,
) -> bool:
    if snapshot is None:
        return False

    slot, fee, compute_units, succeeded = snapshot
    expected_succeeded = 1 if receipt["succeeded"] else 0
    comparisons = [
        int(slot) == receipt["slot"],
        succeeded is not None and int(succeeded) == expected_succeeded,
    ]
    if receipt["network_fee_lamports"] is not None:
        comparisons.append(
            fee is not None
            and int(fee) == receipt["network_fee_lamports"]
        )
    if receipt["compute_units_consumed"] is not None:
        comparisons.append(
            compute_units is not None
            and int(compute_units) == receipt["compute_units_consumed"]
        )

    if not all(comparisons):
        raise ValueError(
            "execution receipt conflicts with stored chain transaction snapshot"
        )
    return True
