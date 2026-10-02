import subprocess

import pytest

from meteora_learner.phase2_rpc_guard import (
    Phase2RpcRateLimited,
    executor_rate_limited,
    raise_for_executor_failure,
)


@pytest.mark.parametrize(
    "stderr",
    (
        "HTTP 429 Too Many Requests",
        "request failed with status code: 429",
        "provider rate limit exceeded",
        "RPC rate-limit response",
    ),
)
def test_rpc_guard_detects_rate_limit_without_exposing_transport(stderr):
    secret = "https://rpc.example.invalid/?api-key=secret"
    completed = subprocess.CompletedProcess(
        ["/executor"],
        1,
        "",
        f"{stderr} at {secret}",
    )

    assert executor_rate_limited(completed) is True
    with pytest.raises(Phase2RpcRateLimited) as excinfo:
        raise_for_executor_failure(completed)

    assert str(excinfo.value) == "RPC_RATE_LIMITED"
    assert secret not in str(excinfo.value)


def test_rpc_guard_keeps_non_rate_limit_failures_generic_and_secret_safe():
    secret = "https://rpc.example.invalid/?api-key=secret"
    completed = subprocess.CompletedProcess(
        ["/executor"],
        1,
        "",
        f"transport failed at {secret}",
    )

    assert executor_rate_limited(completed) is False
    with pytest.raises(RuntimeError) as excinfo:
        raise_for_executor_failure(completed)

    message = str(excinfo.value)
    assert message == "executor failed with status 1"
    assert secret not in message


def test_rpc_guard_never_classifies_success_as_rate_limited():
    completed = subprocess.CompletedProcess(
        ["/executor"],
        0,
        '{"note":"429 Too Many Requests"}',
        "",
    )

    assert executor_rate_limited(completed) is False
    raise_for_executor_failure(completed)
