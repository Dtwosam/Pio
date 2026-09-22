from __future__ import annotations

import time
from typing import Any

import httpx


class MeteoraAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class RateLimiter:
    """Simple serial rate limiter. Default stays below Meteora's 30 RPS limit."""

    def __init__(self, requests_per_second: float = 20.0):
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.min_interval = 1.0 / requests_per_second
        self._last_request_at = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()


class MeteoraDataAPI:
    def __init__(
        self,
        base_url: str = "https://dlmm.datapi.meteora.ag",
        timeout: float = 20.0,
        requests_per_second: float = 20.0,
        max_retries: int = 4,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.rate_limiter = RateLimiter(requests_per_second)
        self.client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "pio-meteora-lp/0.2", "Accept": "application/json"},
        )

    def __enter__(self) -> "MeteoraDataAPI":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self.rate_limiter.wait()
            retry_after: float | None = None

            try:
                response = self.client.get(f"{self.base_url}{path}", params=params)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = self._retry_after(response)
                    last_error = MeteoraAPIError(
                        f"retryable Meteora API error: HTTP {response.status_code}",
                        response.status_code,
                        retry_after,
                    )
                elif response.is_error:
                    raise MeteoraAPIError(
                        f"Meteora API error: HTTP {response.status_code}: {response.text[:500]}",
                        response.status_code,
                    )
                else:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise MeteoraAPIError(f"invalid JSON from Meteora endpoint {path}") from exc

            if attempt >= self.max_retries:
                break
            time.sleep(retry_after if retry_after is not None else min(8.0, 0.5 * (2**attempt)))

        status_code = getattr(last_error, "status_code", None)
        raise MeteoraAPIError(f"Meteora request failed for {path}: {last_error}", status_code)

    def pools(
        self,
        page: int = 1,
        page_size: int = 100,
        sort_by: str | None = None,
        filter_by: str | None = None,
        query: str | None = None,
    ) -> Any:
        if page < 1:
            raise ValueError("page must be >= 1")
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if sort_by is not None and ":" not in sort_by:
            raise ValueError("sort_by must use Meteora format '<field>:<asc|desc>'")

        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if sort_by:
            params["sort_by"] = sort_by
        if filter_by:
            params["filter_by"] = filter_by
        if query:
            params["query"] = query
        return self._get("/pools", params=params)

    def pool(self, address: str) -> Any:
        return self._get(f"/pools/{address}")

    def ohlcv(
        self,
        address: str,
        *,
        from_: int | None = None,
        to: int | None = None,
        resolution: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if from_ is not None:
            params["from"] = from_
        if to is not None:
            params["to"] = to
        if resolution is not None:
            params["resolution"] = resolution
        return self._get(f"/pools/{address}/ohlcv", params=params or None)

    def volume_history(self, address: str) -> Any:
        return self._get(f"/pools/{address}/volume/history")

    def position_history(
        self,
        position_address: str,
        *,
        event_type: str | None = None,
        order_direction: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if event_type is not None:
            params["event_type"] = event_type
        if order_direction is not None:
            if order_direction not in {"asc", "desc"}:
                raise ValueError("order_direction must be 'asc' or 'desc'")
            params["order_direction"] = order_direction
        return self._get(f"/positions/{position_address}/historical", params=params or None)

    def position_pnl(
        self,
        pool_address: str,
        *,
        user: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Any:
        if not user:
            raise ValueError("user wallet address is required")
        if status is not None and status not in {"open", "closed", "all"}:
            raise ValueError("status must be open, closed or all")
        if page < 1:
            raise ValueError("page must be >= 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        params: dict[str, Any] = {
            "user": user,
            "page": page,
            "page_size": page_size,
        }
        if status is not None:
            params["status"] = status
        return self._get(f"/positions/{pool_address}/pnl", params=params)

    def protocol_metrics(self) -> Any:
        return self._get("/stats/protocol_metrics")
