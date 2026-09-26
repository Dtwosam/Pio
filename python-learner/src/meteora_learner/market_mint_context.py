from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from .mint_ingest import ingest_mint_snapshot
from .phase9_mint_capture import inspect_mint_with_rust
from .storage import Storage


InspectMint = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class MarketMintContextCandidate:
    mint_address: str
    pools: tuple[str, ...]
    roles: tuple[str, ...]
    first_chain_observed_at: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketMintContextQueue:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    chain_observed_pools: int
    unique_required_mints: int
    mints_with_any_snapshot: int
    missing_mints: int
    batch_limit: int
    candidates: tuple[MarketMintContextCandidate, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketMintContextCaptureItem:
    mint_address: str
    status: str
    snapshot_id: int | None
    error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketMintContextCaptureReport:
    research_only: bool
    read_only_capture: bool
    policy_actionable: bool
    execution_wired: bool
    queue_before: MarketMintContextQueue
    attempted: int
    captured: int
    failed: int
    items: tuple[MarketMintContextCaptureItem, ...]
    queue_after: MarketMintContextQueue

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_market_mint_context_queue(
    storage: Storage,
    *,
    batch_limit: int = 50,
) -> MarketMintContextQueue:
    """
    Queue token X/Y mints that have never received a mint snapshot.

    Ordering is based on when the mint first appeared in chain-observed pools,
    then mint address. No economic pool ranking is used.
    """
    if batch_limit < 1:
        raise ValueError("batch_limit must be positive")

    with storage.connect() as conn:
        rows = conn.execute(
            """
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
            mint_first AS (
                SELECT
                    mint_address,
                    MIN(observed_at) AS first_chain_observed_at
                FROM pool_mints
                GROUP BY mint_address
            ),
            mint_snap AS (
                SELECT DISTINCT mint_address
                FROM token_mint_snapshots
            )
            SELECT
                mf.mint_address,
                mf.first_chain_observed_at
            FROM mint_first mf
            LEFT JOIN mint_snap ms
              ON ms.mint_address = mf.mint_address
            WHERE ms.mint_address IS NULL
            ORDER BY
                julianday(mf.first_chain_observed_at) ASC,
                mf.mint_address ASC
            LIMIT ?
            """,
            (batch_limit,),
        ).fetchall()

        totals = conn.execute(
            """
            WITH pool_mints AS (
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
            mint_snap AS (
                SELECT DISTINCT mint_address
                FROM token_mint_snapshots
            )
            SELECT
                (
                    SELECT COUNT(DISTINCT pool_address)
                    FROM chain_pool_snapshots
                    WHERE pool_address IS NOT NULL
                      AND TRIM(pool_address) != ''
                ) AS chain_pools,
                (SELECT COUNT(*) FROM pool_mints) AS required_mints,
                (
                    SELECT COUNT(*)
                    FROM pool_mints pm
                    JOIN mint_snap ms
                      ON ms.mint_address = pm.mint_address
                ) AS observed_mints,
                (
                    SELECT COUNT(*)
                    FROM pool_mints pm
                    LEFT JOIN mint_snap ms
                      ON ms.mint_address = pm.mint_address
                    WHERE ms.mint_address IS NULL
                ) AS missing_mints
            """
        ).fetchone()

        context_rows = conn.execute(
            """
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
            )
            SELECT pool_address, mint_address, role
            FROM pool_mints
            """
        ).fetchall()

    pools_by_mint: dict[str, set[str]] = {}
    roles_by_mint: dict[str, set[str]] = {}
    for row in context_rows:
        mint = str(row[1])
        pools_by_mint.setdefault(mint, set()).add(str(row[0]))
        roles_by_mint.setdefault(mint, set()).add(str(row[2]))

    candidates = tuple(
        MarketMintContextCandidate(
            mint_address=str(row[0]),
            pools=tuple(
                sorted(pools_by_mint.get(str(row[0]), set()))
            ),
            roles=tuple(
                sorted(roles_by_mint.get(str(row[0]), set()))
            ),
            first_chain_observed_at=str(row[1]),
        )
        for row in rows
    )

    return MarketMintContextQueue(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        chain_observed_pools=(
            int(totals[0]) if totals is not None else 0
        ),
        unique_required_mints=(
            int(totals[1]) if totals is not None else 0
        ),
        mints_with_any_snapshot=(
            int(totals[2]) if totals is not None else 0
        ),
        missing_mints=(
            int(totals[3]) if totals is not None else 0
        ),
        batch_limit=batch_limit,
        candidates=candidates,
    )


def run_market_mint_context_capture(
    storage: Storage,
    *,
    batch_limit: int = 50,
    inspector: InspectMint | None = None,
    rust_manifest_path: str | None = None,
    rust_binary_path: str | None = None,
    timeout_seconds: int = 120,
    observed_at: str | None = None,
) -> MarketMintContextCaptureReport:
    """
    Capture one read-only batch of previously unseen token mint accounts.
    """
    before = build_market_mint_context_queue(
        storage,
        batch_limit=batch_limit,
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

    items: list[MarketMintContextCaptureItem] = []
    captured = 0
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
            captured += 1
            items.append(
                MarketMintContextCaptureItem(
                    mint_address=candidate.mint_address,
                    status="CAPTURED",
                    snapshot_id=result.snapshot_id,
                    error=None,
                )
            )
        except Exception as exc:
            failed += 1
            items.append(
                MarketMintContextCaptureItem(
                    mint_address=candidate.mint_address,
                    status="FAILED",
                    snapshot_id=None,
                    error=str(exc)[:2000],
                )
            )

    after = build_market_mint_context_queue(
        storage,
        batch_limit=batch_limit,
    )
    return MarketMintContextCaptureReport(
        research_only=True,
        read_only_capture=True,
        policy_actionable=False,
        execution_wired=False,
        queue_before=before,
        attempted=len(before.candidates),
        captured=captured,
        failed=failed,
        items=tuple(items),
        queue_after=after,
    )
