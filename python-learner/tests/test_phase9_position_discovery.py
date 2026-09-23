from types import SimpleNamespace

import pytest

import meteora_learner.phase9_position_discovery as discovery_module
from meteora_learner.phase9_position_discovery import (
    discover_pool_positions_with_rust,
)


def payload():
    return {
        "pool_address": "pool-a",
        "positions_found": 2,
        "positions_returned": 2,
        "truncated": False,
        "positions": [
            {
                "position_address": "position-a",
                "pool_address": "pool-a",
                "owner": "owner-a",
                "lower_bin_id": -1,
                "upper_bin_id": 1,
            },
            {
                "position_address": "position-b",
                "pool_address": "pool-a",
                "owner": "owner-b",
                "lower_bin_id": -2,
                "upper_bin_id": 2,
            },
        ],
    }


def test_position_discovery_uses_env_rpc_without_rpc_argument(
    monkeypatch,
    tmp_path,
):
    binary = tmp_path / "meteora-executor"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("SOLANA_RPC_URL", "https://secret-rpc.invalid")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=discovery_module.json.dumps(payload()),
            stderr="",
        )

    monkeypatch.setattr(discovery_module.subprocess, "run", fake_run)

    result = discover_pool_positions_with_rust(
        "pool-a",
        limit=20,
        rust_binary_path=binary,
    )

    assert seen["command"] == [
        str(binary),
        "discover-pool-positions-env",
        "pool-a",
        "20",
    ]
    assert "https://secret-rpc.invalid" not in seen["command"]
    assert result.pool_address == "pool-a"
    assert result.positions_found == 2
    assert result.positions_returned == 2
    assert result.unique_owners == 2
    assert result.source_scope == "CURRENT_ONCHAIN_POSITION_COHORT"
    assert result.research_only is True
    assert result.read_only_capture is True
    assert result.policy_actionable is False
    assert result.execution_wired is False


def test_position_discovery_rejects_pool_mismatch(monkeypatch, tmp_path):
    binary = tmp_path / "meteora-executor"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.invalid")
    bad = payload()
    bad["pool_address"] = "pool-b"

    monkeypatch.setattr(
        discovery_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=discovery_module.json.dumps(bad),
            stderr="",
        ),
    )

    with pytest.raises(ValueError, match="different pool_address"):
        discover_pool_positions_with_rust(
            "pool-a",
            rust_binary_path=binary,
        )


def test_position_discovery_rejects_position_pool_mismatch(
    monkeypatch,
    tmp_path,
):
    binary = tmp_path / "meteora-executor"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.invalid")
    bad = payload()
    bad["positions"][0]["pool_address"] = "pool-b"

    monkeypatch.setattr(
        discovery_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=discovery_module.json.dumps(bad),
            stderr="",
        ),
    )

    with pytest.raises(ValueError, match="different pool"):
        discover_pool_positions_with_rust(
            "pool-a",
            rust_binary_path=binary,
        )


def test_position_discovery_rejects_duplicates(monkeypatch, tmp_path):
    binary = tmp_path / "meteora-executor"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.invalid")
    bad = payload()
    bad["positions"][1]["position_address"] = "position-a"

    monkeypatch.setattr(
        discovery_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=discovery_module.json.dumps(bad),
            stderr="",
        ),
    )

    with pytest.raises(ValueError, match="duplicate positions"):
        discover_pool_positions_with_rust(
            "pool-a",
            rust_binary_path=binary,
        )


def test_position_discovery_rejects_inconsistent_truncation(
    monkeypatch,
    tmp_path,
):
    binary = tmp_path / "meteora-executor"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.invalid")
    bad = payload()
    bad["positions_found"] = 3
    bad["truncated"] = False

    monkeypatch.setattr(
        discovery_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=discovery_module.json.dumps(bad),
            stderr="",
        ),
    )

    with pytest.raises(ValueError, match="truncated flag"):
        discover_pool_positions_with_rust(
            "pool-a",
            rust_binary_path=binary,
        )
