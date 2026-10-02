import subprocess

import pytest

from meteora_learner.phase2_rpc import (
    Phase2RpcRateLimited,
    executor_is_rate_limited,
    raise_for_executor_failure,
)


@pytest.mark.parametrize(
    "message",
    (
        "HTTP 429 Too Many Requests",
        "provider rate limit exceeded",
        "RPC ratelimit reached",
        "request failed with status 429",
        "error code: 429",
    ),
)
def test_rate_limit_markers_are_detected(message):
    completed = subprocess.CompletedProcess(
        ["executor"], 1, "", message
    )
    assert executor_is_rate_limited(completed) is True


def test_non_rate_limit_failure_stays_generic_and_secret_safe():
    secret = "https://user:secret@example.invalid/rpc"
    completed = subprocess.CompletedProcess(
        ["executor"], 1, "", f"transport failed at {secret}"
    )

    with pytest.raises(RuntimeError) as excinfo:
        raise_for_executor_failure(completed)

    message = str(excinfo.value)
    assert message == "executor failed with status 1"
    assert secret not in message


def test_rate_limit_exception_never_echoes_provider_text():
    secret = "https://secret.example.invalid/?api-key=hidden"
    completed = subprocess.CompletedProcess(
        ["executor"],
        1,
        "",
        f"HTTP 429 Too Many Requests at {secret}",
    )

    with pytest.raises(Phase2RpcRateLimited) as excinfo:
        raise_for_executor_failure(completed)

    message = str(excinfo.value)
    assert message == "executor RPC rate limited"
    assert secret not in message


def test_success_never_raises_even_if_output_mentions_429():
    completed = subprocess.CompletedProcess(
        ["executor"], 0, "historical note: 429", ""
    )
    raise_for_executor_failure(completed)
