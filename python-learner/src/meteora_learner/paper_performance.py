from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from .storage import Storage


BPS = Decimal("10000")


@dataclass(frozen=True)
class PaperPerformanceReport:
    account_id: str
    policy_source: str
    model_id: str | None
    closed_trades: int
    total_entry_capital_quote: float
    total_realized_pnl_quote: float
    realized_return_bps: int | None
    winning_trades: int
    losing_trades: int
    flat_trades: int
    win_rate: float | None
    average_trade_return_bps: float | None
    worst_trade_return_bps: int | None
    best_trade_return_bps: int | None
    realized_max_drawdown_bps: int | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_paper_performance(
    storage: Storage,
    *,
    account_id: str,
    policy_source: str,
    model_id: str | None = None,
) -> PaperPerformanceReport:
    params: list[Any] = [account_id, policy_source]
    model_clause = ""
    if model_id is None:
        model_clause = "AND model_id IS NULL"
    else:
        model_clause = "AND model_id = ?"
        params.append(model_id)

    with storage.connect() as conn:
        account = conn.execute(
            """
            SELECT starting_equity_quote
            FROM paper_accounts
            WHERE account_id = ?
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError(f"unknown paper account: {account_id}")

        rows = conn.execute(
            f"""
            SELECT entry_capital_quote, realized_pnl_quote, closed_at
            FROM paper_positions
            WHERE account_id = ?
              AND policy_source = ?
              {model_clause}
              AND status = 'CLOSED'
              AND realized_pnl_quote IS NOT NULL
            ORDER BY closed_at ASC, position_id ASC
            """,
            params,
        ).fetchall()

    starting_equity = Decimal(str(account[0]))
    total_capital = Decimal("0")
    total_pnl = Decimal("0")
    returns_bps: list[int] = []
    realized_equity = starting_equity
    high_water = starting_equity
    max_drawdown_bps = 0

    for row in rows:
        capital = Decimal(str(row[0]))
        pnl = Decimal(str(row[1]))
        if capital <= 0:
            raise ValueError("closed paper trade has non-positive entry capital")

        total_capital += capital
        total_pnl += pnl
        returns_bps.append(int(pnl * BPS / capital))

        realized_equity += pnl
        if realized_equity > high_water:
            high_water = realized_equity
        elif high_water > 0:
            drawdown = int((high_water - realized_equity) * BPS / high_water)
            max_drawdown_bps = max(max_drawdown_bps, drawdown)

    closed = len(rows)
    wins = sum(value > 0 for value in returns_bps)
    losses = sum(value < 0 for value in returns_bps)
    flats = sum(value == 0 for value in returns_bps)

    return PaperPerformanceReport(
        account_id=account_id,
        policy_source=policy_source,
        model_id=model_id,
        closed_trades=closed,
        total_entry_capital_quote=float(total_capital),
        total_realized_pnl_quote=float(total_pnl),
        realized_return_bps=(
            int(total_pnl * BPS / total_capital)
            if total_capital > 0
            else None
        ),
        winning_trades=wins,
        losing_trades=losses,
        flat_trades=flats,
        win_rate=wins / closed if closed else None,
        average_trade_return_bps=(
            sum(returns_bps) / closed if closed else None
        ),
        worst_trade_return_bps=min(returns_bps) if returns_bps else None,
        best_trade_return_bps=max(returns_bps) if returns_bps else None,
        realized_max_drawdown_bps=(
            max_drawdown_bps if closed else None
        ),
    )
