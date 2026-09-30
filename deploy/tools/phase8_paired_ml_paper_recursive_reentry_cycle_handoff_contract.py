from __future__ import annotations

import hashlib
import json
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_REENTRY_CYCLE_HANDOFF_V1"
)

DEBT_MORE_EVIDENCE = "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
ROUTE_NEXT_PAIR = (
    "PHASE8_PAIRED_ML_PAPER_RECURSIVE_ROLLOVER_REENTRY_CYCLE_NEXT_PAIR_REVIEW"
)

HANDOFF_FIELDS = (
    "format_version",
    "artifact_type",
    "source_final_evaluation_sha256",
    "source_prior_final_evaluation_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "pio_wal_sha256",
    "pio_shm_sha256",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "previous_pair_id",
    "previous_pool_address",
    "previous_entry_observed_at",
    "previous_final_settlement_observed_at",
    "previous_incumbent_position_id",
    "previous_challenger_position_id",
    "previous_incumbent_closed_trades",
    "previous_challenger_closed_trades",
    "required_incumbent_closed_trades",
    "required_challenger_closed_trades",
    "pair_both_closed",
    "closed_trade_floor_met",
    "next_debt_type",
    "continuation_route",
    "next_pair_review_ready",
    "promotion_review_ready",
    "validation_review_ready",
    "separate_next_action_authorization_required",
    "read_only",
    "new_pair_entry_authorized",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_hex_digest(value: Any, length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _require_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_count(value: Any, *, label: str, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if positive and value <= 0:
        raise ValueError(f"{label} must be positive")
    if not positive and value < 0:
        raise ValueError(f"{label} cannot be negative")
    return value


def validate_phase8_paired_ml_paper_recursive_reentry_cycle_handoff(
    handoff: dict[str, Any],
) -> None:
    if not isinstance(handoff, dict):
        raise ValueError("recursive re-entry cycle handoff must be an object")
    if set(handoff) != set(HANDOFF_FIELDS) | {"handoff_sha256"}:
        raise ValueError("recursive re-entry cycle handoff schema mismatch")
    if handoff.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported recursive re-entry cycle handoff format")
    if handoff.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected recursive re-entry cycle handoff type")

    for field in (
        "source_final_evaluation_sha256",
        "source_prior_final_evaluation_sha256",
        "pio_database_sha256",
    ):
        if not _is_hex_digest(handoff.get(field)):
            raise ValueError(f"recursive re-entry cycle handoff {field} is invalid")
    for field in ("pio_wal_sha256", "pio_shm_sha256"):
        value = handoff.get(field)
        if value is not None and not _is_hex_digest(value):
            raise ValueError(f"recursive re-entry cycle handoff {field} is invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_id",
        "previous_pair_id",
        "previous_pool_address",
        "previous_entry_observed_at",
        "previous_final_settlement_observed_at",
        "previous_incumbent_position_id",
        "previous_challenger_position_id",
    ):
        _require_string(handoff.get(field), label=field)

    incumbent_closed = _require_count(
        handoff.get("previous_incumbent_closed_trades"),
        label="previous_incumbent_closed_trades",
    )
    challenger_closed = _require_count(
        handoff.get("previous_challenger_closed_trades"),
        label="previous_challenger_closed_trades",
    )
    incumbent_required = _require_count(
        handoff.get("required_incumbent_closed_trades"),
        label="required_incumbent_closed_trades",
        positive=True,
    )
    challenger_required = _require_count(
        handoff.get("required_challenger_closed_trades"),
        label="required_challenger_closed_trades",
        positive=True,
    )
    if incumbent_closed >= incumbent_required and challenger_closed >= challenger_required:
        raise ValueError(
            "recursive re-entry cycle handoff does not require more evidence"
        )

    if handoff.get("pair_both_closed") is not True:
        raise ValueError("recursive re-entry cycle handoff requires both prior legs closed")
    if handoff.get("closed_trade_floor_met") is not False:
        raise ValueError(
            "recursive re-entry cycle handoff requires closed-trade floor unmet"
        )
    if handoff.get("next_debt_type") != DEBT_MORE_EVIDENCE:
        raise ValueError("recursive re-entry cycle handoff debt mismatch")
    if handoff.get("continuation_route") != ROUTE_NEXT_PAIR:
        raise ValueError("recursive re-entry cycle handoff route mismatch")
    if handoff.get("next_pair_review_ready") is not True:
        raise ValueError("recursive re-entry cycle handoff is not ready for next-pair review")
    if handoff.get("promotion_review_ready") is not False:
        raise ValueError("recursive re-entry cycle handoff cannot be promotion-ready")
    if handoff.get("validation_review_ready") is not False:
        raise ValueError("recursive re-entry cycle handoff cannot be validation-ready")
    if handoff.get("separate_next_action_authorization_required") is not True:
        raise ValueError(
            "recursive re-entry cycle handoff requires separate next-action authorization"
        )
    if handoff.get("read_only") is not True:
        raise ValueError("recursive re-entry cycle handoff must be read-only")

    for field in (
        "new_pair_entry_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
    ):
        if handoff.get(field) is not False:
            raise ValueError(
                f"recursive re-entry cycle handoff requires {field}=false"
            )

    identity = {field: handoff[field] for field in HANDOFF_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if handoff.get("handoff_sha256") != expected:
        raise ValueError("recursive re-entry cycle handoff digest mismatch")
