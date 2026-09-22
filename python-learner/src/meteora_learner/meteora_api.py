from __future__ import annotations

import time
from typing import Any

import httpx


class MeteoraAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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
            headers={"User-Agent": "pio-meteora-lp/0.1"},
        )

    def __enter__(self) -> "MeteoraDataAPI":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.rate_limiter.wait()
            try:
                response = self.client.get(f"{self.base_url}{path}", params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise MeteoraAPIError(
                        f"retryable Meteora API error: HTTP {response.status_code}",
                        response.status_code,
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, MeteoraAPIError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(8.0, 0.5 * (2**attempt)))

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

    def ohlcv(self, address: str, **params: Any) -> Any:
        return self._get(f"/pools/{address}/ohlcv", params=params or None)

    def volume_history(self, address: str, **params: Any) -> Any:
        return self._get(f"/pools/{address}/volume/history", params=params or None)

    def protocol_metrics(self) -> Any:
        return self._get("/stats/protocol_metrics")
