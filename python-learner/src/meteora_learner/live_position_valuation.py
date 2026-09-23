from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any

from .quote_registry import DEFAULT_QUOTE_UNIT
from .storage import Storage, utc_now_iso


BPS = Decimal("10000")
WSOL_MINT = "So11111111111111111111111111111111111111112"
ZERO = Decimal("0")


def _d(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("live valuation encountered a non-finite number")
    return result


def _fmt(value: Decimal) -> str:
    if value == ZERO:
        return "0"
    return format(value.normalize(), "f")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("live valuation timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class LivePositionValuation:
    position_address: str
    pool_address: str
    opened_decision_id: str
    closed_decision_id: str
    quote_unit: str
    valued_execution_count: int
    principal_cashflow_quote: str
    composition_cost_quote: str
    fee_income_quote: str
    reward_income_quote: str
    network_cost_quote: str
    realized_pnl_quote: str
    entry_outflow_quote: str
    realized_return_bps: int
    max_age_seconds: int
    quote_evidence: tuple[dict[str, Any], ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LivePositionValuationResult:
    valuation: LivePositionValuation
    reused_existing: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "valuation": self.valuation.to_record(),
            "reused_existing": self.reused_existing,
        }


def _fresh_quote(
    conn: Any,
    *,
    token_mint: str,
    as_of: str,
    quote_unit: str,
    max_age_seconds: int,
) -> tuple[Decimal, dict[str, Any]]:
    row = conn.execute(
        """
        SELECT quote_per_atomic, source, observed_at
        FROM token_quote_observations
        WHERE token_mint = ?
          AND quote_unit = ?
          AND julianday(observed_at) <= julianday(?)
        ORDER BY julianday(observed_at) DESC, id DESC
        LIMIT 1
        """,
        (token_mint, quote_unit, as_of),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"no persisted {quote_unit} quote at or before {as_of} "
            f"for token mint {token_mint}"
        )

    quote = _d(row[0])
    if quote <= 0:
        raise ValueError(f"stored quote for {token_mint} must be positive")
    as_of_time = _parse_time(as_of)
    observed_time = _parse_time(str(row[2]))
    age_seconds = int((as_of_time - observed_time).total_seconds())
    if age_seconds < 0:
        raise ValueError("quote lookup violated no-lookahead ordering")
    if age_seconds > max_age_seconds:
        raise ValueError(
            f"quote for {token_mint} is stale by {age_seconds} seconds "
            f"at execution time {as_of}"
        )
    return quote, {
        "token_mint": token_mint,
        "quote_unit": quote_unit,
        "quote_per_atomic": _fmt(quote),
        "source": str(row[1]),
        "observed_at": str(row[2]),
        "as_of": as_of,
        "age_seconds": age_seconds,
    }


def _pool_assets(
    conn: Any,
    *,
    pool_address: str,
    as_of: str,
    max_age_seconds: int,
) -> tuple[str, str, str | None, str | None, dict[str, Any]]:
    row = conn.execute(
        """
        SELECT token_x_mint, token_y_mint,
               reward_mint_0, reward_mint_1, observed_at
        FROM chain_pool_snapshots
        WHERE pool_address = ?
          AND julianday(observed_at) <= julianday(?)
        ORDER BY julianday(observed_at) DESC, id DESC
        LIMIT 1
        """,
        (pool_address, as_of),
    ).fetchone()
    if row is None:
        raise ValueError(
            "live valuation requires a persisted pool snapshot at or before "
            f"execution time {as_of}"
        )

    observed = str(row[4])
    age_seconds = int(
        (_parse_time(as_of) - _parse_time(observed)).total_seconds()
    )
    if age_seconds < 0:
        raise ValueError("pool snapshot lookup violated no-lookahead ordering")
    if age_seconds > max_age_seconds:
        raise ValueError(
            f"pool metadata is stale by {age_seconds} seconds at {as_of}"
        )
    return (
        str(row[0]),
        str(row[1]),
        str(row[2]) if row[2] is not None and str(row[2]) else None,
        str(row[3]) if row[3] is not None and str(row[3]) else None,
        {
            "pool_address": pool_address,
            "observed_at": observed,
            "as_of": as_of,
            "age_seconds": age_seconds,
        },
    )


def _value_asset(
    conn: Any,
    *,
    amount: Decimal,
    token_mint: str | None,
    as_of: str,
    quote_unit: str,
    max_age_seconds: int,
    quote_cache: dict[tuple[str, str], tuple[Decimal, dict[str, Any]]],
) -> tuple[Decimal, dict[str, Any] | None]:
    if amount == ZERO:
        return ZERO, None
    if not token_mint:
        raise ValueError("non-zero live amount is missing its token mint")

    key = (token_mint, as_of)
    if key not in quote_cache:
        quote_cache[key] = _fresh_quote(
            conn,
            token_mint=token_mint,
            as_of=as_of,
            quote_unit=quote_unit,
            max_age_seconds=max_age_seconds,
        )
    quote, evidence = quote_cache[key]
    return amount * quote, evidence


def value_live_position_outcome(
    storage: Storage,
    *,
    position_address: str,
    max_age_seconds: int = 300,
    quote_unit: str = DEFAULT_QUOTE_UNIT,
) -> LivePositionValuationResult:
    if not position_address.strip():
        raise ValueError("position_address is required")
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds cannot be negative")
    if not quote_unit.strip():
        raise ValueError("quote_unit is required")

    with storage.connect() as conn:
        outcome = conn.execute(
            """
            SELECT pool_address, opened_decision_id, closed_decision_id,
                   execution_count, label_status, raw_json
            FROM live_position_outcomes
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if outcome is None:
            raise ValueError(
                "live valuation requires an immutable atomic position outcome"
            )
        (
            pool_address,
            opened_decision_id,
            closed_decision_id,
            execution_count,
            label_status,
            outcome_raw,
        ) = outcome
        pool_address = str(pool_address)
        opened_decision_id = str(opened_decision_id)
        closed_decision_id = str(closed_decision_id)

        effects = conn.execute(
            """
            SELECT decision_id, observed_at,
                   token_x_wallet_delta_atomic,
                   token_y_wallet_delta_atomic,
                   composition_fee_x_atomic,
                   composition_fee_y_atomic,
                   earned_fee_x_atomic,
                   earned_fee_y_atomic,
                   reward_one_atomic,
                   reward_two_atomic,
                   network_fee_lamports
            FROM live_execution_effects
            WHERE position_address = ?
            ORDER BY julianday(observed_at) ASC, decision_id ASC
            """,
            (position_address,),
        ).fetchall()
        if len(effects) != int(execution_count):
            raise ValueError(
                "live valuation requires one persisted execution effect for "
                "every execution counted in the closed-position outcome"
            )
        if not effects:
            raise ValueError("live valuation requires execution effects")

        quote_cache: dict[
            tuple[str, str],
            tuple[Decimal, dict[str, Any]],
        ] = {}
        quote_evidence: list[dict[str, Any]] = []
        principal_total = ZERO
        composition_total = ZERO
        fee_total = ZERO
        reward_total = ZERO
        network_total = ZERO
        entry_outflow: Decimal | None = None
        saw_opening_effect = False

        for row in effects:
            decision_id = str(row[0])
            observed_at = str(row[1])
            (
                token_x_mint,
                token_y_mint,
                reward_mint_0,
                reward_mint_1,
                pool_evidence,
            ) = _pool_assets(
                conn,
                pool_address=pool_address,
                as_of=observed_at,
                max_age_seconds=max_age_seconds,
            )

            values: dict[str, Decimal] = {}
            evidence_for_effect: dict[str, dict[str, Any]] = {}

            for name, amount, mint in (
                ("principal_x", _d(row[2]), token_x_mint),
                ("principal_y", _d(row[3]), token_y_mint),
                ("composition_x", _d(row[4]), token_x_mint),
                ("composition_y", _d(row[5]), token_y_mint),
                ("fee_x", _d(row[6]), token_x_mint),
                ("fee_y", _d(row[7]), token_y_mint),
                ("reward_0", _d(row[8]), reward_mint_0),
                ("reward_1", _d(row[9]), reward_mint_1),
            ):
                value, evidence = _value_asset(
                    conn,
                    amount=amount,
                    token_mint=mint,
                    as_of=observed_at,
                    quote_unit=quote_unit,
                    max_age_seconds=max_age_seconds,
                    quote_cache=quote_cache,
                )
                values[name] = value
                if evidence is not None:
                    evidence_for_effect[name] = evidence

            network_lamports = (
                _d(row[10]) if row[10] is not None else ZERO
            )
            network_value, network_evidence = _value_asset(
                conn,
                amount=network_lamports,
                token_mint=WSOL_MINT,
                as_of=observed_at,
                quote_unit=quote_unit,
                max_age_seconds=max_age_seconds,
                quote_cache=quote_cache,
            )
            if network_evidence is not None:
                evidence_for_effect["network_fee"] = network_evidence

            principal = values["principal_x"] + values["principal_y"]
            composition = (
                values["composition_x"] + values["composition_y"]
            )
            fees = values["fee_x"] + values["fee_y"]
            rewards = values["reward_0"] + values["reward_1"]
            net = principal - composition + fees + rewards - network_value

            principal_total += principal
            composition_total += composition
            fee_total += fees
            reward_total += rewards
            network_total += network_value

            if decision_id == opened_decision_id:
                if saw_opening_effect:
                    raise ValueError(
                        "live valuation found duplicate opening execution effect"
                    )
                saw_opening_effect = True
                entry_outflow = -net
                if entry_outflow <= 0:
                    raise ValueError(
                        "opening execution does not produce a positive quote outflow"
                    )

            quote_evidence.append(
                {
                    "decision_id": decision_id,
                    "observed_at": observed_at,
                    "pool_snapshot": pool_evidence,
                    "quotes": evidence_for_effect,
                    "principal_cashflow_quote": _fmt(principal),
                    "composition_cost_quote": _fmt(composition),
                    "fee_income_quote": _fmt(fees),
                    "reward_income_quote": _fmt(rewards),
                    "network_cost_quote": _fmt(network_value),
                    "net_cashflow_quote": _fmt(net),
                }
            )

        if not saw_opening_effect or entry_outflow is None:
            raise ValueError(
                "live valuation is missing the opening execution effect"
            )

        realized_pnl = (
            principal_total
            - composition_total
            + fee_total
            + reward_total
            - network_total
        )
        realized_return_bps = int(realized_pnl * BPS / entry_outflow)

        valuation = LivePositionValuation(
            position_address=position_address,
            pool_address=pool_address,
            opened_decision_id=opened_decision_id,
            closed_decision_id=closed_decision_id,
            quote_unit=quote_unit,
            valued_execution_count=len(effects),
            principal_cashflow_quote=_fmt(principal_total),
            composition_cost_quote=_fmt(composition_total),
            fee_income_quote=_fmt(fee_total),
            reward_income_quote=_fmt(reward_total),
            network_cost_quote=_fmt(network_total),
            realized_pnl_quote=_fmt(realized_pnl),
            entry_outflow_quote=_fmt(entry_outflow),
            realized_return_bps=realized_return_bps,
            max_age_seconds=max_age_seconds,
            quote_evidence=tuple(quote_evidence),
        )
        canonical = json.dumps(
            valuation.to_record(),
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_json = json.dumps(
            quote_evidence,
            sort_keys=True,
            separators=(",", ":"),
        )

        existing = conn.execute(
            """
            SELECT raw_json
            FROM live_position_valuations
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != canonical:
                raise ValueError(
                    "position already has a different immutable live valuation"
                )
            if str(label_status) != "VALUED":
                stored_outcome = json.loads(str(outcome_raw))
                stored_outcome["label_status"] = "VALUED"
                conn.execute(
                    """
                    UPDATE live_position_outcomes
                    SET label_status = 'VALUED', raw_json = ?
                    WHERE position_address = ?
                    """,
                    (
                        json.dumps(
                            stored_outcome,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        position_address,
                    ),
                )
            return LivePositionValuationResult(
                valuation=valuation,
                reused_existing=True,
            )

        conn.execute(
            """
            INSERT INTO live_position_valuations(
                position_address, pool_address,
                opened_decision_id, closed_decision_id,
                quote_unit, valued_execution_count,
                principal_cashflow_quote, composition_cost_quote,
                fee_income_quote, reward_income_quote,
                network_cost_quote, realized_pnl_quote,
                entry_outflow_quote, realized_return_bps,
                max_age_seconds, created_at,
                quote_evidence_json, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                valuation.position_address,
                valuation.pool_address,
                valuation.opened_decision_id,
                valuation.closed_decision_id,
                valuation.quote_unit,
                valuation.valued_execution_count,
                valuation.principal_cashflow_quote,
                valuation.composition_cost_quote,
                valuation.fee_income_quote,
                valuation.reward_income_quote,
                valuation.network_cost_quote,
                valuation.realized_pnl_quote,
                valuation.entry_outflow_quote,
                valuation.realized_return_bps,
                valuation.max_age_seconds,
                utc_now_iso(),
                evidence_json,
                canonical,
            ),
        )

        stored_outcome = json.loads(str(outcome_raw))
        stored_outcome["label_status"] = "VALUED"
        updated = conn.execute(
            """
            UPDATE live_position_outcomes
            SET label_status = 'VALUED', raw_json = ?
            WHERE position_address = ?
              AND label_status IN ('ATOMIC_ONLY', 'VALUED')
            """,
            (
                json.dumps(
                    stored_outcome,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                position_address,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError(
                "live position outcome changed concurrently during valuation"
            )

    return LivePositionValuationResult(
        valuation=valuation,
        reused_existing=False,
    )
