from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import os
from typing import Any, Callable

import httpx

from .quote_registry import (
    DEFAULT_QUOTE_UNIT,
    required_paper_quote_mints,
    save_token_quote,
)
from .storage import Storage, utc_now_iso


JUPITER_BASE_URL = "https://api.jup.ag"
JUPITER_SOURCE = "JUPITER_TOKENS_V2_USD"


@dataclass(frozen=True)
class JupiterTokenUSDQuote:
    token_mint: str
    symbol: str | None
    decimals: int
    usd_price: float
    usd_per_atomic: float


@dataclass(frozen=True)
class JupiterQuoteRefreshItem:
    token_mint: str
    status: str
    usd_per_atomic: float | None
    error: str | None


@dataclass(frozen=True)
class JupiterQuoteRefreshReport:
    account_id: str
    observed_at: str
    mints_requested: int
    quotes_refreshed: int
    quotes_failed: int
    items: tuple[JupiterQuoteRefreshItem, ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


class JupiterTokenClient:
    def __init__(
        self,
        *,
        base_url: str = JUPITER_BASE_URL,
        api_key: str | None = None,
        timeout_seconds: float = 15.0,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        headers = {
            "Accept": "application/json",
            "User-Agent": "pio-meteora-lp/0.5",
        }
        key = api_key or os.getenv("JUPITER_API_KEY")
        if key:
            headers["x-api-key"] = key
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers=headers,
        )

    def __enter__(self) -> "JupiterTokenClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def token_usd_quote(self, token_mint: str) -> JupiterTokenUSDQuote:
        if not token_mint.strip():
            raise ValueError("token_mint is required")
        response = self.client.get(
            "/tokens/v2/search",
            params={"query": token_mint},
        )
        if response.is_error:
            raise RuntimeError(
                f"Jupiter Tokens API HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Jupiter Tokens API returned invalid JSON") from exc
        if not isinstance(payload, list):
            raise RuntimeError("Jupiter Tokens API response must be a list")

        token = next(
            (
                item
                for item in payload
                if isinstance(item, dict)
                and str(item.get("id", "")) == token_mint
            ),
            None,
        )
        if token is None:
            raise ValueError("token mint is missing from Jupiter response")

        price_raw = token.get("usdPrice")
        decimals_raw = token.get("decimals")
        if price_raw is None:
            raise ValueError("Jupiter has no USD price for token")
        if decimals_raw is None:
            raise ValueError("Jupiter has no decimals for token")

        price = Decimal(str(price_raw))
        decimals = int(decimals_raw)
        if not price.is_finite() or price <= 0:
            raise ValueError("Jupiter USD price must be finite and positive")
        if not 0 <= decimals <= 30:
            raise ValueError("Jupiter token decimals are outside supported range")
        per_atomic = price / (Decimal(10) ** decimals)
        return JupiterTokenUSDQuote(
            token_mint=token_mint,
            symbol=(
                str(token["symbol"])
                if token.get("symbol") is not None
                else None
            ),
            decimals=decimals,
            usd_price=float(price),
            usd_per_atomic=float(per_atomic),
        )


def refresh_open_paper_jupiter_quotes(
    storage: Storage,
    *,
    account_id: str,
    observed_at: str | None = None,
    fetch_quote: Callable[[str], JupiterTokenUSDQuote] | None = None,
) -> JupiterQuoteRefreshReport:
    """
    Refresh USD-per-atomic quotes required by open chain-bound PAPER positions.

    Pio v1 treats ACCOUNT_QUOTE as USD when this adapter is used. A missing
    Jupiter price fails that mint; no fallback or synthetic price is inserted.
    """
    if not account_id.strip():
        raise ValueError("account_id is required")
    timestamp = observed_at or utc_now_iso()
    mints = required_paper_quote_mints(storage, account_id=account_id)
    items: list[JupiterQuoteRefreshItem] = []
    refreshed = 0
    failed = 0

    if fetch_quote is None:
        with JupiterTokenClient() as client:
            def fetch(mint: str) -> JupiterTokenUSDQuote:
                return client.token_usd_quote(mint)

            for mint in mints:
                try:
                    quote = fetch(mint)
                    save_token_quote(
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
                        JupiterQuoteRefreshItem(
                            token_mint=mint,
                            status="REFRESHED",
                            usd_per_atomic=quote.usd_per_atomic,
                            error=None,
                        )
                    )
                except Exception as exc:
                    failed += 1
                    items.append(
                        JupiterQuoteRefreshItem(
                            token_mint=mint,
                            status="FAILED",
                            usd_per_atomic=None,
                            error=str(exc)[:2000],
                        )
                    )
    else:
        for mint in mints:
            try:
                quote = fetch_quote(mint)
                if quote.token_mint != mint:
                    raise ValueError("quote mint does not match requested mint")
                save_token_quote(
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
                    JupiterQuoteRefreshItem(
                        token_mint=mint,
                        status="REFRESHED",
                        usd_per_atomic=quote.usd_per_atomic,
                        error=None,
                    )
                )
            except Exception as exc:
                failed += 1
                items.append(
                    JupiterQuoteRefreshItem(
                        token_mint=mint,
                        status="FAILED",
                        usd_per_atomic=None,
                        error=str(exc)[:2000],
                    )
                )

    return JupiterQuoteRefreshReport(
        account_id=account_id,
        observed_at=timestamp,
        mints_requested=len(mints),
        quotes_refreshed=refreshed,
        quotes_failed=failed,
        items=tuple(items),
    )
