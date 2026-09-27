from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from .research_store import ResearchStore
from .storage import Storage


SUPPORTED_STRATEGY_VARIANTS = {6: "SPOT", 7: "CURVE", 8: "BID_ASK"}
SUPPORTED_EXPLICIT_TYPES = {"add_liquidity", "add_liquidity2"}
SUPPORTED_STRATEGY_TYPES = {
    "add_liquidity_by_strategy",
    "add_liquidity_by_strategy2",
}


@dataclass(frozen=True)
class CompositionPrestateCandidate:
    position_address: str
    signature: str
    parent_ix_index: int
    pool_address: str
    transaction_slot: int | None
    transaction_block_time: int | None
    active_bin_id: int | None
    snapshot_observed_at: str | None
    capture_slot_start: int | None
    capture_slot_end: int | None
    bin_array_address: str | None
    instruction_type: str | None
    strategy_variant: int | None
    verification_addresses: tuple[str, ...]
    eligible_for_verification: bool
    ineligibility_reason: str | None


@dataclass(frozen=True)
class CompositionPrestateReport:
    position_address: str
    add_events: int
    verification_ready: int
    candidates: tuple[CompositionPrestateCandidate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrestateVerificationIngestResult:
    signature: str
    snapshot_observed_at: str
    eligible: bool


def _request_support_reason(request: dict[str, Any]) -> str | None:
    instruction_type = str(request["instruction_type"])
    if instruction_type in SUPPORTED_EXPLICIT_TYPES:
        if not str(request.get("explicit_distribution_json") or "[]").strip():
            return "explicit distribution metadata missing"
        return None

    if instruction_type in SUPPORTED_STRATEGY_TYPES:
        variant = request.get("strategy_variant")
        if variant is None or int(variant) not in SUPPORTED_STRATEGY_VARIANTS:
            return "strategy variant is not an exact imbalanced SPOT/CURVE/BID_ASK variant"
        if request.get("min_bin_id") is None or request.get("max_bin_id") is None:
            return "strategy range metadata missing"
        if request.get("strategy_favor_x") is None:
            return "strategy active-bin side metadata missing"
        return None

    return f"unsupported exact allocation instruction: {instruction_type}"


def build_composition_prestate_candidates(
    database_path: str,
    *,
    position_address: str,
) -> CompositionPrestateReport:
    store = ResearchStore(database_path)
    history = store.load_position_history_events(
        position_address,
        event_type="add",
    )
    if not history:
        raise ValueError(f"no stored add events for position {position_address}")

    candidates: list[CompositionPrestateCandidate] = []
    for row in history:
        signature = str(row["signature"])
        parent_ix_index = int(row["ix_index"])
        pool_address = str(row["pool_address"])
        tx = store.transaction_snapshot(signature)
        request = store.add_liquidity_request(signature, parent_ix_index)
        chain_events = store.load_transaction_events(
            signature,
            parent_ix_index=parent_ix_index,
        )
        add_event = next(
            (
                item
                for item in chain_events
                if item["event_type"] == "AddLiquidity"
                and item["position_address"] == position_address
            ),
            None,
        )

        reason: str | None = None
        transaction_slot: int | None = None
        block_time: int | None = None
        active_bin_id: int | None = None
        capture = None
        bin_row = None

        if tx is None:
            reason = "decoded Solana transaction snapshot missing"
        else:
            transaction_slot = int(tx["slot"])
            if tx.get("block_time") is None:
                reason = "transaction block_time missing"
            else:
                block_time = int(tx["block_time"])
            if tx.get("succeeded") is not None and not bool(tx["succeeded"]):
                reason = "target transaction did not succeed"

        if reason is None and add_event is None:
            reason = "AddLiquidity event decode missing"
        if add_event is not None:
            active_bin_id = int(add_event["active_bin_id"])
            event_pool = add_event.get("lb_pair")
            if event_pool is not None and str(event_pool) != pool_address:
                reason = "Data API pool and AddLiquidity event pool disagree"

        if reason is None and request is None:
            reason = "add-liquidity request decode missing"
        if reason is None and request is not None:
            reason = _request_support_reason(request)

        if (
            reason is None
            and transaction_slot is not None
            and active_bin_id is not None
        ):
            capture = store.pool_capture_before_slot(
                pool_address,
                target_slot=transaction_slot,
                active_bin_id=active_bin_id,
                require_single_context=True,
            )
            if capture is None:
                legacy_capture = store.pool_capture_before_slot(
                    pool_address,
                    target_slot=transaction_slot,
                    active_bin_id=active_bin_id,
                )
                if legacy_capture is None:
                    reason = (
                        "no slot-bounded pre-add pool capture with matching "
                        "active bin"
                    )
                else:
                    reason = (
                        "no single-context strict-prior pre-add pool capture "
                        "with matching active bin"
                    )

        if reason is None and capture is not None:
            if (
                str(capture.get("token_x_program")) != STANDARD_SPL_TOKEN_PROGRAM
                or str(capture.get("token_y_program")) != STANDARD_SPL_TOKEN_PROGRAM
            ):
                reason = "exact composition reconciliation currently requires standard SPL tokens"
            required_fee_fields = (
                "fee_base_factor",
                "fee_filter_period",
                "fee_decay_period",
                "fee_reduction_factor",
                "fee_variable_fee_control",
                "fee_max_volatility_accumulator",
                "fee_base_fee_power_factor",
                "fee_volatility_accumulator",
                "fee_volatility_reference",
                "fee_index_reference",
                "fee_last_update_timestamp",
            )
            if reason is None and any(capture.get(key) is None for key in required_fee_fields):
                reason = "pre-add capture is missing raw fee-state metadata"

        if reason is None and capture is not None and active_bin_id is not None:
            bin_row = store.bin_liquidity_at(
                pool_address,
                observed_at=str(capture["observed_at"]),
                bin_id=active_bin_id,
            )
            if bin_row is None:
                reason = "pre-add capture does not contain the active bin"
            elif not bin_row.get("bin_array_address"):
                reason = "pre-add active bin is missing its bin-array address"

        addresses: tuple[str, ...] = ()
        if reason is None and bin_row is not None:
            addresses = (
                pool_address,
                str(bin_row["bin_array_address"]),
            )

        candidates.append(
            CompositionPrestateCandidate(
                position_address=position_address,
                signature=signature,
                parent_ix_index=parent_ix_index,
                pool_address=pool_address,
                transaction_slot=transaction_slot,
                transaction_block_time=block_time,
                active_bin_id=active_bin_id,
                snapshot_observed_at=(
                    str(capture["observed_at"]) if capture is not None else None
                ),
                capture_slot_start=(
                    int(capture["capture_slot_start"])
                    if capture is not None
                    and capture.get("capture_slot_start") is not None
                    else None
                ),
                capture_slot_end=(
                    int(capture["capture_slot_end"])
                    if capture is not None
                    and capture.get("capture_slot_end") is not None
                    else None
                ),
                bin_array_address=(
                    str(bin_row["bin_array_address"])
                    if bin_row is not None
                    and bin_row.get("bin_array_address") is not None
                    else None
                ),
                instruction_type=(
                    str(request["instruction_type"])
                    if request is not None
                    else None
                ),
                strategy_variant=(
                    int(request["strategy_variant"])
                    if request is not None
                    and request.get("strategy_variant") is not None
                    else None
                ),
                verification_addresses=addresses,
                eligible_for_verification=reason is None,
                ineligibility_reason=reason,
            )
        )

    return CompositionPrestateReport(
        position_address=position_address,
        add_events=len(candidates),
        verification_ready=sum(item.eligible_for_verification for item in candidates),
        candidates=tuple(candidates),
    )


def ingest_prestate_verification(
    storage: Storage,
    payload: Any,
    *,
    snapshot_observed_at: str,
    pool_address: str,
) -> PrestateVerificationIngestResult:
    if not isinstance(payload, dict):
        raise ValueError("prestate verification must be a JSON object")
    required = {
        "signature",
        "transaction_slot",
        "capture_slot_start",
        "capture_slot_end",
        "eligible",
        "reasons",
        "account_checks",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"prestate verification missing fields: {missing}")

    storage.save_composition_prestate_verification(
        payload,
        snapshot_observed_at=snapshot_observed_at,
        pool_address=pool_address,
    )
    return PrestateVerificationIngestResult(
        signature=str(payload["signature"]),
        snapshot_observed_at=snapshot_observed_at,
        eligible=bool(payload["eligible"]),
    )
