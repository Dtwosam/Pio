from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .research_store import ResearchStore


@dataclass(frozen=True)
class CompositionFeeLabel:
    position_address: str
    signature: str
    parent_ix_index: int
    api_amount_x: str
    api_amount_y: str
    chain_amount_x: str | None
    chain_amount_y: str | None
    active_bin_id: int | None
    composition_event_count: int
    composition_fee_x: int
    composition_fee_y: int
    protocol_fee_x: int
    protocol_fee_y: int
    chain_add_event_found: bool
    exact_amount_string_match: bool | None


@dataclass(frozen=True)
class CompositionFeeLabelReport:
    position_address: str
    add_events: int
    chain_add_events_found: int
    composition_labeled_adds: int
    exact_amount_string_matches: int
    labels: tuple[CompositionFeeLabel, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_integer_string(value: str) -> str | None:
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("+"):
        text = text[1:]
    if not text.isdigit():
        return None
    return str(int(text))


def build_composition_fee_labels(
    database_path: str,
    *,
    position_address: str,
) -> CompositionFeeLabelReport:
    store = ResearchStore(database_path)
    history = store.load_position_history_events(
        position_address,
        event_type="add",
    )
    if not history:
        raise ValueError(f"no stored add events for position {position_address}")

    labels: list[CompositionFeeLabel] = []
    for row in history:
        signature = str(row["signature"])
        parent_ix_index = int(row["ix_index"])
        chain_events = store.load_transaction_events(
            signature,
            parent_ix_index=parent_ix_index,
        )
        add = next(
            (
                item
                for item in chain_events
                if item["event_type"] == "AddLiquidity"
                and item["position_address"] == position_address
            ),
            None,
        )
        composition = [
            item
            for item in chain_events
            if item["event_type"] == "CompositionFee"
        ]

        api_x = str(row["amount_x"])
        api_y = str(row["amount_y"])
        chain_x = str(add["amount_x"]) if add is not None else None
        chain_y = str(add["amount_y"]) if add is not None else None

        api_x_int = _canonical_integer_string(api_x)
        api_y_int = _canonical_integer_string(api_y)
        exact_match: bool | None = None
        if add is not None and api_x_int is not None and api_y_int is not None:
            exact_match = (
                api_x_int == _canonical_integer_string(chain_x or "")
                and api_y_int == _canonical_integer_string(chain_y or "")
            )

        labels.append(
            CompositionFeeLabel(
                position_address=position_address,
                signature=signature,
                parent_ix_index=parent_ix_index,
                api_amount_x=api_x,
                api_amount_y=api_y,
                chain_amount_x=chain_x,
                chain_amount_y=chain_y,
                active_bin_id=int(add["active_bin_id"]) if add is not None else None,
                composition_event_count=len(composition),
                composition_fee_x=sum(
                    int(str(item["token_x_fee_amount"])) for item in composition
                ),
                composition_fee_y=sum(
                    int(str(item["token_y_fee_amount"])) for item in composition
                ),
                protocol_fee_x=sum(
                    int(str(item["protocol_token_x_fee_amount"])) for item in composition
                ),
                protocol_fee_y=sum(
                    int(str(item["protocol_token_y_fee_amount"])) for item in composition
                ),
                chain_add_event_found=add is not None,
                exact_amount_string_match=exact_match,
            )
        )

    return CompositionFeeLabelReport(
        position_address=position_address,
        add_events=len(labels),
        chain_add_events_found=sum(item.chain_add_event_found for item in labels),
        composition_labeled_adds=sum(
            item.composition_event_count > 0 for item in labels
        ),
        exact_amount_string_matches=sum(
            item.exact_amount_string_match is True for item in labels
        ),
        labels=tuple(labels),
    )
