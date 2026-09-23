from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class LiveExecutionLedgerAudit:
    receipts: int
    execution_effects: int
    closure_proofs: int
    live_positions: int
    closed_positions: int
    atomic_outcomes: int
    valued_outcomes: int
    learning_labels: int
    unprocessed_receipt_decisions: tuple[str, ...]
    unapplied_position_effect_decisions: tuple[str, ...]
    closure_without_position_event: tuple[str, ...]
    closed_positions_missing_outcome: tuple[str, ...]
    valued_outcomes_missing_evidence: tuple[str, ...]
    orphan_valuation_positions: tuple[str, ...]
    valued_positions_missing_learning_label: tuple[str, ...]
    orphan_learning_label_positions: tuple[str, ...]
    clean: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def audit_live_execution_ledger(
    storage: Storage,
) -> LiveExecutionLedgerAudit:
    with storage.connect() as conn:
        receipts = conn.execute(
            """
            SELECT decision_id
            FROM live_execution_receipts
            ORDER BY decision_id
            """
        ).fetchall()
        effects = conn.execute(
            """
            SELECT decision_id, position_address
            FROM live_execution_effects
            ORDER BY decision_id
            """
        ).fetchall()
        closures = conn.execute(
            """
            SELECT decision_id, position_address
            FROM live_position_closure_proofs
            WHERE closed = 1
            ORDER BY decision_id
            """
        ).fetchall()
        position_events = conn.execute(
            """
            SELECT decision_id
            FROM live_position_events
            ORDER BY decision_id
            """
        ).fetchall()
        positions = conn.execute(
            """
            SELECT position_address, status
            FROM live_positions
            ORDER BY position_address
            """
        ).fetchall()
        outcomes = conn.execute(
            """
            SELECT position_address, label_status
            FROM live_position_outcomes
            ORDER BY position_address
            """
        ).fetchall()
        valuations = conn.execute(
            """
            SELECT position_address
            FROM live_position_valuations
            ORDER BY position_address
            """
        ).fetchall()
        learning_labels = conn.execute(
            """
            SELECT position_address
            FROM live_learning_labels
            ORDER BY position_address
            """
        ).fetchall()

    receipt_ids = {str(row[0]) for row in receipts}
    effect_map = {
        str(row[0]): (str(row[1]) if row[1] is not None else None)
        for row in effects
    }
    closure_map = {str(row[0]): str(row[1]) for row in closures}
    event_ids = {str(row[0]) for row in position_events}
    outcome_positions = {str(row[0]) for row in outcomes}
    valued_positions = {
        str(row[0]) for row in outcomes if str(row[1]) == "VALUED"
    }
    valuation_positions = {str(row[0]) for row in valuations}
    learning_label_positions = {
        str(row[0]) for row in learning_labels
    }

    unprocessed = tuple(
        sorted(
            decision_id
            for decision_id in receipt_ids
            if decision_id not in effect_map
            and decision_id not in closure_map
        )
    )
    unapplied_effects = tuple(
        sorted(
            decision_id
            for decision_id, position in effect_map.items()
            if position is not None and decision_id not in event_ids
        )
    )
    closure_without_event = tuple(
        sorted(
            decision_id
            for decision_id in closure_map
            if decision_id not in event_ids
        )
    )
    closed_positions = [
        str(row[0]) for row in positions if str(row[1]) == "CLOSED"
    ]
    missing_outcome = tuple(
        sorted(
            position
            for position in closed_positions
            if position not in outcome_positions
        )
    )

    valued_missing_evidence = tuple(
        sorted(valued_positions - valuation_positions)
    )
    orphan_valuations = tuple(
        sorted(valuation_positions - valued_positions)
    )
    valued_missing_label = tuple(
        sorted(valued_positions - learning_label_positions)
    )
    orphan_labels = tuple(
        sorted(learning_label_positions - valued_positions)
    )

    clean = not (
        unprocessed
        or unapplied_effects
        or closure_without_event
        or missing_outcome
        or valued_missing_evidence
        or orphan_valuations
        or valued_missing_label
        or orphan_labels
    )

    return LiveExecutionLedgerAudit(
        receipts=len(receipts),
        execution_effects=len(effects),
        closure_proofs=len(closures),
        live_positions=len(positions),
        closed_positions=len(closed_positions),
        atomic_outcomes=len(outcomes),
        valued_outcomes=len(valued_positions),
        learning_labels=len(learning_labels),
        unprocessed_receipt_decisions=unprocessed,
        unapplied_position_effect_decisions=unapplied_effects,
        closure_without_position_event=closure_without_event,
        closed_positions_missing_outcome=missing_outcome,
        valued_outcomes_missing_evidence=valued_missing_evidence,
        orphan_valuation_positions=orphan_valuations,
        valued_positions_missing_learning_label=valued_missing_label,
        orphan_learning_label_positions=orphan_labels,
        clean=clean,
    )
