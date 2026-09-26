from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .mint_ingest import ingest_mint_snapshot
from .phase9_mint_capture import inspect_mint_with_rust
from .storage import Storage


InspectMint = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class MarketMintRefreshCandidate:
    mint_address: str
    first_chain_observed_at: str
    last_mint_observed_at: str
    mint_observations: int
    pools: tuple[str, ...]
    roles: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketMintRefreshQueue:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    required_mints: int
    observed_mints: int
    refreshable_mints: int
    batch_limit: int
    candidates: tuple[MarketMintRefreshCandidate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketMintRefreshItem:
    mint_address: str
    previous_mint_observed_at: str
    status: str
    new_observed_at: str | None
    snapshot_id: int | None
    error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketMintRefreshReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    queue_before: MarketMintRefreshQueue
    mints_attempted: int
    mints_refreshed: int
    mints_failed: int
    items: tuple[MarketMintRefreshItem, ...]
    queue_after: MarketMintRefreshQueue

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_market_mint_refresh_queue(
    storage: Storage,
    *,
    batch_limit: int = 20,
    exclude_last_observed_at: str | None = None,
) -> MarketMintRefreshQueue:
    """
    Queue already-observed required mints for neutral longitudinal refresh.

    Ordering is oldest persisted mint observation first, then first chain
    appearance and mint address. No TVL, volume, price, fee, model prediction,
    authority state or other economic/risk field affects ordering.
    """
    if batch_limit < 1:
        raise ValueError("batch_limit must be positive")

    with storage.connect() as conn:
        totals = conn.execute(
            """
            WITH required AS (
                SELECT token_x_mint AS mint_address
                FROM chain_pool_snapshots
                WHERE token_x_mint IS NOT NULL
                  AND TRIM(token_x_mint) != ''
                UNION
                SELECT token_y_mint AS mint_address
                FROM chain_pool_snapshots
                WHERE token_y_mint IS NOT NULL
                  AND TRIM(token_y_mint) != ''
            ),
            observed AS (
                SELECT DISTINCT mint_address
                FROM token_mint_snapshots
            )
            SELECT
                (SELECT COUNT(*) FROM required),
                (
                    SELECT COUNT(*)
                    FROM required r
                    JOIN observed o
                      ON o.mint_address = r.mint_address
                )
            """
        ).fetchone()

        params: list[Any] = []
        exclusion = ""
        if exclude_last_observed_at is not None:
            exclusion = (
                "AND julianday(mints.last_mint_observed_at) "
                "< julianday(?)"
            )
            params.append(exclude_last_observed_at)
        params.append(batch_limit)

        rows = conn.execute(
            f"""
            WITH pool_mints AS (
                SELECT
                    pool_address,
                    observed_at,
                    token_x_mint AS mint_address,
                    'TOKEN_X' AS role
                FROM chain_pool_snapshots
                WHERE token_x_mint IS NOT NULL
                  AND TRIM(token_x_mint) != ''
                UNION ALL
                SELECT
                    pool_address,
                    observed_at,
                    token_y_mint AS mint_address,
                    'TOKEN_Y' AS role
                FROM chain_pool_snapshots
                WHERE token_y_mint IS NOT NULL
                  AND TRIM(token_y_mint) != ''
            ),
            required AS (
                SELECT
                    mint_address,
                    MIN(observed_at) AS first_chain_observed_at
                FROM pool_mints
                GROUP BY mint_address
            ),
            mints AS (
                SELECT
                    mint_address,
                    COUNT(*) AS mint_observations,
                    MAX(observed_at) AS last_mint_observed_at
                FROM token_mint_snapshots
                GROUP BY mint_address
            )
            SELECT
                required.mint_address,
                required.first_chain_observed_at,
                mints.last_mint_observed_at,
                mints.mint_observations
            FROM required
            JOIN mints
              ON mints.mint_address = required.mint_address
            WHERE 1 = 1
              {exclusion}
            ORDER BY
                julianday(mints.last_mint_observed_at) ASC,
                julianday(required.first_chain_observed_at) ASC,
                required.mint_address ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

        context_rows = conn.execute(
            """
            SELECT pool_address, token_x_mint, token_y_mint
            FROM chain_pool_snapshots
            """
        ).fetchall()

    pools_by_mint: dict[str, set[str]] = {}
    roles_by_mint: dict[str, set[str]] = {}
    for row in context_rows:
        pool = str(row[0])
        if row[1] is not None and str(row[1]).strip():
            mint = str(row[1])
            pools_by_mint.setdefault(mint, set()).add(pool)
            roles_by_mint.setdefault(mint, set()).add("TOKEN_X")
        if row[2] is not None and str(row[2]).strip():
            mint = str(row[2])
            pools_by_mint.setdefault(mint, set()).add(pool)
            roles_by_mint.setdefault(mint, set()).add("TOKEN_Y")

    candidates = tuple(
        MarketMintRefreshCandidate(
            mint_address=str(row[0]),
            first_chain_observed_at=str(row[1]),
            last_mint_observed_at=str(row[2]),
            mint_observations=int(row[3]),
            pools=tuple(
                sorted(pools_by_mint.get(str(row[0]), set()))
            ),
            roles=tuple(
                sorted(roles_by_mint.get(str(row[0]), set()))
            ),
        )
        for row in rows
    )

    required = int(totals[0]) if totals is not None else 0
    observed = int(totals[1]) if totals is not None else 0
    return MarketMintRefreshQueue(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        required_mints=required,
        observed_mints=observed,
        refreshable_mints=observed,
        batch_limit=batch_limit,
        candidates=candidates,
    )


def _latest_mint_observed_at(
    storage: Storage,
    mint_address: str,
) -> str:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT observed_at
            FROM token_mint_snapshots
            WHERE mint_address = ?
            ORDER BY julianday(observed_at) DESC, id DESC
            LIMIT 1
            """,
            (mint_address,),
        ).fetchone()
    if row is None:
        raise ValueError(
            "refreshed mint snapshot was not persisted"
        )
    return str(row[0])


def run_market_mint_refresh(
    storage: Storage,
    *,
    batch_limit: int = 20,
    inspector: InspectMint | None = None,
    rust_manifest_path: str | None = None,
    rust_binary_path: str | None = None,
    timeout_seconds: int = 120,
    observed_at: str | None = None,
) -> MarketMintRefreshReport:
    """
    Refresh one neutral batch of already-observed required token mints.

    This performs read-only RPC inspection plus local evidence ingestion. It
    cannot rank pools, construct/sign/submit transactions, or change capital.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    before = build_market_mint_refresh_queue(
        storage,
        batch_limit=batch_limit,
        exclude_last_observed_at=observed_at,
    )

    if inspector is None:
        def inspect(mint: str) -> dict[str, Any]:
            return inspect_mint_with_rust(
                mint,
                rust_manifest_path=rust_manifest_path,
                rust_binary_path=rust_binary_path,
                timeout_seconds=timeout_seconds,
            )
    else:
        inspect = inspector

    items: list[MarketMintRefreshItem] = []
    refreshed = 0
    failed = 0
    for candidate in before.candidates:
        try:
            payload = inspect(candidate.mint_address)
            if str(payload.get("mint_address", "")).strip() != (
                candidate.mint_address
            ):
                raise ValueError(
                    "mint inspector returned a different mint_address"
                )
            result = ingest_mint_snapshot(
                storage,
                payload,
                observed_at=observed_at,
            )
            refreshed += 1
            items.append(
                MarketMintRefreshItem(
                    mint_address=candidate.mint_address,
                    previous_mint_observed_at=(
                        candidate.last_mint_observed_at
                    ),
                    status="REFRESHED",
                    new_observed_at=_latest_mint_observed_at(
                        storage,
                        candidate.mint_address,
                    ),
                    snapshot_id=result.snapshot_id,
                    error=None,
                )
            )
        except Exception as exc:
            failed += 1
            items.append(
                MarketMintRefreshItem(
                    mint_address=candidate.mint_address,
                    previous_mint_observed_at=(
                        candidate.last_mint_observed_at
                    ),
                    status="FAILED",
                    new_observed_at=None,
                    snapshot_id=None,
                    error=str(exc)[:2000],
                )
            )

    after = build_market_mint_refresh_queue(
        storage,
        batch_limit=batch_limit,
        exclude_last_observed_at=observed_at,
    )
    return MarketMintRefreshReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        queue_before=before,
        mints_attempted=len(before.candidates),
        mints_refreshed=refreshed,
        mints_failed=failed,
        items=tuple(items),
        queue_after=after,
    )
