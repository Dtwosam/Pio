from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

from .paper_chain_refresh import InspectPool, refresh_paper_chain_state
from .paper_market_refresh import PoolFetcher, refresh_paper_market_state
from .paper_supervisor import run_paper_supervisor
from .pool_safety import PoolSafetyConfig
from .position_policy import PositionManagementConfig
from .settings import Settings
from .storage import Storage, utc_now_iso


@dataclass(frozen=True)
class PaperTickReport:
    tick_id: str
    account_id: str
    observed_at: str
    status: str
    reused_existing_tick: bool
    market: dict[str, Any] | None
    chain: dict[str, Any] | None
    supervisor: dict[str, Any] | None
    error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _from_stored(
    *,
    tick_id: str,
    account_id: str,
    started_at: str,
    status: str,
    result_json: str | None,
    error: str | None,
) -> PaperTickReport:
    payload = json.loads(result_json) if result_json else {}
    return PaperTickReport(
        tick_id=tick_id,
        account_id=account_id,
        observed_at=str(payload.get("observed_at") or started_at),
        status=status,
        reused_existing_tick=True,
        market=payload.get("market"),
        chain=payload.get("chain"),
        supervisor=payload.get("supervisor"),
        error=error or payload.get("error"),
    )


def run_paper_tick(
    storage: Storage,
    *,
    account_id: str,
    tick_id: str,
    settings: Settings | None = None,
    observed_at: str | None = None,
    chain_max_age_seconds: int = 300,
    quote_max_age_seconds: int = 300,
    array_radius: int = 1,
    max_positions: int | None = None,
    safety_config: PoolSafetyConfig = PoolSafetyConfig(),
    management_config: PositionManagementConfig = PositionManagementConfig(),
    retry_failed: bool = False,
    fetch_pool: PoolFetcher | None = None,
    inspect_pool: InspectPool | None = None,
) -> PaperTickReport:
    """
    Execute one idempotent PAPER orchestration tick.

    Sequence:
    1. refresh Data API metadata for open-position pools;
    2. refresh missing/stale chain state through read-only Rust;
    3. run dependency-aware paper management using fresh persisted quotes.

    Quote acquisition is intentionally outside this function. Missing/stale
    quotes remain visible blockers instead of being guessed.
    """
    if not account_id.strip():
        raise ValueError("account_id is required")
    if not tick_id.strip():
        raise ValueError("tick_id is required")

    with storage.connect() as conn:
        existing = conn.execute(
            """
            SELECT account_id, started_at, status, result_json, error
            FROM paper_ticks
            WHERE tick_id = ?
            LIMIT 1
            """,
            (tick_id,),
        ).fetchone()

    if existing is not None:
        if str(existing[0]) != account_id:
            raise ValueError("tick_id already belongs to another account")
        if str(existing[2]) != "FAILED" or not retry_failed:
            return _from_stored(
                tick_id=tick_id,
                account_id=account_id,
                started_at=str(existing[1]),
                status=str(existing[2]),
                result_json=existing[3],
                error=existing[4],
            )
        timestamp = str(existing[1])
        with storage.connect() as conn:
            conn.execute(
                """
                UPDATE paper_ticks
                SET status = 'RUNNING', finished_at = NULL,
                    result_json = NULL, error = NULL
                WHERE tick_id = ?
                """,
                (tick_id,),
            )
    else:
        timestamp = observed_at or utc_now_iso()
        with storage.connect() as conn:
            conn.execute(
                """
                INSERT INTO paper_ticks(
                    tick_id, account_id, started_at, status
                ) VALUES (?, ?, ?, 'RUNNING')
                """,
                (tick_id, account_id, timestamp),
            )

    market_record: dict[str, Any] | None = None
    chain_record: dict[str, Any] | None = None
    supervisor_record: dict[str, Any] | None = None

    try:
        market = refresh_paper_market_state(
            storage,
            account_id=account_id,
            observed_at=timestamp,
            settings=settings,
            fetch_pool=fetch_pool,
        )
        market_record = market.to_record()

        if market.pools_failed:
            status = "MARKET_REFRESH_FAILED"
        else:
            chain = refresh_paper_chain_state(
                storage,
                account_id=account_id,
                max_age_seconds=chain_max_age_seconds,
                array_radius=array_radius,
                as_of=timestamp,
                inspector=inspect_pool,
                ingest_observed_at=timestamp,
            )
            chain_record = chain.to_record()

            supervisor = run_paper_supervisor(
                storage,
                account_id=account_id,
                cycle_id=f"tick:{tick_id}",
                chain_max_age_seconds=chain_max_age_seconds,
                quote_max_age_seconds=quote_max_age_seconds,
                array_radius=array_radius,
                max_positions=max_positions,
                as_of=timestamp,
                safety_config=safety_config,
                management_config=management_config,
                retry_failed=retry_failed,
            )
            supervisor_record = supervisor.to_record()
            status = (
                "PARTIAL"
                if supervisor.status == "PARTIAL_FAILURE"
                else supervisor.status
            )

        payload = {
            "observed_at": timestamp,
            "market": market_record,
            "chain": chain_record,
            "supervisor": supervisor_record,
            "error": None,
        }
        with storage.connect() as conn:
            conn.execute(
                """
                UPDATE paper_ticks
                SET finished_at = ?, status = ?, result_json = ?, error = NULL
                WHERE tick_id = ?
                """,
                (
                    utc_now_iso(),
                    status,
                    json.dumps(payload, separators=(",", ":")),
                    tick_id,
                ),
            )
        return PaperTickReport(
            tick_id=tick_id,
            account_id=account_id,
            observed_at=timestamp,
            status=status,
            reused_existing_tick=False,
            market=market_record,
            chain=chain_record,
            supervisor=supervisor_record,
            error=None,
        )
    except Exception as exc:
        error = str(exc)[:2000]
        payload = {
            "observed_at": timestamp,
            "market": market_record,
            "chain": chain_record,
            "supervisor": supervisor_record,
            "error": error,
        }
        with storage.connect() as conn:
            conn.execute(
                """
                UPDATE paper_ticks
                SET finished_at = ?, status = 'FAILED',
                    result_json = ?, error = ?
                WHERE tick_id = ?
                """,
                (
                    utc_now_iso(),
                    json.dumps(payload, separators=(",", ":")),
                    error,
                    tick_id,
                ),
            )
        return PaperTickReport(
            tick_id=tick_id,
            account_id=account_id,
            observed_at=timestamp,
            status="FAILED",
            reused_existing_tick=False,
            market=market_record,
            chain=chain_record,
            supervisor=supervisor_record,
            error=error,
        )
