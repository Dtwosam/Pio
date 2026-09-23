from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .meteora_api import MeteoraDataAPI
from .phase9_position_discovery import (
    Phase9PositionDiscoveryReport,
    discover_pool_positions_with_rust,
)
from .position_history import collect_position_history
from .settings import Settings
from .storage import Storage
from .wallet_flow import WalletFlowCriteria


DiscoverPositions = Callable[[str, int], Phase9PositionDiscoveryReport]
CollectHistory = Callable[[str], int]


@dataclass(frozen=True)
class Phase9WalletFlowSourceState:
    pool_address: str
    events: int
    unique_users: int
    positions: int
    ready: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9WalletFlowCaptureItem:
    position_address: str
    owner: str
    status: str
    events_received: int | None
    error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase9WalletFlowCaptureReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    source_scope: str
    pool_address: str
    as_of: str | None
    source_before: Phase9WalletFlowSourceState
    source_after: Phase9WalletFlowSourceState
    positions_found: int
    positions_returned: int
    discovery_truncated: bool
    discovery_unique_owners: int
    positions_attempted: int
    positions_refreshed: int
    positions_failed: int
    items: tuple[Phase9WalletFlowCaptureItem, ...]
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def wallet_flow_source_state(
    storage: Storage,
    *,
    pool_address: str,
    criteria: WalletFlowCriteria = WalletFlowCriteria(),
    as_of: str | None = None,
) -> Phase9WalletFlowSourceState:
    if not pool_address.strip():
        raise ValueError("pool_address is required")

    with storage.connect() as conn:
        if as_of is None:
            row = conn.execute(
                """
                SELECT COUNT(*),
                       COUNT(DISTINCT user_address),
                       COUNT(DISTINCT position_address)
                FROM position_event_history
                WHERE pool_address = ?
                """,
                (pool_address,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*),
                       COUNT(DISTINCT user_address),
                       COUNT(DISTINCT position_address)
                FROM position_event_history
                WHERE pool_address = ?
                  AND julianday(created_at) <= julianday(?)
                """,
                (pool_address, as_of),
            ).fetchone()
    events = int(row[0] or 0)
    users = int(row[1] or 0)
    positions = int(row[2] or 0)
    return Phase9WalletFlowSourceState(
        pool_address=pool_address,
        events=events,
        unique_users=users,
        positions=positions,
        ready=(
            events >= criteria.min_events
            and users >= criteria.min_unique_users
        ),
    )


def _existing_source_members(
    storage: Storage,
    *,
    pool_address: str,
    as_of: str | None,
) -> tuple[set[str], set[str]]:
    with storage.connect() as conn:
        if as_of is None:
            rows = conn.execute(
                """
                SELECT DISTINCT position_address, user_address
                FROM position_event_history
                WHERE pool_address = ?
                """,
                (pool_address,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT position_address, user_address
                FROM position_event_history
                WHERE pool_address = ?
                  AND julianday(created_at) <= julianday(?)
                """,
                (pool_address, as_of),
            ).fetchall()
    return (
        {str(row[0]) for row in rows},
        {str(row[1]) for row in rows},
    )


def run_phase9_wallet_flow_capture(
    storage: Storage,
    *,
    pool_address: str,
    criteria: WalletFlowCriteria = WalletFlowCriteria(),
    discovery_limit: int = 250,
    max_positions_per_run: int = 50,
    as_of: str | None = None,
    settings: Settings | None = None,
    discover_positions: DiscoverPositions | None = None,
    collect_history: CollectHistory | None = None,
    rust_manifest_path: str | None = None,
    rust_binary_path: str | None = None,
    timeout_seconds: int = 120,
) -> Phase9WalletFlowCaptureReport:
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if discovery_limit < 1 or discovery_limit > 5_000:
        raise ValueError("discovery_limit must be between 1 and 5000")
    if max_positions_per_run < 1:
        raise ValueError("max_positions_per_run must be positive")

    before = wallet_flow_source_state(
        storage,
        pool_address=pool_address,
        criteria=criteria,
        as_of=as_of,
    )
    if before.ready:
        return Phase9WalletFlowCaptureReport(
            research_only=True,
            read_only_capture=True,
            policy_actionable=False,
            execution_wired=False,
            source_scope="CURRENT_ONCHAIN_POSITION_COHORT",
            pool_address=pool_address,
            as_of=as_of,
            source_before=before,
            source_after=before,
            positions_found=0,
            positions_returned=0,
            discovery_truncated=False,
            discovery_unique_owners=0,
            positions_attempted=0,
            positions_refreshed=0,
            positions_failed=0,
            items=(),
            reasons=("wallet-flow source thresholds are already satisfied",),
        )

    if discover_positions is None:
        def discover(pool: str, limit: int) -> Phase9PositionDiscoveryReport:
            return discover_pool_positions_with_rust(
                pool,
                limit=limit,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
            )
    else:
        discover = discover_positions

    discovery = discover(pool_address, discovery_limit)
    if discovery.pool_address != pool_address:
        raise ValueError("position discovery returned a different pool")

    existing_positions, existing_users = _existing_source_members(
        storage,
        pool_address=pool_address,
        as_of=as_of,
    )
    candidates = sorted(
        discovery.positions,
        key=lambda item: (
            item.owner in existing_users,
            item.position_address in existing_positions,
            item.owner,
            item.position_address,
        ),
    )[:max_positions_per_run]

    items: list[Phase9WalletFlowCaptureItem] = []
    refreshed = 0
    failed = 0

    api: MeteoraDataAPI | None = None
    if collect_history is None:
        current_settings = settings or Settings.from_env()
        api = MeteoraDataAPI(
            base_url=current_settings.meteora_data_api,
            requests_per_second=current_settings.requests_per_second,
        )

        def fetch(position: str) -> int:
            assert api is not None
            return collect_position_history(
                storage,
                api,
                position,
            ).events
    else:
        fetch = collect_history

    try:
        for candidate in candidates:
            current = wallet_flow_source_state(
                storage,
                pool_address=pool_address,
                criteria=criteria,
                as_of=as_of,
            )
            if current.ready:
                break
            try:
                events_received = int(fetch(candidate.position_address))
                refreshed += 1
                items.append(
                    Phase9WalletFlowCaptureItem(
                        position_address=candidate.position_address,
                        owner=candidate.owner,
                        status="REFRESHED",
                        events_received=events_received,
                        error=None,
                    )
                )
            except Exception as exc:
                failed += 1
                items.append(
                    Phase9WalletFlowCaptureItem(
                        position_address=candidate.position_address,
                        owner=candidate.owner,
                        status="FAILED",
                        events_received=None,
                        error=str(exc)[:2000],
                    )
                )
    finally:
        if api is not None:
            api.close()

    after = wallet_flow_source_state(
        storage,
        pool_address=pool_address,
        criteria=criteria,
        as_of=as_of,
    )
    reasons: list[str] = []
    if discovery.truncated:
        reasons.append(
            "current on-chain position discovery was truncated by the "
            "configured limit"
        )
    if not discovery.positions:
        reasons.append(
            "no current on-chain PositionV2 accounts were discovered "
            "for the pool"
        )
    if failed:
        reasons.append(
            f"{failed} position history collection(s) failed"
        )
    if not after.ready:
        reasons.append(
            f"wallet-flow source remains below threshold: "
            f"events {after.events}/{criteria.min_events}, "
            f"unique users {after.unique_users}/{criteria.min_unique_users}"
        )
        reasons.append(
            "the current-position cohort is not a complete historical "
            "pool census; additional known/closed positions may be required"
        )

    return Phase9WalletFlowCaptureReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        source_scope="CURRENT_ONCHAIN_POSITION_COHORT",
        pool_address=pool_address,
        as_of=as_of,
        source_before=before,
        source_after=after,
        positions_found=discovery.positions_found,
        positions_returned=discovery.positions_returned,
        discovery_truncated=discovery.truncated,
        discovery_unique_owners=discovery.unique_owners,
        positions_attempted=len(items),
        positions_refreshed=refreshed,
        positions_failed=failed,
        items=tuple(items),
        reasons=tuple(reasons),
    )
