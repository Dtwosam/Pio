from __future__ import annotations

from typing import Protocol


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str | bytes | None
    stderr: str | bytes | None


class Phase2RpcRateLimited(RuntimeError):
    """Safe signal that the Solana RPC provider rejected work for rate limiting."""


_RATE_LIMIT_MARKERS = (
    "429 too many requests",
    "http 429",
    "http status 429",
    "status code: 429",
    "status code 429",
    "too many requests",
    "rate limit",
    "rate-limit",
    "ratelimit",
)


def _safe_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return value


def executor_rate_limited(completed: CompletedProcessLike) -> bool:
    """
    Detect a provider rate-limit response without returning raw transport text.

    The caller may inspect stdout/stderr internally for classification, but raw
    content must never be copied into user-visible reports or logs because RPC
    URLs can contain credentials.
    """
    if int(completed.returncode) == 0:
        return False
    combined = (
        _safe_text(completed.stderr)
        + "\n"
        + _safe_text(completed.stdout)
    ).casefold()
    return any(marker in combined for marker in _RATE_LIMIT_MARKERS)


def raise_for_executor_failure(completed: CompletedProcessLike) -> None:
    if int(completed.returncode) == 0:
        return
    if executor_rate_limited(completed):
        raise Phase2RpcRateLimited("RPC_RATE_LIMITED")
    raise RuntimeError(
        f"executor failed with status {int(completed.returncode)}"
    )
