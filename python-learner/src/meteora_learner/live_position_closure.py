from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class LivePositionClosureResult:
    decision_id: str
    signature: str
    position_address: str
    receipt_slot: int
    proof_slot: int
    closed: bool
    reused_existing: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def finalize_live_position_closure(
    storage: Storage,
    *,
    decision_id: str,
    proof: dict[str, Any],
) -> LivePositionClosureResult:
    if not decision_id.strip():
        raise ValueError("decision_id is required")
    position_address = str(proof.get("position") or "").strip()
    if not position_address:
        raise ValueError("closure proof position is required")
    if proof.get("closed") is not True:
        raise ValueError("closure proof must confirm the position is closed")
    proof_slot = int(proof["rpc_context_slot"])

    with storage.connect() as conn:
        receipt = conn.execute(
            """
            SELECT signature, observed_at, action, pool_address,
                   intent_status, slot, succeeded
            FROM live_execution_receipts
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        if receipt is None:
            raise ValueError(
                "position closure requires a stored settlement execution receipt"
            )
        (
            signature,
            observed_at,
            action,
            pool_address,
            intent_status,
            receipt_slot,
            succeeded,
        ) = receipt
        signature = str(signature)
        if str(action) != "EXIT":
            raise ValueError("position closure receipt action must be EXIT")
        if str(intent_status) != "CONFIRMED" or int(succeeded) != 1:
            raise ValueError(
                "position closure requires a CONFIRMED successful receipt"
            )

        snapshot = conn.execute(
            """
            SELECT slot, succeeded
            FROM chain_transaction_snapshots
            WHERE signature = ?
            """,
            (signature,),
        ).fetchone()
        if snapshot is None:
            raise ValueError(
                "position closure requires a stored chain transaction snapshot"
            )
        if int(snapshot[0]) != int(receipt_slot) or snapshot[1] is None:
            raise ValueError(
                "settlement receipt does not reconcile to chain snapshot"
            )
        if int(snapshot[1]) != 1:
            raise ValueError("settlement chain snapshot was not successful")
        if proof_slot < int(receipt_slot):
            raise ValueError(
                "closure proof slot cannot precede settlement receipt slot"
            )

        position = conn.execute(
            """
            SELECT pool_address, status
            FROM live_positions
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if position is None:
            raise ValueError("unknown live position for closure proof")
        if str(position[0]) != str(pool_address):
            raise ValueError(
                "settlement receipt pool differs from live position pool"
            )
        if str(position[1]) not in {"LIQUIDITY_REMOVED", "CLOSED"}:
            raise ValueError(
                "live position must have liquidity removed before closure"
            )

        canonical = json.dumps(
            {
                "decision_id": decision_id,
                "signature": signature,
                "position": position_address,
                "receipt_slot": int(receipt_slot),
                "proof_slot": proof_slot,
                "closed": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = conn.execute(
            """
            SELECT position_address, receipt_slot, proof_slot, closed, raw_json
            FROM live_position_closure_proofs
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        if existing is not None:
            if str(existing[4]) != canonical:
                raise ValueError(
                    "decision_id already has a different closure proof"
                )
            return LivePositionClosureResult(
                decision_id=decision_id,
                signature=signature,
                position_address=position_address,
                receipt_slot=int(existing[1]),
                proof_slot=int(existing[2]),
                closed=bool(existing[3]),
                reused_existing=True,
            )

        signature_owner = conn.execute(
            """
            SELECT decision_id
            FROM live_position_closure_proofs
            WHERE signature = ?
            """,
            (signature,),
        ).fetchone()
        if signature_owner is not None:
            raise ValueError(
                "settlement signature is already linked to another closure proof"
            )

        conn.execute(
            """
            INSERT INTO live_position_closure_proofs(
                decision_id, signature, position_address,
                receipt_slot, proof_slot, observed_at, closed, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                decision_id,
                signature,
                position_address,
                int(receipt_slot),
                proof_slot,
                str(observed_at),
                canonical,
            ),
        )

        if str(position[1]) != "CLOSED":
            updated = conn.execute(
                """
                UPDATE live_positions
                SET status = 'CLOSED',
                    closed_decision_id = ?,
                    closed_signature = ?,
                    last_decision_id = ?,
                    last_signature = ?,
                    last_observed_at = ?,
                    raw_json = ?
                WHERE position_address = ?
                  AND status = 'LIQUIDITY_REMOVED'
                """,
                (
                    decision_id,
                    signature,
                    decision_id,
                    signature,
                    str(observed_at),
                    canonical,
                    position_address,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "live position changed concurrently during closure"
                )

            conn.execute(
                """
                INSERT INTO live_position_events(
                    decision_id, signature, position_address, event_time,
                    action, prior_status, next_status,
                    min_bin_id, max_bin_id, raw_json
                )
                SELECT ?, ?, position_address, ?, 'CLOSE',
                       'LIQUIDITY_REMOVED', 'CLOSED',
                       min_bin_id, max_bin_id, ?
                FROM live_positions
                WHERE position_address = ?
                """,
                (
                    decision_id,
                    signature,
                    str(observed_at),
                    canonical,
                    position_address,
                ),
            )

    return LivePositionClosureResult(
        decision_id=decision_id,
        signature=signature,
        position_address=position_address,
        receipt_slot=int(receipt_slot),
        proof_slot=proof_slot,
        closed=True,
        reused_existing=False,
    )
