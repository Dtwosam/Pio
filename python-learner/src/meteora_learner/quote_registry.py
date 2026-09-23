from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any, Iterable

from .storage import Storage, utc_now_iso


DEFAULT_QUOTE_UNIT = "ACCOUNT_QUOTE"


@dataclass(frozen=True)
class TokenQuoteObservation:
    token_mint: str
    quote_unit: str
    quote_per_atomic: float
    source: str
    observed_at: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TokenQuoteStatus:
    token_mint: str
    quote_unit: str
    available: bool
    fresh: bool
    quote_per_atomic: float | None
    source: str | None
    observed_at: str | None
    age_seconds: int | None
    reason: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("quote timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def save_token_quote(
    storage: Storage,
    *,
    token_mint: str,
    quote_per_atomic: float,
    source: str,
    observed_at: str | None = None,
    quote_unit: str = DEFAULT_QUOTE_UNIT,
    raw: dict[str, Any] | None = None,
) -> TokenQuoteObservation:
    if not token_mint.strip():
        raise ValueError("token_mint is required")
    if not source.strip():
        raise ValueError("quote source is required")
    if not quote_unit.strip():
        raise ValueError("quote_unit is required")
    value = Decimal(str(quote_per_atomic))
    if not value.is_finite() or value <= 0:
        raise ValueError("quote_per_atomic must be finite and positive")

    timestamp = observed_at or utc_now_iso()
    _parse_time(timestamp)
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO token_quote_observations(
                token_mint, quote_unit, quote_per_atomic,
                source, observed_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_mint, quote_unit, source, observed_at)
            DO UPDATE SET
                quote_per_atomic=excluded.quote_per_atomic,
                raw_json=excluded.raw_json
            """,
            (
                token_mint,
                quote_unit,
                format(value, "f"),
                source,
                timestamp,
                json.dumps(raw or {}, separators=(",", ":")),
            ),
        )
    return TokenQuoteObservation(
        token_mint=token_mint,
        quote_unit=quote_unit,
        quote_per_atomic=float(value),
        source=source,
        observed_at=timestamp,
    )


def token_quote_status(
    storage: Storage,
    *,
    token_mint: str,
    max_age_seconds: int = 300,
    as_of: str | None = None,
    quote_unit: str = DEFAULT_QUOTE_UNIT,
) -> TokenQuoteStatus:
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds cannot be negative")
    now = _parse_time(as_of) if as_of is not None else datetime.now(timezone.utc)

    with storage.connect() as conn:
        if as_of is None:
            row = conn.execute(
                """
                SELECT quote_per_atomic, source, observed_at
                FROM token_quote_observations
                WHERE token_mint = ? AND quote_unit = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (token_mint, quote_unit),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT quote_per_atomic, source, observed_at
                FROM token_quote_observations
                WHERE token_mint = ? AND quote_unit = ?
                  AND observed_at <= ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (token_mint, quote_unit, as_of),
            ).fetchone()

    if row is None:
        return TokenQuoteStatus(
            token_mint=token_mint,
            quote_unit=quote_unit,
            available=False,
            fresh=False,
            quote_per_atomic=None,
            source=None,
            observed_at=None,
            age_seconds=None,
            reason="no persisted quote observation",
        )

    observed = _parse_time(str(row[2]))
    age = max(0, int((now - observed).total_seconds()))
    fresh = age <= max_age_seconds
    return TokenQuoteStatus(
        token_mint=token_mint,
        quote_unit=quote_unit,
        available=True,
        fresh=fresh,
        quote_per_atomic=float(Decimal(str(row[0]))),
        source=str(row[1]),
        observed_at=str(row[2]),
        age_seconds=age,
        reason=None if fresh else f"quote is stale by {age} seconds",
    )



def required_paper_quote_mints(
    storage: Storage,
    *,
    account_id: str,
) -> tuple[str, ...]:
    """
    Return quote mints required to value open chain-bound PAPER positions.

    Token Y is always required because PAPER account value is expressed in the
    account quote unit. Reward mints need an external quote only when they are
    neither token X nor token Y for their pool.
    """
    if not account_id.strip():
        raise ValueError("account_id is required")

    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT c.token_x_mint, c.token_y_mint,
                   c.reward_mint_0, c.reward_mint_1
            FROM paper_positions p
            JOIN paper_counterfactual_positions c
              ON c.position_id = p.position_id
            WHERE p.account_id = ?
              AND p.status = 'OPEN'
            """,
            (account_id,),
        ).fetchall()

    required: set[str] = set()
    for token_x, token_y, reward_0, reward_1 in rows:
        x = str(token_x)
        y = str(token_y)
        if y:
            required.add(y)
        for reward in (reward_0, reward_1):
            if reward is None:
                continue
            mint = str(reward)
            if mint and mint not in {x, y}:
                required.add(mint)
    return tuple(sorted(required))

def load_fresh_quote_map(
    storage: Storage,
    *,
    token_mints: Iterable[str],
    max_age_seconds: int = 300,
    as_of: str | None = None,
    quote_unit: str = DEFAULT_QUOTE_UNIT,
) -> tuple[dict[str, float], tuple[TokenQuoteStatus, ...]]:
    quotes: dict[str, float] = {}
    statuses: list[TokenQuoteStatus] = []
    for mint in sorted(set(str(value) for value in token_mints if str(value))):
        status = token_quote_status(
            storage,
            token_mint=mint,
            max_age_seconds=max_age_seconds,
            as_of=as_of,
            quote_unit=quote_unit,
        )
        statuses.append(status)
        if status.fresh and status.quote_per_atomic is not None:
            quotes[mint] = status.quote_per_atomic
    return quotes, tuple(statuses)
