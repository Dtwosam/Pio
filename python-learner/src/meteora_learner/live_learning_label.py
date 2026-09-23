from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from typing import Any

from .storage import Storage, utc_now_iso


BPS_PER_PERCENT = Decimal("100")


@dataclass(frozen=True)
class LiveLearningLabel:
    position_address: str
    decision_id: str
    pool_address: str
    model_version: str
    strategy: str
    min_bin_id: int
    max_bin_id: int
    range_width_bins: int
    proposed_capital_quote: str
    expected_net_return_pct: str
    expected_downside_pct: str
    realized_pnl_quote: str
    realized_return_bps: int
    prediction_error_bps: int
    target_positive_return: int
    quote_unit: str
    opened_signature: str
    closed_decision_id: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveLearningLabelBuildResult:
    label: LiveLearningLabel
    reused_existing: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "label": self.label.to_record(),
            "reused_existing": self.reused_existing,
        }


def _d(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("live learning label encountered non-finite value")
    return parsed


def build_live_learning_label(
    storage: Storage,
    *,
    position_address: str,
) -> LiveLearningLabelBuildResult:
    if not position_address.strip():
        raise ValueError("position_address is required")

    with storage.connect() as conn:
        position = conn.execute(
            """
            SELECT pool_address, status, opened_decision_id,
                   opened_signature, closed_decision_id
            FROM live_positions
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if position is None:
            raise ValueError(f"unknown live position: {position_address}")
        (
            pool_address,
            status,
            opened_decision_id,
            opened_signature,
            closed_decision_id,
        ) = position
        if str(status) != "CLOSED":
            raise ValueError(
                "live learning label requires CLOSED position state"
            )
        if closed_decision_id is None:
            raise ValueError(
                "closed live position is missing closed_decision_id"
            )

        valuation = conn.execute(
            """
            SELECT quote_unit, realized_pnl_quote,
                   realized_return_bps, opened_decision_id,
                   closed_decision_id
            FROM live_position_valuations
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if valuation is None:
            raise ValueError(
                "live learning label requires valued position outcome"
            )
        if str(valuation[3]) != str(opened_decision_id):
            raise ValueError(
                "valuation opening decision differs from live position"
            )
        if str(valuation[4]) != str(closed_decision_id):
            raise ValueError(
                "valuation closing decision differs from live position"
            )

        context = conn.execute(
            """
            SELECT mode, action, pool_address, status,
                   capital_quote, min_bin_id, max_bin_id,
                   strategy, expected_net_return_pct,
                   expected_downside_pct, model_version, signature
            FROM live_decision_contexts
            WHERE decision_id = ?
            """,
            (str(opened_decision_id),),
        ).fetchone()
        if context is None:
            raise ValueError(
                "live learning label requires opening decision context"
            )
        (
            mode,
            action,
            context_pool,
            context_status,
            capital_quote,
            context_min,
            context_max,
            strategy,
            expected_return,
            expected_downside,
            model_version,
            context_signature,
        ) = context

        if str(mode) != "LIVE" or str(action) != "ENTER":
            raise ValueError(
                "opening decision context must be LIVE ENTER"
            )
        if str(context_status) != "CONFIRMED":
            raise ValueError(
                "opening decision context must be CONFIRMED"
            )
        if str(context_pool) != str(pool_address):
            raise ValueError(
                "opening decision context pool differs from live position"
            )
        if str(context_signature) != str(opened_signature):
            raise ValueError(
                "opening decision context signature differs from live position"
            )

        enter_event = conn.execute(
            """
            SELECT signature, min_bin_id, max_bin_id
            FROM live_position_events
            WHERE decision_id = ?
              AND position_address = ?
              AND action = 'ENTER'
            """,
            (str(opened_decision_id), position_address),
        ).fetchone()
        if enter_event is None:
            raise ValueError(
                "live learning label requires persisted ENTER lifecycle event"
            )
        if str(enter_event[0]) != str(opened_signature):
            raise ValueError(
                "ENTER lifecycle signature differs from live position"
            )
        if enter_event[1] is None or enter_event[2] is None:
            raise ValueError("ENTER lifecycle event is missing range")
        event_min = int(enter_event[1])
        event_max = int(enter_event[2])
        if int(context_min) != event_min or int(context_max) != event_max:
            raise ValueError(
                "opening decision range differs from confirmed ENTER range"
            )

        proposed_capital = _d(capital_quote)
        if proposed_capital <= 0:
            raise ValueError(
                "confirmed ENTER learning label requires positive capital"
            )
        expected_return_decimal = _d(expected_return)
        expected_downside_decimal = _d(expected_downside)
        realized_pnl = _d(valuation[1])
        realized_return_bps = int(valuation[2])
        expected_return_bps = int(
            expected_return_decimal * BPS_PER_PERCENT
        )
        prediction_error_bps = (
            realized_return_bps - expected_return_bps
        )

        label = LiveLearningLabel(
            position_address=position_address,
            decision_id=str(opened_decision_id),
            pool_address=str(pool_address),
            model_version=str(model_version),
            strategy=str(strategy),
            min_bin_id=event_min,
            max_bin_id=event_max,
            range_width_bins=event_max - event_min + 1,
            proposed_capital_quote=format(proposed_capital, "f"),
            expected_net_return_pct=format(
                expected_return_decimal,
                "f",
            ),
            expected_downside_pct=format(
                expected_downside_decimal,
                "f",
            ),
            realized_pnl_quote=format(realized_pnl, "f"),
            realized_return_bps=realized_return_bps,
            prediction_error_bps=prediction_error_bps,
            target_positive_return=int(realized_pnl > 0),
            quote_unit=str(valuation[0]),
            opened_signature=str(opened_signature),
            closed_decision_id=str(closed_decision_id),
        )
        canonical = json.dumps(
            label.to_record(),
            sort_keys=True,
            separators=(",", ":"),
        )

        existing = conn.execute(
            """
            SELECT raw_json
            FROM live_learning_labels
            WHERE position_address = ?
            """,
            (position_address,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != canonical:
                raise ValueError(
                    "position already has a different immutable learning label"
                )
            return LiveLearningLabelBuildResult(
                label=label,
                reused_existing=True,
            )

        decision_owner = conn.execute(
            """
            SELECT position_address
            FROM live_learning_labels
            WHERE decision_id = ?
            """,
            (str(opened_decision_id),),
        ).fetchone()
        if decision_owner is not None:
            raise ValueError(
                "opening decision is already linked to another live label"
            )

        conn.execute(
            """
            INSERT INTO live_learning_labels(
                position_address, decision_id, pool_address,
                model_version, strategy,
                min_bin_id, max_bin_id, range_width_bins,
                proposed_capital_quote,
                expected_net_return_pct, expected_downside_pct,
                realized_pnl_quote, realized_return_bps,
                prediction_error_bps, target_positive_return,
                quote_unit, opened_signature, closed_decision_id,
                created_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                label.position_address,
                label.decision_id,
                label.pool_address,
                label.model_version,
                label.strategy,
                label.min_bin_id,
                label.max_bin_id,
                label.range_width_bins,
                label.proposed_capital_quote,
                label.expected_net_return_pct,
                label.expected_downside_pct,
                label.realized_pnl_quote,
                label.realized_return_bps,
                label.prediction_error_bps,
                label.target_positive_return,
                label.quote_unit,
                label.opened_signature,
                label.closed_decision_id,
                utc_now_iso(),
                canonical,
            ),
        )

    return LiveLearningLabelBuildResult(
        label=label,
        reused_existing=False,
    )
