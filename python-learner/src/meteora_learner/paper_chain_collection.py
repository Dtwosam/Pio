from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import shlex
from typing import Any

from .research_store import ResearchStore
from .storage import Storage


@dataclass(frozen=True)
class PaperChainCollectionItem:
    pool_address: str
    open_positions: tuple[str, ...]
    latest_observed_at: str | None
    age_seconds: int | None
    needs_collection: bool
    reason: str
    shell_command: str | None


@dataclass(frozen=True)
class PaperChainCollectionQueue:
    account_id: str | None
    as_of: str
    max_age_seconds: int
    array_radius: int
    open_positions_seen: int
    pools_seen: int
    pools_needing_collection: int
    items: tuple[PaperChainCollectionItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _q(value: object) -> str:
    return shlex.quote(str(value))


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _inspect_command(pool_address: str, array_radius: int) -> str:
    return (
        '(cd rust-executor && cargo run -- inspect-pool '
        f'"$RPC_URL" {_q(pool_address)} {array_radius}) '
        '| python-learner/.venv/bin/pio ingest-chain-snapshot'
    )


def build_paper_chain_collection_queue(
    storage: Storage,
    *,
    account_id: str | None = None,
    max_age_seconds: int = 300,
    array_radius: int = 1,
    as_of: str | None = None,
) -> PaperChainCollectionQueue:
    """
    Build read-only Rust collection tasks for pools behind open paper positions.

    This queue does not claim the Data API collector can provide chain state.
    Missing/stale pools get an exact inspect-pool -> ingest-chain-snapshot command.
    """
    if account_id is not None and not account_id.strip():
        raise ValueError("account_id cannot be blank")
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds cannot be negative")
    if array_radius < 0:
        raise ValueError("array_radius cannot be negative")

    now = (
        _parse_time(as_of)
        if as_of is not None
        else datetime.now(timezone.utc)
    )
    as_of_text = now.isoformat()

    params: tuple[Any, ...]
    account_clause = ""
    if account_id is None:
        params = ()
    else:
        account_clause = "AND account_id = ?"
        params = (account_id,)

    with storage.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT position_id, pool_address
            FROM paper_positions
            WHERE status = 'OPEN'
            {account_clause}
            ORDER BY pool_address ASC, position_id ASC
            """,
            params,
        ).fetchall()

    by_pool: dict[str, list[str]] = {}
    for position_id, pool_address in rows:
        by_pool.setdefault(str(pool_address), []).append(str(position_id))

    store = ResearchStore(storage.path)
    items: list[PaperChainCollectionItem] = []
    for pool_address in sorted(by_pool):
        times = store.chain_observation_times(
            pool_address,
            limit=1,
            ascending=False,
        )
        latest = times[0] if times else None
        if latest is None:
            age = None
            needs = True
            reason = "no local chain snapshot"
        else:
            latest_time = _parse_time(latest)
            age = max(0, int((now - latest_time).total_seconds()))
            needs = age > max_age_seconds
            reason = (
                f"latest chain snapshot is stale by {age} seconds"
                if needs
                else f"latest chain snapshot age {age} seconds is within limit"
            )

        items.append(
            PaperChainCollectionItem(
                pool_address=pool_address,
                open_positions=tuple(by_pool[pool_address]),
                latest_observed_at=latest,
                age_seconds=age,
                needs_collection=needs,
                reason=reason,
                shell_command=(
                    _inspect_command(pool_address, array_radius)
                    if needs
                    else None
                ),
            )
        )

    return PaperChainCollectionQueue(
        account_id=account_id,
        as_of=as_of_text,
        max_age_seconds=max_age_seconds,
        array_radius=array_radius,
        open_positions_seen=len(rows),
        pools_seen=len(items),
        pools_needing_collection=sum(item.needs_collection for item in items),
        items=tuple(items),
    )
