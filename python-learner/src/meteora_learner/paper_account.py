from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from typing import Any

from .storage import Storage, utc_now_iso


ZERO = Decimal("0")
BPS = Decimal("10000")


def _d(value: Decimal | str | float | int) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("paper-account values must be finite")
    return result


def _text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class PaperAccountSnapshot:
    account_id: str
    starting_equity_quote: float
    cash_quote: float
    open_position_mark_quote: float
    open_fee_income_quote: float
    open_reward_income_quote: float
    account_equity_quote: float
    high_water_equity_quote: float
    drawdown_bps: int
    open_positions: int
    closed_positions: int
    realized_pnl_quote: float

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaperPositionSnapshot:
    position_id: str
    account_id: str
    pool_address: str
    status: str
    policy_source: str
    model_id: str | None
    strategy: str
    min_bin_id: int
    max_bin_id: int
    entry_capital_quote: float
    entry_cost_quote: float
    current_mark_quote: float
    fee_income_quote: float
    reward_income_quote: float
    rebalance_cost_quote: float
    exit_cost_quote: float
    realized_pnl_quote: float | None
    rebalances: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_event_unused(conn: Any, event_key: str) -> None:
    if not event_key:
        raise ValueError("event_key is required")
    row = conn.execute(
        "SELECT 1 FROM paper_events WHERE event_key = ? LIMIT 1",
        (event_key,),
    ).fetchone()
    if row is not None:
        raise ValueError(f"paper event already applied: {event_key}")


def _equity_parts(conn: Any, account_id: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    account = conn.execute(
        """
        SELECT cash_quote
        FROM paper_accounts
        WHERE account_id = ?
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    if account is None:
        raise ValueError(f"unknown paper account: {account_id}")

    totals = conn.execute(
        """
        SELECT
            COALESCE(SUM(CAST(current_mark_quote AS REAL)), 0),
            COALESCE(SUM(CAST(fee_income_quote AS REAL)), 0),
            COALESCE(SUM(CAST(reward_income_quote AS REAL)), 0)
        FROM paper_positions
        WHERE account_id = ? AND status = 'OPEN'
        """,
        (account_id,),
    ).fetchone()
    return (
        _d(account[0]),
        _d(totals[0]),
        _d(totals[1]),
        _d(totals[2]),
    )


def _refresh_high_water(conn: Any, account_id: str) -> Decimal:
    cash, marks, fees, rewards = _equity_parts(conn, account_id)
    equity = cash + marks + fees + rewards
    row = conn.execute(
        """
        SELECT high_water_equity_quote
        FROM paper_accounts
        WHERE account_id = ?
        """,
        (account_id,),
    ).fetchone()
    high_water = _d(row[0])
    if equity > high_water:
        high_water = equity
        conn.execute(
            """
            UPDATE paper_accounts
            SET high_water_equity_quote = ?, updated_at = ?
            WHERE account_id = ?
            """,
            (_text(high_water), utc_now_iso(), account_id),
        )
    else:
        conn.execute(
            """
            UPDATE paper_accounts
            SET updated_at = ?
            WHERE account_id = ?
            """,
            (utc_now_iso(), account_id),
        )
    return equity


def create_paper_account(
    storage: Storage,
    *,
    account_id: str,
    starting_cash_quote: float,
) -> PaperAccountSnapshot:
    if not account_id.strip():
        raise ValueError("account_id is required")
    starting = _d(starting_cash_quote)
    if starting <= 0:
        raise ValueError("starting_cash_quote must be positive")
    now = utc_now_iso()

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_accounts(
                account_id, created_at, updated_at,
                starting_equity_quote, cash_quote,
                high_water_equity_quote
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                now,
                now,
                _text(starting),
                _text(starting),
                _text(starting),
            ),
        )
    return paper_account_snapshot(storage, account_id=account_id)


def paper_account_snapshot(
    storage: Storage,
    *,
    account_id: str,
) -> PaperAccountSnapshot:
    with storage.connect() as conn:
        account = conn.execute(
            """
            SELECT starting_equity_quote, cash_quote, high_water_equity_quote
            FROM paper_accounts
            WHERE account_id = ?
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError(f"unknown paper account: {account_id}")

        open_totals = conn.execute(
            """
            SELECT COUNT(*),
                   COALESCE(SUM(CAST(current_mark_quote AS REAL)), 0),
                   COALESCE(SUM(CAST(fee_income_quote AS REAL)), 0),
                   COALESCE(SUM(CAST(reward_income_quote AS REAL)), 0)
            FROM paper_positions
            WHERE account_id = ? AND status = 'OPEN'
            """,
            (account_id,),
        ).fetchone()
        closed = conn.execute(
            """
            SELECT COUNT(*),
                   COALESCE(SUM(CAST(realized_pnl_quote AS REAL)), 0)
            FROM paper_positions
            WHERE account_id = ? AND status = 'CLOSED'
            """,
            (account_id,),
        ).fetchone()

    starting = _d(account[0])
    cash = _d(account[1])
    high_water = _d(account[2])
    mark = _d(open_totals[1])
    fees = _d(open_totals[2])
    rewards = _d(open_totals[3])
    equity = cash + mark + fees + rewards
    drawdown_bps = (
        int((high_water - equity) * BPS / high_water)
        if high_water > 0 and equity < high_water
        else 0
    )

    return PaperAccountSnapshot(
        account_id=account_id,
        starting_equity_quote=float(starting),
        cash_quote=float(cash),
        open_position_mark_quote=float(mark),
        open_fee_income_quote=float(fees),
        open_reward_income_quote=float(rewards),
        account_equity_quote=float(equity),
        high_water_equity_quote=float(high_water),
        drawdown_bps=drawdown_bps,
        open_positions=int(open_totals[0]),
        closed_positions=int(closed[0]),
        realized_pnl_quote=float(_d(closed[1])),
    )


def paper_position_snapshot(
    storage: Storage,
    *,
    position_id: str,
) -> PaperPositionSnapshot:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT position_id, account_id, pool_address, status,
                   policy_source, model_id, strategy, min_bin_id, max_bin_id,
                   entry_capital_quote, entry_cost_quote, current_mark_quote,
                   fee_income_quote, reward_income_quote,
                   rebalance_cost_quote, exit_cost_quote,
                   realized_pnl_quote, rebalances
            FROM paper_positions
            WHERE position_id = ?
            LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown paper position: {position_id}")

    return PaperPositionSnapshot(
        position_id=str(row[0]),
        account_id=str(row[1]),
        pool_address=str(row[2]),
        status=str(row[3]),
        policy_source=str(row[4]),
        model_id=str(row[5]) if row[5] is not None else None,
        strategy=str(row[6]),
        min_bin_id=int(row[7]),
        max_bin_id=int(row[8]),
        entry_capital_quote=float(_d(row[9])),
        entry_cost_quote=float(_d(row[10])),
        current_mark_quote=float(_d(row[11])),
        fee_income_quote=float(_d(row[12])),
        reward_income_quote=float(_d(row[13])),
        rebalance_cost_quote=float(_d(row[14])),
        exit_cost_quote=float(_d(row[15])),
        realized_pnl_quote=(
            float(_d(row[16])) if row[16] is not None else None
        ),
        rebalances=int(row[17]),
    )


def open_paper_position(
    storage: Storage,
    *,
    event_key: str,
    account_id: str,
    position_id: str,
    pool_address: str,
    policy_source: str,
    strategy: str,
    min_bin_id: int,
    max_bin_id: int,
    capital_quote: float,
    entry_cost_quote: float = 0.0,
    model_id: str | None = None,
    event_time: str | None = None,
) -> PaperPositionSnapshot:
    if not position_id.strip() or not pool_address.strip():
        raise ValueError("position_id and pool_address are required")
    if not policy_source.strip() or not strategy.strip():
        raise ValueError("policy_source and strategy are required")
    if min_bin_id > max_bin_id:
        raise ValueError("min_bin_id cannot exceed max_bin_id")

    capital = _d(capital_quote)
    cost = _d(entry_cost_quote)
    if capital <= 0:
        raise ValueError("capital_quote must be positive")
    if cost < 0:
        raise ValueError("entry_cost_quote cannot be negative")
    event_time = event_time or utc_now_iso()

    with storage.connect() as conn:
        _ensure_event_unused(conn, event_key)
        account = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if account is None:
            raise ValueError(f"unknown paper account: {account_id}")
        cash = _d(account[0])
        debit = capital + cost
        if cash < debit:
            raise ValueError("insufficient paper cash for entry and cost")
        if conn.execute(
            "SELECT 1 FROM paper_positions WHERE position_id = ?",
            (position_id,),
        ).fetchone() is not None:
            raise ValueError(f"paper position already exists: {position_id}")

        conn.execute(
            """
            UPDATE paper_accounts
            SET cash_quote = ?, updated_at = ?
            WHERE account_id = ?
            """,
            (_text(cash - debit), event_time, account_id),
        )
        conn.execute(
            """
            INSERT INTO paper_positions(
                position_id, account_id, pool_address, status,
                policy_source, model_id, strategy, min_bin_id, max_bin_id,
                opened_at, entry_capital_quote, entry_cost_quote,
                current_mark_quote, fee_income_quote, reward_income_quote,
                rebalance_cost_quote, exit_cost_quote, rebalances, raw_json
            ) VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, '0', '0', '0', '0', 0, ?)
            """,
            (
                position_id,
                account_id,
                pool_address,
                policy_source,
                model_id,
                strategy,
                min_bin_id,
                max_bin_id,
                event_time,
                _text(capital),
                _text(cost),
                _text(capital),
                json.dumps(
                    {
                        "event_key": event_key,
                        "policy_source": policy_source,
                        "model_id": model_id,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO paper_events(
                event_key, account_id, position_id, event_time, event_type,
                cash_delta_quote, position_mark_quote, cost_quote, raw_json
            ) VALUES (?, ?, ?, ?, 'ENTER', ?, ?, ?, ?)
            """,
            (
                event_key,
                account_id,
                position_id,
                event_time,
                _text(-debit),
                _text(capital),
                _text(cost),
                "{}",
            ),
        )
        _refresh_high_water(conn, account_id)

    return paper_position_snapshot(storage, position_id=position_id)


def mark_paper_position(
    storage: Storage,
    *,
    event_key: str,
    position_id: str,
    mark_quote: float,
    fee_delta_quote: float = 0.0,
    reward_delta_quote: float = 0.0,
    event_time: str | None = None,
) -> PaperPositionSnapshot:
    mark = _d(mark_quote)
    fee_delta = _d(fee_delta_quote)
    reward_delta = _d(reward_delta_quote)
    if mark < 0 or fee_delta < 0 or reward_delta < 0:
        raise ValueError("mark and income deltas cannot be negative")
    event_time = event_time or utc_now_iso()

    with storage.connect() as conn:
        _ensure_event_unused(conn, event_key)
        row = conn.execute(
            """
            SELECT account_id, status, fee_income_quote, reward_income_quote
            FROM paper_positions
            WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown paper position: {position_id}")
        if row[1] != "OPEN":
            raise ValueError("only open paper positions can be marked")

        account_id = str(row[0])
        fees = _d(row[2]) + fee_delta
        rewards = _d(row[3]) + reward_delta
        conn.execute(
            """
            UPDATE paper_positions
            SET current_mark_quote = ?, fee_income_quote = ?,
                reward_income_quote = ?
            WHERE position_id = ?
            """,
            (_text(mark), _text(fees), _text(rewards), position_id),
        )
        conn.execute(
            """
            INSERT INTO paper_events(
                event_key, account_id, position_id, event_time, event_type,
                cash_delta_quote, position_mark_quote,
                fee_delta_quote, reward_delta_quote, raw_json
            ) VALUES (?, ?, ?, ?, 'MARK', '0', ?, ?, ?, '{}')
            """,
            (
                event_key,
                account_id,
                position_id,
                event_time,
                _text(mark),
                _text(fee_delta),
                _text(reward_delta),
            ),
        )
        _refresh_high_water(conn, account_id)

    return paper_position_snapshot(storage, position_id=position_id)


def rebalance_paper_position(
    storage: Storage,
    *,
    event_key: str,
    position_id: str,
    new_min_bin_id: int,
    new_max_bin_id: int,
    new_mark_quote: float,
    rebalance_cost_quote: float,
    event_time: str | None = None,
) -> PaperPositionSnapshot:
    if new_min_bin_id > new_max_bin_id:
        raise ValueError("new_min_bin_id cannot exceed new_max_bin_id")
    mark = _d(new_mark_quote)
    cost = _d(rebalance_cost_quote)
    if mark < 0 or cost < 0:
        raise ValueError("mark and rebalance cost cannot be negative")
    event_time = event_time or utc_now_iso()

    with storage.connect() as conn:
        _ensure_event_unused(conn, event_key)
        row = conn.execute(
            """
            SELECT account_id, status, rebalance_cost_quote
            FROM paper_positions
            WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown paper position: {position_id}")
        if row[1] != "OPEN":
            raise ValueError("only open paper positions can be rebalanced")

        account_id = str(row[0])
        account = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        cash = _d(account[0])
        if cash < cost:
            raise ValueError("insufficient paper cash for rebalance cost")
        total_rebalance_cost = _d(row[2]) + cost

        conn.execute(
            """
            UPDATE paper_accounts
            SET cash_quote = ?, updated_at = ?
            WHERE account_id = ?
            """,
            (_text(cash - cost), event_time, account_id),
        )
        conn.execute(
            """
            UPDATE paper_positions
            SET min_bin_id = ?, max_bin_id = ?, current_mark_quote = ?,
                rebalance_cost_quote = ?, rebalances = rebalances + 1
            WHERE position_id = ?
            """,
            (
                new_min_bin_id,
                new_max_bin_id,
                _text(mark),
                _text(total_rebalance_cost),
                position_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO paper_events(
                event_key, account_id, position_id, event_time, event_type,
                cash_delta_quote, position_mark_quote, cost_quote, raw_json
            ) VALUES (?, ?, ?, ?, 'REBALANCE', ?, ?, ?, ?)
            """,
            (
                event_key,
                account_id,
                position_id,
                event_time,
                _text(-cost),
                _text(mark),
                _text(cost),
                json.dumps(
                    {
                        "new_min_bin_id": new_min_bin_id,
                        "new_max_bin_id": new_max_bin_id,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        _refresh_high_water(conn, account_id)

    return paper_position_snapshot(storage, position_id=position_id)


def close_paper_position(
    storage: Storage,
    *,
    event_key: str,
    position_id: str,
    final_mark_quote: float,
    exit_cost_quote: float = 0.0,
    fee_delta_quote: float = 0.0,
    reward_delta_quote: float = 0.0,
    event_time: str | None = None,
) -> PaperPositionSnapshot:
    mark = _d(final_mark_quote)
    exit_cost = _d(exit_cost_quote)
    fee_delta = _d(fee_delta_quote)
    reward_delta = _d(reward_delta_quote)
    if min(mark, exit_cost, fee_delta, reward_delta) < 0:
        raise ValueError("close mark, income and cost values cannot be negative")
    event_time = event_time or utc_now_iso()

    with storage.connect() as conn:
        _ensure_event_unused(conn, event_key)
        row = conn.execute(
            """
            SELECT account_id, status, entry_capital_quote, entry_cost_quote,
                   fee_income_quote, reward_income_quote, rebalance_cost_quote
            FROM paper_positions
            WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown paper position: {position_id}")
        if row[1] != "OPEN":
            raise ValueError("only open paper positions can be closed")

        account_id = str(row[0])
        entry_capital = _d(row[2])
        entry_cost = _d(row[3])
        fees = _d(row[4]) + fee_delta
        rewards = _d(row[5]) + reward_delta
        rebalance_cost = _d(row[6])

        proceeds = mark + fees + rewards - exit_cost
        account = conn.execute(
            "SELECT cash_quote FROM paper_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        cash = _d(account[0])
        final_cash = cash + proceeds
        if final_cash < 0:
            raise ValueError("paper exit would make cash negative")

        realized = (
            mark
            + fees
            + rewards
            - entry_capital
            - entry_cost
            - rebalance_cost
            - exit_cost
        )

        conn.execute(
            """
            UPDATE paper_accounts
            SET cash_quote = ?, updated_at = ?
            WHERE account_id = ?
            """,
            (_text(final_cash), event_time, account_id),
        )
        conn.execute(
            """
            UPDATE paper_positions
            SET status = 'CLOSED', closed_at = ?, current_mark_quote = '0',
                fee_income_quote = ?, reward_income_quote = ?,
                exit_cost_quote = ?, realized_pnl_quote = ?
            WHERE position_id = ?
            """,
            (
                event_time,
                _text(fees),
                _text(rewards),
                _text(exit_cost),
                _text(realized),
                position_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO paper_events(
                event_key, account_id, position_id, event_time, event_type,
                cash_delta_quote, position_mark_quote,
                fee_delta_quote, reward_delta_quote,
                cost_quote, realized_pnl_quote, raw_json
            ) VALUES (?, ?, ?, ?, 'EXIT', ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                event_key,
                account_id,
                position_id,
                event_time,
                _text(proceeds),
                _text(mark),
                _text(fee_delta),
                _text(reward_delta),
                _text(exit_cost),
                _text(realized),
            ),
        )
        _refresh_high_water(conn, account_id)

    return paper_position_snapshot(storage, position_id=position_id)
