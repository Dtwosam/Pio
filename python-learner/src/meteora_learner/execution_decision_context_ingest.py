from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class ExecutionDecisionContextIngestResult:
    decision_id: str
    reused_existing: bool
    receipt_reconciled: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _required_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _finite_decimal(payload: dict[str, Any], name: str) -> Decimal:
    value = Decimal(str(payload.get(name)))
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def _non_negative_int(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{name} cannot be negative")
    return parsed


def _normalize(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("execution decision context must be a JSON object")

    mode = _required_string(payload, "mode")
    if mode != "LIVE":
        raise ValueError("execution decision context mode must be LIVE")
    action = _required_string(payload, "action")
    if action not in {"ENTER", "REBALANCE", "EXIT"}:
        raise ValueError(
            "execution decision context action must be ENTER, REBALANCE or EXIT"
        )

    min_bin_id = int(payload["min_bin_id"])
    max_bin_id = int(payload["max_bin_id"])
    if min_bin_id > max_bin_id:
        raise ValueError("min_bin_id cannot exceed max_bin_id")

    status = _required_string(payload, "status")
    if status not in {"CONFIRMED", "FAILED"}:
        raise ValueError(
            "immutable decision context ingestion requires terminal "
            "CONFIRMED or FAILED status"
        )

    signature_raw = payload.get("signature")
    if not isinstance(signature_raw, str) or not signature_raw.strip():
        raise ValueError(
            "terminal execution decision context requires a signature"
        )
    signature = signature_raw.strip()

    decimals = {
        name: _finite_decimal(payload, name)
        for name in (
            "capital_quote",
            "account_equity_quote",
            "portfolio_deployed_quote",
            "daily_drawdown_pct",
            "expected_net_return_pct",
            "expected_downside_pct",
        )
    }
    if decimals["capital_quote"] < 0:
        raise ValueError("capital_quote cannot be negative")
    if decimals["account_equity_quote"] < 0:
        raise ValueError("account_equity_quote cannot be negative")
    if decimals["portfolio_deployed_quote"] < 0:
        raise ValueError("portfolio_deployed_quote cannot be negative")

    normalized = {
        "decision_id": _required_string(payload, "decision_id"),
        "mode": mode,
        "action": action,
        "pool_address": _required_string(payload, "pool_address"),
        "status": status,
        "created_at_unix": _non_negative_int(payload, "created_at_unix"),
        "updated_at_unix": _non_negative_int(payload, "updated_at_unix"),
        "capital_quote": format(decimals["capital_quote"], "f"),
        "account_equity_quote": format(
            decimals["account_equity_quote"],
            "f",
        ),
        "portfolio_deployed_quote": format(
            decimals["portfolio_deployed_quote"],
            "f",
        ),
        "daily_drawdown_pct": format(
            decimals["daily_drawdown_pct"],
            "f",
        ),
        "min_bin_id": min_bin_id,
        "max_bin_id": max_bin_id,
        "strategy": _required_string(payload, "strategy"),
        "expected_net_return_pct": format(
            decimals["expected_net_return_pct"],
            "f",
        ),
        "expected_downside_pct": format(
            decimals["expected_downside_pct"],
            "f",
        ),
        "model_version": _required_string(payload, "model_version"),
        "data_age_seconds": _non_negative_int(
            payload,
            "data_age_seconds",
        ),
        "signature": signature,
    }
    if normalized["updated_at_unix"] < normalized["created_at_unix"]:
        raise ValueError("updated_at_unix cannot precede created_at_unix")
    return normalized


def _receipt_reconciles(conn: Any, context: dict[str, Any]) -> bool:
    receipt = conn.execute(
        """
        SELECT action, pool_address, signature
        FROM live_execution_receipts
        WHERE decision_id = ?
        """,
        (context["decision_id"],),
    ).fetchone()
    if receipt is None:
        return False
    if str(receipt[0]) != context["action"]:
        raise ValueError(
            "decision context action conflicts with execution receipt"
        )
    if str(receipt[1]) != context["pool_address"]:
        raise ValueError(
            "decision context pool conflicts with execution receipt"
        )
    if str(receipt[2]) != context["signature"]:
        raise ValueError(
            "decision context signature conflicts with execution receipt"
        )
    return True


def ingest_execution_decision_context(
    storage: Storage,
    payload: Any,
) -> ExecutionDecisionContextIngestResult:
    context = _normalize(payload)
    canonical = json.dumps(
        context,
        sort_keys=True,
        separators=(",", ":"),
    )

    with storage.connect() as conn:
        existing = conn.execute(
            """
            SELECT raw_json
            FROM live_decision_contexts
            WHERE decision_id = ?
            """,
            (context["decision_id"],),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != canonical:
                raise ValueError(
                    "decision_id already has a different immutable context"
                )
            return ExecutionDecisionContextIngestResult(
                decision_id=context["decision_id"],
                reused_existing=True,
                receipt_reconciled=_receipt_reconciles(conn, context),
            )

        reconciled = _receipt_reconciles(conn, context)
        conn.execute(
            """
            INSERT INTO live_decision_contexts(
                decision_id, mode, action, pool_address, status,
                created_at_unix, updated_at_unix,
                capital_quote, account_equity_quote,
                portfolio_deployed_quote, daily_drawdown_pct,
                min_bin_id, max_bin_id, strategy,
                expected_net_return_pct, expected_downside_pct,
                model_version, data_age_seconds, signature, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context["decision_id"],
                context["mode"],
                context["action"],
                context["pool_address"],
                context["status"],
                context["created_at_unix"],
                context["updated_at_unix"],
                context["capital_quote"],
                context["account_equity_quote"],
                context["portfolio_deployed_quote"],
                context["daily_drawdown_pct"],
                context["min_bin_id"],
                context["max_bin_id"],
                context["strategy"],
                context["expected_net_return_pct"],
                context["expected_downside_pct"],
                context["model_version"],
                context["data_age_seconds"],
                context["signature"],
                canonical,
            ),
        )

    return ExecutionDecisionContextIngestResult(
        decision_id=context["decision_id"],
        reused_existing=False,
        receipt_reconciled=reconciled,
    )
