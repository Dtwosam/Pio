from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from .storage import Storage


ZERO = Decimal("0")


def _d(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("paper audit encountered a non-finite value")
    return result


@dataclass(frozen=True)
class PaperPositionAudit:
    position_id: str
    status: str
    passing: bool
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperLedgerAuditReport:
    account_id: str
    passing: bool
    starting_equity_quote: float
    stored_cash_quote: float
    event_derived_cash_quote: float
    cash_drift_quote: float
    positions_checked: int
    position_failures: int
    positions: tuple[PaperPositionAudit, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _latest_mark(conn: Any, position_id: str) -> Decimal | None:
    row = conn.execute(
        """
        SELECT position_mark_quote
        FROM paper_events
        WHERE position_id = ?
          AND event_type IN ('ENTER', 'MARK', 'REBALANCE')
          AND position_mark_quote IS NOT NULL
        ORDER BY event_time DESC, id DESC
        LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    return _d(row[0]) if row is not None else None


def audit_paper_ledger(
    storage: Storage,
    *,
    account_id: str,
) -> PaperLedgerAuditReport:
    if not account_id.strip():
        raise ValueError("account_id is required")

    with storage.connect() as conn:
        account = conn.execute(
            """
            SELECT starting_equity_quote, cash_quote
            FROM paper_accounts
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError(f"unknown paper account: {account_id}")

        cash_event_rows = conn.execute(
            """
            SELECT cash_delta_quote
            FROM paper_events
            WHERE account_id = ?
            ORDER BY event_time ASC, id ASC
            """,
            (account_id,),
        ).fetchall()

        position_rows = conn.execute(
            """
            SELECT position_id, status, entry_cost_quote,
                   current_mark_quote, fee_income_quote,
                   reward_income_quote, rebalance_cost_quote,
                   exit_cost_quote, realized_pnl_quote
            FROM paper_positions
            WHERE account_id = ?
            ORDER BY position_id ASC
            """,
            (account_id,),
        ).fetchall()

        position_reports: list[PaperPositionAudit] = []
        for row in position_rows:
            position_id = str(row[0])
            status = str(row[1])
            reasons: list[str] = []

            event_rows = conn.execute(
                """
                SELECT event_type, fee_delta_quote, reward_delta_quote,
                       cost_quote, realized_pnl_quote
                FROM paper_events
                WHERE account_id = ? AND position_id = ?
                ORDER BY event_time ASC, id ASC
                """,
                (account_id, position_id),
            ).fetchall()

            enter_count = sum(str(event[0]) == "ENTER" for event in event_rows)
            exit_count = sum(str(event[0]) == "EXIT" for event in event_rows)
            fee_total = sum((_d(event[1]) for event in event_rows), ZERO)
            reward_total = sum((_d(event[2]) for event in event_rows), ZERO)
            rebalance_cost = sum(
                (
                    _d(event[3])
                    for event in event_rows
                    if str(event[0]) == "REBALANCE"
                ),
                ZERO,
            )
            exit_cost = sum(
                (
                    _d(event[3])
                    for event in event_rows
                    if str(event[0]) == "EXIT"
                ),
                ZERO,
            )
            entry_cost = sum(
                (
                    _d(event[3])
                    for event in event_rows
                    if str(event[0]) == "ENTER"
                ),
                ZERO,
            )
            realized_total = sum(
                (_d(event[4]) for event in event_rows),
                ZERO,
            )
            if enter_count != 1:
                reasons.append(
                    f"ENTER event count {enter_count} != required 1"
                )
            expected_exit_count = 1 if status == "CLOSED" else 0
            if exit_count != expected_exit_count:
                reasons.append(
                    f"EXIT event count {exit_count} != required "
                    f"{expected_exit_count} for status {status}"
                )

            comparisons = (
                ("entry_cost_quote", _d(row[2]), entry_cost),
                ("fee_income_quote", _d(row[4]), fee_total),
                ("reward_income_quote", _d(row[5]), reward_total),
                ("rebalance_cost_quote", _d(row[6]), rebalance_cost),
                ("exit_cost_quote", _d(row[7]), exit_cost),
            )
            for name, stored, expected in comparisons:
                if stored != expected:
                    reasons.append(
                        f"{name} stored {stored} != event-derived {expected}"
                    )

            realized = row[8]
            if status == "CLOSED":
                if realized is None:
                    reasons.append(
                        "closed position is missing realized_pnl_quote"
                    )
                elif _d(realized) != realized_total:
                    reasons.append(
                        "realized_pnl_quote stored "
                        f"{_d(realized)} != event-derived "
                        f"{realized_total}"
                    )
                if _d(row[3]) != ZERO:
                    reasons.append(
                        "closed position current_mark_quote must be zero"
                    )
            else:
                if realized is not None:
                    reasons.append(
                        "open position unexpectedly has realized_pnl_quote"
                    )
                expected_mark = _latest_mark(conn, position_id)
                if expected_mark is None:
                    reasons.append(
                        "open position has no mark-bearing ledger event"
                    )
                elif _d(row[3]) != expected_mark:
                    reasons.append(
                        "current_mark_quote stored "
                        f"{_d(row[3])} != latest event mark {expected_mark}"
                    )

            position_reports.append(
                PaperPositionAudit(
                    position_id=position_id,
                    status=status,
                    passing=not reasons,
                    reasons=tuple(reasons),
                )
            )

    starting = _d(account[0])
    stored_cash = _d(account[1])
    expected_cash = starting + sum(
        (_d(row[0]) for row in cash_event_rows),
        ZERO,
    )
    cash_drift = stored_cash - expected_cash
    reasons: list[str] = []
    if cash_drift != ZERO:
        reasons.append(
            f"cash_quote drift {cash_drift}: stored {stored_cash}, "
            f"event-derived {expected_cash}"
        )

    position_failures = sum(not item.passing for item in position_reports)
    if position_failures:
        reasons.append(
            f"{position_failures} paper position(s) failed ledger audit"
        )

    return PaperLedgerAuditReport(
        account_id=account_id,
        passing=not reasons,
        starting_equity_quote=float(starting),
        stored_cash_quote=float(stored_cash),
        event_derived_cash_quote=float(expected_cash),
        cash_drift_quote=float(cash_drift),
        positions_checked=len(position_reports),
        position_failures=position_failures,
        positions=tuple(position_reports),
        reasons=tuple(reasons),
    )
