from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .storage import Storage


REQUIRED_PHASE9_IMMUTABILITY_TRIGGERS = (
    (
        "chain_pool_snapshots_no_update",
        "chain_pool_snapshots",
        "UPDATE",
    ),
    (
        "chain_pool_snapshots_no_delete",
        "chain_pool_snapshots",
        "DELETE",
    ),
    (
        "token_mint_snapshots_no_update",
        "token_mint_snapshots",
        "UPDATE",
    ),
    (
        "token_mint_snapshots_no_delete",
        "token_mint_snapshots",
        "DELETE",
    ),
    (
        "bin_liquidity_snapshots_no_update",
        "bin_liquidity_snapshots",
        "UPDATE",
    ),
    (
        "bin_liquidity_snapshots_no_delete",
        "bin_liquidity_snapshots",
        "DELETE",
    ),
    (
        "position_event_history_no_update",
        "position_event_history",
        "UPDATE",
    ),
    (
        "position_event_history_no_delete",
        "position_event_history",
        "DELETE",
    ),
    (
        "advanced_edge_evidence_no_update",
        "advanced_edge_evidence",
        "UPDATE",
    ),
    (
        "advanced_edge_evidence_no_delete",
        "advanced_edge_evidence",
        "DELETE",
    ),
    (
        "model_live_evidence_no_update",
        "model_live_evidence",
        "UPDATE",
    ),
    (
        "model_live_evidence_no_delete",
        "model_live_evidence",
        "DELETE",
    ),
    (
        "phase_promotion_history_no_update",
        "phase_promotion_evidence_history",
        "UPDATE",
    ),
    (
        "phase_promotion_history_no_delete",
        "phase_promotion_evidence_history",
        "DELETE",
    ),
)


@dataclass(frozen=True)
class Phase9StorageTriggerCheck:
    trigger_name: str
    expected_table: str
    expected_operation: str
    present: bool
    table_matches: bool
    operation_matches: bool
    fail_closed_raise_present: bool

    @property
    def verified(self) -> bool:
        return (
            self.present
            and self.table_matches
            and self.operation_matches
            and self.fail_closed_raise_present
        )

    def to_record(self) -> dict[str, Any]:
        return asdict(self) | {"verified": self.verified}


@dataclass(frozen=True)
class Phase9StorageIntegrityReport:
    verified: bool
    required_trigger_count: int
    verified_trigger_count: int
    checks: tuple[Phase9StorageTriggerCheck, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "required_trigger_count": self.required_trigger_count,
            "verified_trigger_count": self.verified_trigger_count,
            "checks": [item.to_record() for item in self.checks],
            "reasons": list(self.reasons),
        }


def evaluate_phase9_storage_integrity(
    storage: Storage,
) -> Phase9StorageIntegrityReport:
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT name, tbl_name, sql
            FROM sqlite_master
            WHERE type = 'trigger'
            """
        ).fetchall()

    triggers = {
        str(row[0]): {
            "table": str(row[1]),
            "sql": str(row[2] or ""),
        }
        for row in rows
    }

    checks: list[Phase9StorageTriggerCheck] = []
    reasons: list[str] = []

    for name, expected_table, operation in (
        REQUIRED_PHASE9_IMMUTABILITY_TRIGGERS
    ):
        raw = triggers.get(name)
        present = raw is not None
        table_matches = bool(
            raw is not None
            and raw["table"] == expected_table
        )
        normalized_sql = (
            raw["sql"].upper()
            if raw is not None
            else ""
        )
        operation_matches = (
            f"BEFORE {operation} ON {expected_table}".upper()
            in normalized_sql
        )
        fail_closed_raise_present = (
            "RAISE(ABORT" in normalized_sql
            and "IMMUTABLE" in normalized_sql
        )
        check = Phase9StorageTriggerCheck(
            trigger_name=name,
            expected_table=expected_table,
            expected_operation=operation,
            present=present,
            table_matches=table_matches,
            operation_matches=operation_matches,
            fail_closed_raise_present=fail_closed_raise_present,
        )
        checks.append(check)
        if not check.verified:
            reasons.append(
                f"immutability trigger {name} is missing or malformed "
                f"for {expected_table} {operation}"
            )

    verified_count = sum(item.verified for item in checks)
    return Phase9StorageIntegrityReport(
        verified=not reasons,
        required_trigger_count=len(checks),
        verified_trigger_count=verified_count,
        checks=tuple(checks),
        reasons=tuple(reasons),
    )
