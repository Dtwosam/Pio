from __future__ import annotations

import subprocess


class Phase2RpcRateLimited(RuntimeError):
    """Safe sentinel for an executor failure caused by RPC rate limiting."""


def executor_is_rate_limited(
    completed: subprocess.CompletedProcess[str],
) -> bool:
    text = "\n".join(
        value
        for value in (completed.stdout, completed.stderr)
        if isinstance(value, str)
    ).lower()
    markers = (
        "too many requests",
        "rate limit",
        "ratelimit",
        "http 429",
        "status 429",
        "429 too many",
        "code: 429",
    )
    return any(marker in text for marker in markers)


def raise_for_executor_failure(
    completed: subprocess.CompletedProcess[str],
) -> None:
    if completed.returncode == 0:
        return
    if executor_is_rate_limited(completed):
        raise Phase2RpcRateLimited("executor RPC rate limited")
    raise RuntimeError(
        f"executor failed with status {completed.returncode}"
    )
