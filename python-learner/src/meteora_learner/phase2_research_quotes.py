from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any, Callable

from .jupiter_quotes import (
    JUPITER_SOURCE,
    JupiterTokenClient,
    JupiterTokenUSDQuote,
)
from .quote_registry import DEFAULT_QUOTE_UNIT, save_token_quote
from .research_store import ResearchStore
from .settings import Settings
from .storage import Storage, utc_now_iso


DEFAULT_PUBKEY = "11111111111111111111111111111111"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass(frozen=True)
class Phase2ResearchQuoteItem:
    token_mint: str
    status: str
    quote_per_atomic: float | None
    source: str | None
    error: str | None


@dataclass(frozen=True)
class Phase2ResearchQuoteReport:
    pool_address: str
    observed_at: str
    mints_requested: int
    quotes_refreshed: int
    quotes_failed: int
    items: tuple[Phase2ResearchQuoteItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def required_pool_research_quote_mints(
    storage: Storage,
    *,
    pool_address: str,
) -> tuple[str, ...]:
    if not pool_address.strip():
        raise ValueError("pool_address is required")

    snapshot = ResearchStore(
        str(storage.path)
    ).latest_chain_pool_snapshot(pool_address)
    if snapshot is None:
        raise ValueError(
            f"no stored chain pool snapshot for {pool_address}"
        )

    required = {
        str(snapshot["token_x_mint"]),
        str(snapshot["token_y_mint"]),
        WRAPPED_SOL_MINT,
    }
    for key in ("reward_mint_0", "reward_mint_1"):
        value = snapshot.get(key)
        if value is None:
            continue
        mint = str(value)
        if mint and mint != DEFAULT_PUBKEY:
            required.add(mint)

    required.discard("")
    return tuple(sorted(required))


def collect_phase2_research_quotes(
    storage: Storage,
    *,
    pool_address: str,
    observed_at: str | None = None,
    fetch_quote: Callable[[str], JupiterTokenUSDQuote] | None = None,
) -> Phase2ResearchQuoteReport:
    """
    Capture prospective quote evidence for Phase-2/rotation research.

    This collector is read-only with respect to external systems. It persists
    only observed token quotes and performs no policy/action selection.
    """
    timestamp = observed_at or utc_now_iso()
    mints = required_pool_research_quote_mints(
        storage,
        pool_address=pool_address,
    )
    items: list[Phase2ResearchQuoteItem] = []
    refreshed = 0
    failed = 0

    def record(mint: str, quote: JupiterTokenUSDQuote) -> None:
        nonlocal refreshed
        if quote.token_mint != mint:
            raise ValueError("quote mint does not match requested mint")
        saved = save_token_quote(
            storage,
            token_mint=mint,
            quote_per_atomic=quote.usd_per_atomic,
            source=JUPITER_SOURCE,
            observed_at=timestamp,
            quote_unit=DEFAULT_QUOTE_UNIT,
            raw=asdict(quote),
        )
        refreshed += 1
        items.append(
            Phase2ResearchQuoteItem(
                token_mint=mint,
                status="REFRESHED",
                quote_per_atomic=saved.quote_per_atomic,
                source=saved.source,
                error=None,
            )
        )

    if fetch_quote is None:
        with JupiterTokenClient() as client:
            for mint in mints:
                try:
                    record(mint, client.token_usd_quote(mint))
                except Exception as exc:
                    failed += 1
                    items.append(
                        Phase2ResearchQuoteItem(
                            token_mint=mint,
                            status="FAILED",
                            quote_per_atomic=None,
                            source=None,
                            error=str(exc)[:2000],
                        )
                    )
    else:
        for mint in mints:
            try:
                record(mint, fetch_quote(mint))
            except Exception as exc:
                failed += 1
                items.append(
                    Phase2ResearchQuoteItem(
                        token_mint=mint,
                        status="FAILED",
                        quote_per_atomic=None,
                        source=None,
                        error=str(exc)[:2000],
                    )
                )

    return Phase2ResearchQuoteReport(
        pool_address=pool_address,
        observed_at=timestamp,
        mints_requested=len(mints),
        quotes_refreshed=refreshed,
        quotes_failed=failed,
        items=tuple(items),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Capture read-only Jupiter USD quotes for a Phase-2 research "
            "pool's X/Y/reward mints plus WSOL."
        )
    )
    parser.add_argument(
        "--pool",
        default=os.getenv("PIO_PHASE2_POSITION_POOL"),
        help="Pool address (or PIO_PHASE2_POSITION_POOL)",
    )
    parser.add_argument("--database")
    args = parser.parse_args()
    if not args.pool:
        parser.error("--pool or PIO_PHASE2_POSITION_POOL is required")

    settings = Settings.from_env()
    storage = Storage(
        Path(args.database) if args.database else settings.database_path
    )
    report = collect_phase2_research_quotes(
        storage,
        pool_address=args.pool,
    )
    print(json.dumps(report.to_record(), indent=2))
    if report.quotes_failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
