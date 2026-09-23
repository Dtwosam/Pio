import json
from types import SimpleNamespace

import pytest

import meteora_learner.phase9_pool_activity_discovery as discovery_module
from meteora_learner.phase9_pool_activity_discovery import (
    discover_historical_pool_activity_with_rust,
)


def payload():
    return {
        "research_only": True,
        "read_only_capture": True,
        "pool_address": "pool-a",
        "before_signature": None,
        "signatures_requested": 2,
        "signatures_scanned": 2,
        "failed_transactions": 0,
        "matching_transactions": 1,
        "positions_found": 1,
        "has_more": True,
        "next_before_signature": "sig-oldest",
        "positions": [
            {
                "position_address": "position-a",
                "owner": "owner-a",
                "latest_matching_signature": "sig-new",
                "latest_matching_slot": 123,
                "latest_matching_block_time": 456,
            }
        ],
        "errors": [],
    }


def fake_binary(tmp_path):
    binary = tmp_path / "meteora-executor"
    binary.write_text("", encoding="utf-8")
    return binary


def test_historical_pool_activity_wrapper_validates_and_returns_report(
    monkeypatch,
    tmp_path,
):
    binary = fake_binary(tmp_path)
    monkeypatch.setenv("SOLANA_RPC_URL", "http://rpc")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload()),
            stderr="",
        )

    monkeypatch.setattr(discovery_module.subprocess, "run", fake_run)

    report = discover_historical_pool_activity_with_rust(
        "pool-a",
        limit=2,
        rust_binary_path=binary,
    )

    assert calls == [
        [
            str(binary),
            "discover-pool-activity-env",
            "pool-a",
            "2",
        ]
    ]
    assert report.research_only is True
    assert report.read_only_capture is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.source_scope == "HISTORICAL_POOL_SIGNATURE_ACTIVITY"
    assert report.positions_found == 1
    assert report.positions[0].position_address == "position-a"
    assert report.positions[0].owner == "owner-a"
    assert report.has_more is True
    assert report.next_before_signature == "sig-oldest"


def test_historical_pool_activity_wrapper_forwards_cursor(
    monkeypatch,
    tmp_path,
):
    binary = fake_binary(tmp_path)
    monkeypatch.setenv("SOLANA_RPC_URL", "http://rpc")
    value = payload()
    value["before_signature"] = "sig-before"
    monkeypatch.setattr(
        discovery_module.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(value),
            stderr="",
        ),
    )

    report = discover_historical_pool_activity_with_rust(
        "pool-a",
        limit=2,
        before_signature="sig-before",
        rust_binary_path=binary,
    )

    assert report.before_signature == "sig-before"


@pytest.mark.parametrize(
    "mutator, match",
    [
        (
            lambda value: value.__setitem__("pool_address", "pool-b"),
            "different pool",
        ),
        (
            lambda value: value.__setitem__("signatures_scanned", 3),
            "signatures_scanned",
        ),
        (
            lambda value: value.__setitem__("has_more", False),
            "has_more",
        ),
        (
            lambda value: value.__setitem__("next_before_signature", None),
            "next cursor",
        ),
        (
            lambda value: value["positions"].append(
                dict(value["positions"][0])
            ),
            "duplicate positions",
        ),
    ],
)
def test_historical_pool_activity_wrapper_rejects_malformed_output(
    monkeypatch,
    tmp_path,
    mutator,
    match,
):
    binary = fake_binary(tmp_path)
    monkeypatch.setenv("SOLANA_RPC_URL", "http://rpc")
    value = payload()
    mutator(value)
    if match == "duplicate positions":
        value["positions_found"] = 2

    monkeypatch.setattr(
        discovery_module.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(value),
            stderr="",
        ),
    )

    with pytest.raises(ValueError, match=match):
        discover_historical_pool_activity_with_rust(
            "pool-a",
            limit=2,
            rust_binary_path=binary,
        )


def test_historical_pool_activity_wrapper_requires_rpc_env(tmp_path, monkeypatch):
    binary = fake_binary(tmp_path)
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    monkeypatch.delenv("RPC_URL", raising=False)

    with pytest.raises(ValueError, match="SOLANA_RPC_URL"):
        discover_historical_pool_activity_with_rust(
            "pool-a",
            rust_binary_path=binary,
        )
