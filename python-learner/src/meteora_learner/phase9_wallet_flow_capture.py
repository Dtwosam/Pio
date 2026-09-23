from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .meteora_api import MeteoraDataAPI
from .phase9_position_discovery import (
    Phase9PositionDiscoveryReport,
    discover_pool_positions_with_rust,
)
from .phase9_pool_activity_discovery import (
    Phase9PoolActivityDiscoveryReport,
    discover_historical_pool_activity_with_rust,
)
from .phase9_pool_activity_scan_state import (
    Phase9PoolActivityScanState,
    phase9_pool_activity_scan_state,
    record_phase9_pool_activity_page,
)
from .position_history import collect_position_history
from .settings import Settings
from .storage import Storage
from .wallet_flow import WalletFlowCriteria


DiscoverPositions = Callable[[str, int], Phase9PositionDiscoveryReport]
CollectHistory = Callable[[str], int]
ExpandOwnerPositions = Callable[[str, str], tuple[str, ...]]
DiscoverHistoricalActivity = Callable[
    [str, int, str | None],
    Phase9PoolActivityDiscoveryReport,
]


@dataclass(frozen=True)
class _WalletFlowPositionCandidate:
    position_address: str
    owner: str
    source: str


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
    source: str
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
    owners_expanded: int
    owner_expansion_failures: int
    expanded_positions_added: int
    historical_scan_enabled: bool
    historical_recent_signatures_scanned: int
    historical_backfill_signatures_scanned: int
    historical_matching_transactions: int
    historical_positions_added: int
    historical_scan_failures: int
    historical_scan_state: Phase9PoolActivityScanState | None
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
                FROM (
                    SELECT user_address, position_address
                    FROM position_event_history
                    WHERE pool_address = ?
                    ORDER BY julianday(created_at) DESC, id DESC
                    LIMIT ?
                )
                """,
                (pool_address, criteria.lookback_events),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*),
                       COUNT(DISTINCT user_address),
                       COUNT(DISTINCT position_address)
                FROM (
                    SELECT user_address, position_address
                    FROM position_event_history
                    WHERE pool_address = ?
                      AND julianday(created_at) <= julianday(?)
                    ORDER BY julianday(created_at) DESC, id DESC
                    LIMIT ?
                )
                """,
                (
                    pool_address,
                    as_of,
                    criteria.lookback_events,
                ),
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
    criteria: WalletFlowCriteria,
    as_of: str | None,
) -> tuple[set[str], set[str]]:
    with storage.connect() as conn:
        if as_of is None:
            rows = conn.execute(
                """
                SELECT DISTINCT position_address, user_address
                FROM (
                    SELECT position_address, user_address
                    FROM position_event_history
                    WHERE pool_address = ?
                    ORDER BY julianday(created_at) DESC, id DESC
                    LIMIT ?
                )
                """,
                (pool_address, criteria.lookback_events),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT position_address, user_address
                FROM (
                    SELECT position_address, user_address
                    FROM position_event_history
                    WHERE pool_address = ?
                      AND julianday(created_at) <= julianday(?)
                    ORDER BY julianday(created_at) DESC, id DESC
                    LIMIT ?
                )
                """,
                (
                    pool_address,
                    as_of,
                    criteria.lookback_events,
                ),
            ).fetchall()
    return (
        {str(row[0]) for row in rows},
        {str(row[1]) for row in rows},
    )


def _diversity_order(
    candidates: list[_WalletFlowPositionCandidate],
    *,
    existing_positions: set[str],
    existing_users: set[str],
) -> list[_WalletFlowPositionCandidate]:
    grouped: dict[str, list[_WalletFlowPositionCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.owner, []).append(candidate)

    for owner in grouped:
        grouped[owner].sort(
            key=lambda item: (
                item.position_address in existing_positions,
                0 if item.source == "ONCHAIN_CURRENT" else 1,
                item.position_address,
            )
        )

    owners = sorted(
        grouped,
        key=lambda owner: (
            owner in existing_users,
            owner,
        ),
    )
    ordered: list[_WalletFlowPositionCandidate] = []
    offset = 0
    while True:
        added = False
        for owner in owners:
            values = grouped[owner]
            if offset < len(values):
                ordered.append(values[offset])
                added = True
        if not added:
            break
        offset += 1
    return ordered


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
    expand_owner_positions: ExpandOwnerPositions | None = None,
    discover_historical_activity: DiscoverHistoricalActivity | None = None,
    expand_closed_positions: bool = True,
    enable_historical_activity: bool = True,
    historical_signature_limit: int = 25,
    owner_expansion_limit: int = 25,
    owner_position_max_pages: int = 3,
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
    if historical_signature_limit < 1 or historical_signature_limit > 1_000:
        raise ValueError(
            "historical_signature_limit must be between 1 and 1000"
        )
    if owner_expansion_limit < 1:
        raise ValueError("owner_expansion_limit must be positive")
    if owner_position_max_pages < 1:
        raise ValueError("owner_position_max_pages must be positive")

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
            owners_expanded=0,
            owner_expansion_failures=0,
            expanded_positions_added=0,
            historical_scan_enabled=False,
            historical_recent_signatures_scanned=0,
            historical_backfill_signatures_scanned=0,
            historical_matching_transactions=0,
            historical_positions_added=0,
            historical_scan_failures=0,
            historical_scan_state=None,
            positions_attempted=0,
            positions_refreshed=0,
            positions_failed=0,
            items=(),
            reasons=("wallet-flow source thresholds are already satisfied",),
        )

    production_capture_path = (
        discover_positions is None
        and collect_history is None
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
        criteria=criteria,
        as_of=as_of,
    )

    candidate_map: dict[str, _WalletFlowPositionCandidate] = {
        item.position_address: _WalletFlowPositionCandidate(
            position_address=item.position_address,
            owner=item.owner,
            source="ONCHAIN_CURRENT",
        )
        for item in discovery.positions
    }

    items: list[Phase9WalletFlowCaptureItem] = []
    historical_scan_enabled = (
        enable_historical_activity
        and as_of is None
        and (
            discover_historical_activity is not None
            or production_capture_path
        )
    )
    historical_recent_signatures_scanned = 0
    historical_backfill_signatures_scanned = 0
    historical_matching_transactions = 0
    historical_positions_added = 0
    historical_scan_failures = 0
    historical_errors: list[str] = []
    historical_state: Phase9PoolActivityScanState | None = None
    refreshed = 0
    failed = 0
    owners_expanded = 0
    expansion_failures = 0
    expanded_positions_added = 0
    expansion_errors: list[str] = []

    api: MeteoraDataAPI | None = None
    needs_api = (
        collect_history is None
        or (
            expand_closed_positions
            and expand_owner_positions is None
            and collect_history is None
        )
    )
    if needs_api:
        current_settings = settings or Settings.from_env()
        api = MeteoraDataAPI(
            base_url=current_settings.meteora_data_api,
            requests_per_second=current_settings.requests_per_second,
        )

    if collect_history is None:
        def fetch(position: str) -> int:
            assert api is not None
            return collect_position_history(
                storage,
                api,
                position,
            ).events
    else:
        fetch = collect_history

    can_expand = (
        expand_closed_positions
        and (
            expand_owner_positions is not None
            or api is not None
        )
    )
    if expand_owner_positions is not None:
        expand = expand_owner_positions
    else:
        def expand(pool: str, owner: str) -> tuple[str, ...]:
            assert api is not None
            return api.pool_position_addresses(
                pool,
                user=owner,
                max_pages=owner_position_max_pages,
                page_size=100,
            )

    try:
        if can_expand:
            owners = sorted(
                {item.owner for item in discovery.positions},
                key=lambda owner: (
                    owner in existing_users,
                    owner,
                ),
            )[:owner_expansion_limit]
            for owner in owners:
                try:
                    addresses = expand(pool_address, owner)
                    owners_expanded += 1
                    for address in addresses:
                        value = str(address).strip()
                        if not value or value in candidate_map:
                            continue
                        candidate_map[value] = _WalletFlowPositionCandidate(
                            position_address=value,
                            owner=owner,
                            source="API_OWNER_ALL",
                        )
                        expanded_positions_added += 1
                except Exception as exc:
                    expansion_failures += 1
                    expansion_errors.append(
                        f"{owner}: {type(exc).__name__}: {str(exc)[:500]}"
                    )

        if historical_scan_enabled:
            if discover_historical_activity is not None:
                historical_discover = discover_historical_activity
            else:
                def historical_discover(
                    pool: str,
                    limit: int,
                    before: str | None,
                ) -> Phase9PoolActivityDiscoveryReport:
                    return discover_historical_pool_activity_with_rust(
                        pool,
                        limit=limit,
                        before_signature=before,
                        rust_manifest_path=rust_manifest_path,
                        rust_binary_path=rust_binary_path,
                        timeout_seconds=max(timeout_seconds, 300),
                    )

            historical_state = phase9_pool_activity_scan_state(
                storage,
                pool_address=pool_address,
            )

            def add_historical_candidates(
                page: Phase9PoolActivityDiscoveryReport,
                *,
                source: str,
            ) -> None:
                nonlocal historical_positions_added
                if page.pool_address != pool_address:
                    raise ValueError(
                        "historical activity discovery returned a different pool"
                    )
                for value in page.positions:
                    if value.position_address in candidate_map:
                        continue
                    candidate_map[value.position_address] = (
                        _WalletFlowPositionCandidate(
                            position_address=value.position_address,
                            owner=value.owner,
                            source=source,
                        )
                    )
                    historical_positions_added += 1

            try:
                recent = historical_discover(
                    pool_address,
                    historical_signature_limit,
                    None,
                )
                historical_recent_signatures_scanned = (
                    recent.signatures_scanned
                )
                historical_matching_transactions += (
                    recent.matching_transactions
                )
                add_historical_candidates(
                    recent,
                    source="ONCHAIN_HISTORICAL_RECENT",
                )

                if historical_state.pages_scanned == 0:
                    historical_state = record_phase9_pool_activity_page(
                        storage,
                        pool_address=pool_address,
                        next_before_signature=(
                            recent.next_before_signature
                        ),
                        has_more=recent.has_more,
                        signatures_scanned=recent.signatures_scanned,
                        matching_transactions=(
                            recent.matching_transactions
                        ),
                        positions_discovered=recent.positions_found,
                    )
                elif (
                    not historical_state.backfill_exhausted
                    and historical_state.backfill_before_signature
                ):
                    backfill = historical_discover(
                        pool_address,
                        historical_signature_limit,
                        historical_state.backfill_before_signature,
                    )
                    historical_backfill_signatures_scanned = (
                        backfill.signatures_scanned
                    )
                    historical_matching_transactions += (
                        backfill.matching_transactions
                    )
                    add_historical_candidates(
                        backfill,
                        source="ONCHAIN_HISTORICAL_BACKFILL",
                    )
                    historical_state = record_phase9_pool_activity_page(
                        storage,
                        pool_address=pool_address,
                        next_before_signature=(
                            backfill.next_before_signature
                        ),
                        has_more=backfill.has_more,
                        signatures_scanned=backfill.signatures_scanned,
                        matching_transactions=(
                            backfill.matching_transactions
                        ),
                        positions_discovered=backfill.positions_found,
                    )
            except Exception as exc:
                historical_scan_failures += 1
                historical_errors.append(
                    f"{type(exc).__name__}: {str(exc)[:1000]}"
                )

        candidates = _diversity_order(
            list(candidate_map.values()),
            existing_positions=existing_positions,
            existing_users=existing_users,
        )[:max_positions_per_run]

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
                        source=candidate.source,
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
                        source=candidate.source,
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
    if expansion_failures:
        reasons.append(
            f"{expansion_failures} owner position expansion(s) failed: "
            + "; ".join(expansion_errors)
        )
    if historical_scan_failures:
        reasons.append(
            "historical pool-activity discovery failed: "
            + "; ".join(historical_errors)
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
            "the bounded wallet-flow cohort is not a complete historical "
            "pool census even after current-owner status=all expansion and "
            "read-only pool-signature backfill; RPC history retention and "
            "configured page limits can omit older activity"
        )

    return Phase9WalletFlowCaptureReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        source_scope=(
            "CURRENT_AND_HISTORICAL_POOL_ACTIVITY_COHORT"
            if historical_positions_added > 0
            else (
                "CURRENT_OWNER_ALL_POSITION_COHORT"
                if owners_expanded > 0
                else "CURRENT_ONCHAIN_POSITION_COHORT"
            )
        ),
        pool_address=pool_address,
        as_of=as_of,
        source_before=before,
        source_after=after,
        positions_found=discovery.positions_found,
        positions_returned=discovery.positions_returned,
        discovery_truncated=discovery.truncated,
        discovery_unique_owners=discovery.unique_owners,
        owners_expanded=owners_expanded,
        owner_expansion_failures=expansion_failures,
        expanded_positions_added=expanded_positions_added,
        historical_scan_enabled=historical_scan_enabled,
        historical_recent_signatures_scanned=(
            historical_recent_signatures_scanned
        ),
        historical_backfill_signatures_scanned=(
            historical_backfill_signatures_scanned
        ),
        historical_matching_transactions=(
            historical_matching_transactions
        ),
        historical_positions_added=historical_positions_added,
        historical_scan_failures=historical_scan_failures,
        historical_scan_state=historical_state,
        positions_attempted=len(items),
        positions_refreshed=refreshed,
        positions_failed=failed,
        items=tuple(items),
        reasons=tuple(reasons),
    )
