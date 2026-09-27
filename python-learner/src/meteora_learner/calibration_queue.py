from __future__ import annotations

from dataclasses import asdict, dataclass
import shlex
from typing import Any

from .composition_prestate import build_composition_prestate_candidates
from .composition_reconciliation import build_composition_fee_reconciliation
from .research_store import ResearchStore


@dataclass(frozen=True)
class CalibrationWorkItem:
    task_type: str
    position_address: str
    signature: str | None
    instruction_index: int | None
    reason: str
    shell_command: str | None


@dataclass(frozen=True)
class CalibrationWorkQueue:
    positions_seen: int
    inspect_transaction_tasks: int
    verify_prestate_tasks: int
    stale_decode_tasks: int
    future_prestate_samples_needed: int
    review_mismatch_tasks: int
    rebalance_decode_tasks: int
    items: tuple[CalibrationWorkItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _q(value: object) -> str:
    return shlex.quote(str(value))


def _inspect_command(signature: str) -> str:
    return (
        '(cd rust-executor && cargo run -- inspect-transaction-events-env '
        f'{_q(signature)}) '
        "| python-learner/.venv/bin/pio ingest-transaction-events"
    )


def _needs_future_prestate(reason: str | None) -> bool:
    if not reason:
        return False
    return (
        "no slot-bounded pre-add pool capture" in reason
        or "no single-context strict-prior pre-add pool capture" in reason
    )


def _verify_command(
    *,
    signature: str,
    capture_slot_start: int,
    capture_slot_end: int,
    addresses: tuple[str, ...],
    snapshot_observed_at: str,
    pool_address: str,
) -> str:
    address_args = " ".join(_q(value) for value in addresses)
    return (
        '(cd rust-executor && cargo run -- verify-prestate-env '
        f'{_q(signature)} {capture_slot_start} {capture_slot_end} '
        f"{address_args}) | python-learner/.venv/bin/pio "
        "ingest-prestate-verification "
        f"--snapshot-observed-at {_q(snapshot_observed_at)} "
        f"--pool {_q(pool_address)}"
    )


def build_calibration_work_queue(database_path: str) -> CalibrationWorkQueue:
    """
    Turn the current Phase 2 calibration gaps into concrete collection tasks.

    The queue never claims historical state can be recreated. If an add has no
    provable prestate capture, it explicitly asks for a future sample instead.
    """
    store = ResearchStore(database_path)
    positions = sorted(
        set(store.position_history_addresses(event_type="add"))
        | set(
            store.transaction_event_position_addresses(
                event_type="Rebalancing",
            )
        )
    )

    items: list[CalibrationWorkItem] = []
    seen_keys: set[tuple[object, ...]] = set()

    def add_item(item: CalibrationWorkItem) -> None:
        key = (
            item.task_type,
            item.position_address,
            item.signature,
            item.instruction_index,
            item.reason,
        )
        if key not in seen_keys:
            seen_keys.add(key)
            items.append(item)

    for position in store.position_history_addresses(event_type="add"):
        adds = store.load_position_history_events(position, event_type="add")
        for row in adds:
            signature = str(row["signature"])
            ix = int(row["ix_index"])
            tx = store.transaction_snapshot(signature)
            if tx is None:
                add_item(
                    CalibrationWorkItem(
                        task_type="INSPECT_TRANSACTION",
                        position_address=position,
                        signature=signature,
                        instruction_index=ix,
                        reason=(
                            "transaction receipt/event/request data has not been decoded"
                        ),
                        shell_command=_inspect_command(signature),
                    )
                )
                continue

            request = store.add_liquidity_request(signature, ix)
            events = store.load_transaction_events(
                signature,
                parent_ix_index=ix,
            )
            add_event = next(
                (
                    item
                    for item in events
                    if item["event_type"] == "AddLiquidity"
                    and item["position_address"] == position
                ),
                None,
            )
            if request is None or add_event is None:
                add_item(
                    CalibrationWorkItem(
                        task_type="REINSPECT_TRANSACTION",
                        position_address=position,
                        signature=signature,
                        instruction_index=ix,
                        reason=(
                            "stored transaction predates the current add request/event decoder "
                            "or uses an unsupported instruction"
                        ),
                        shell_command=_inspect_command(signature),
                    )
                )

        try:
            candidates = build_composition_prestate_candidates(
                database_path,
                position_address=position,
            )
        except ValueError:
            continue

        for candidate in candidates.candidates:
            if not candidate.eligible_for_verification:
                if _needs_future_prestate(
                    candidate.ineligibility_reason
                ):
                    add_item(
                        CalibrationWorkItem(
                            task_type="NEED_FUTURE_PRESTATE_SAMPLE",
                            position_address=position,
                            signature=candidate.signature,
                            instruction_index=candidate.parent_ix_index,
                            reason=(
                                "historical prestate cannot be reconstructed safely; "
                                "collect high-frequency pool snapshots and validate a future add"
                            ),
                            shell_command=None,
                        )
                    )
                continue

            assert candidate.snapshot_observed_at is not None
            assert candidate.capture_slot_start is not None
            assert candidate.capture_slot_end is not None
            verification = store.prestate_verification(
                candidate.signature,
                snapshot_observed_at=candidate.snapshot_observed_at,
            )
            if verification is None:
                add_item(
                    CalibrationWorkItem(
                        task_type="VERIFY_PRESTATE",
                        position_address=position,
                        signature=candidate.signature,
                        instruction_index=candidate.parent_ix_index,
                        reason=(
                            "slot-bounded candidate exists but account history gap "
                            "has not been verified"
                        ),
                        shell_command=_verify_command(
                            signature=candidate.signature,
                            capture_slot_start=candidate.capture_slot_start,
                            capture_slot_end=candidate.capture_slot_end,
                            addresses=candidate.verification_addresses,
                            snapshot_observed_at=candidate.snapshot_observed_at,
                            pool_address=candidate.pool_address,
                        ),
                    )
                )
            elif not bool(verification["eligible"]):
                add_item(
                    CalibrationWorkItem(
                        task_type="NEED_FUTURE_PRESTATE_SAMPLE",
                        position_address=position,
                        signature=candidate.signature,
                        instruction_index=candidate.parent_ix_index,
                        reason=(
                            "prestate verifier found intervening or ambiguous account activity"
                        ),
                        shell_command=None,
                    )
                )

        try:
            reconciliation = build_composition_fee_reconciliation(
                database_path,
                position_address=position,
            )
        except ValueError:
            reconciliation = None
        if reconciliation is not None:
            for entry in reconciliation.entries:
                if (
                    entry.eligible
                    and entry.sample is not None
                    and not entry.sample.exact_match
                ):
                    add_item(
                        CalibrationWorkItem(
                            task_type="REVIEW_COMPOSITION_MISMATCH",
                            position_address=position,
                            signature=entry.signature,
                            instruction_index=entry.parent_ix_index,
                            reason=(
                                "verified prestate composition formula does not match "
                                "the emitted CompositionFee event"
                            ),
                            shell_command=None,
                        )
                    )

    for position in store.transaction_event_position_addresses(
        event_type="Rebalancing",
    ):
        events = store.load_position_transaction_events(
            position,
            event_type="Rebalancing",
        )
        for event in events:
            signature = str(event["signature"])
            ix = int(event["parent_ix_index"])
            if store.rebalance_request(signature, ix) is None:
                add_item(
                    CalibrationWorkItem(
                        task_type="REINSPECT_REBALANCE_TRANSACTION",
                        position_address=position,
                        signature=signature,
                        instruction_index=ix,
                        reason=(
                            "rebalance event exists but current execution-bound decode is missing"
                        ),
                        shell_command=_inspect_command(signature),
                    )
                )

    return CalibrationWorkQueue(
        positions_seen=len(positions),
        inspect_transaction_tasks=sum(
            item.task_type == "INSPECT_TRANSACTION" for item in items
        ),
        verify_prestate_tasks=sum(
            item.task_type == "VERIFY_PRESTATE" for item in items
        ),
        stale_decode_tasks=sum(
            item.task_type == "REINSPECT_TRANSACTION" for item in items
        ),
        future_prestate_samples_needed=sum(
            item.task_type == "NEED_FUTURE_PRESTATE_SAMPLE" for item in items
        ),
        review_mismatch_tasks=sum(
            item.task_type == "REVIEW_COMPOSITION_MISMATCH" for item in items
        ),
        rebalance_decode_tasks=sum(
            item.task_type == "REINSPECT_REBALANCE_TRANSACTION" for item in items
        ),
        items=tuple(items),
    )
