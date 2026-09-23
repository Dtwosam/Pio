from types import SimpleNamespace

import meteora_learner.phase9_mint_capture as capture_module
from meteora_learner.mint_ingest import ingest_mint_snapshot
from meteora_learner.phase9_mint_capture import (
    Phase9MintCaptureCriteria,
    build_phase9_mint_capture_plan,
    inspect_mint_with_rust,
    run_phase9_mint_capture,
)
from meteora_learner.storage import Storage


SPL = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def seed_pool(
    storage,
    pool,
    *,
    token_x,
    token_y,
    reward=None,
    observed_at="2026-09-23T12:00:00+00:00",
):
    storage.save_chain_pool_snapshot(
        {
            "pool_address": pool,
            "active_bin_id": 0,
            "bin_step": 25,
            "token_x_mint": token_x,
            "token_y_mint": token_y,
            "token_x_program": SPL,
            "token_y_program": SPL,
            "reward_mints": [reward, None],
            "bin_arrays": [],
        },
        observed_at=observed_at,
    )


def mint_payload(mint):
    return {
        "mint_address": mint,
        "token_program": SPL,
        "capture_slot_start": 100,
        "capture_slot_end": 101,
        "supply": "1000000",
        "decimals": 6,
        "is_initialized": True,
        "mint_authority": None,
        "freeze_authority": None,
        "data_len": 82,
        "token_2022_extension_data_len": 0,
        "has_token_2022_extension_data": False,
    }


def test_mint_capture_plan_deduplicates_required_mints_across_pools(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seed_pool(
        storage,
        "pool-a",
        token_x="shared",
        token_y="a-y",
        reward="reward",
    )
    seed_pool(
        storage,
        "pool-b",
        token_x="shared",
        token_y="b-y",
        reward="reward",
    )

    plan = build_phase9_mint_capture_plan(
        storage,
        as_of="2026-09-23T13:00:00+00:00",
    )

    assert plan.selected_pool_count == 2
    assert plan.required_mints == 4
    assert plan.captures_required == 4
    assert plan.inputs_ready is False
    shared = next(
        item for item in plan.candidates
        if item.mint_address == "shared"
    )
    assert shared.pools == ("pool-a", "pool-b")
    assert shared.roles == ("TOKEN_X",)


def test_mint_capture_plan_accepts_current_snapshots(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_pool(storage, "pool-a", token_x="a-x", token_y="a-y")
    seed_pool(storage, "pool-b", token_x="b-x", token_y="b-y")
    for mint in ("a-x", "a-y", "b-x", "b-y"):
        ingest_mint_snapshot(
            storage,
            mint_payload(mint),
            observed_at="2026-09-23T12:30:00+00:00",
        )

    plan = build_phase9_mint_capture_plan(
        storage,
        as_of="2026-09-23T13:00:00+00:00",
    )

    assert plan.current_mints == 4
    assert plan.captures_required == 0
    assert plan.inputs_ready is True
    assert all(
        item.age_seconds == 1800
        for item in plan.candidates
    )


def test_mint_capture_plan_refreshes_stale_snapshots(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_pool(storage, "pool-a", token_x="a-x", token_y="a-y")
    seed_pool(storage, "pool-b", token_x="b-x", token_y="b-y")
    for mint in ("a-x", "a-y", "b-x", "b-y"):
        ingest_mint_snapshot(
            storage,
            mint_payload(mint),
            observed_at="2026-09-23T11:00:00+00:00",
        )

    plan = build_phase9_mint_capture_plan(
        storage,
        as_of="2026-09-23T13:00:00+00:00",
    )

    assert plan.inputs_ready is False
    assert plan.captures_required == 4
    assert all(
        item.age_seconds == 7200
        for item in plan.candidates
    )
    assert all(
        "exceeds 3600s" in item.reason
        for item in plan.candidates
    )


def test_mint_capture_batch_refreshes_missing_inputs(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_pool(storage, "pool-a", token_x="a-x", token_y="a-y")
    seed_pool(storage, "pool-b", token_x="b-x", token_y="b-y")

    calls = []

    def inspector(mint):
        calls.append(mint)
        return mint_payload(mint)

    report = run_phase9_mint_capture(
        storage,
        inspector=inspector,
        observed_at="2026-09-23T13:00:00+00:00",
    )

    assert report.inputs_ready_before is False
    assert report.inputs_ready_after is True
    assert report.mints_attempted == 4
    assert report.mints_captured == 4
    assert report.mints_failed == 0
    assert report.captures_remaining_after == 0
    assert calls == ["a-x", "a-y", "b-x", "b-y"]
    assert report.research_only is True
    assert report.read_only_capture is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_mint_capture_batch_isolates_inspector_failure(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_pool(storage, "pool-a", token_x="a-x", token_y="a-y")
    seed_pool(storage, "pool-b", token_x="b-x", token_y="b-y")

    def inspector(mint):
        if mint == "b-y":
            raise RuntimeError("rpc unavailable")
        return mint_payload(mint)

    report = run_phase9_mint_capture(
        storage,
        inspector=inspector,
        observed_at="2026-09-23T13:00:00+00:00",
    )

    assert report.inputs_ready_after is False
    assert report.mints_captured == 3
    assert report.mints_failed == 1
    assert report.captures_remaining_after == 1
    failed = next(
        item for item in report.items
        if item.mint_address == "b-y"
    )
    assert "rpc unavailable" in failed.error


def test_inspect_mint_with_rust_uses_env_command_without_rpc_argument(
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
            stdout='{"mint_address":"mint"}',
            stderr="",
        )

    monkeypatch.setattr(capture_module.subprocess, "run", fake_run)

    payload = inspect_mint_with_rust(
        "mint",
        rust_binary_path=binary,
    )

    assert payload["mint_address"] == "mint"
    assert seen["command"] == [
        str(binary),
        "inspect-mint-env",
        "mint",
    ]
    assert "https://secret-rpc.invalid" not in seen["command"]


def test_mint_capture_plan_reports_missing_pool_diversity(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_pool(storage, "pool-a", token_x="a-x", token_y="a-y")

    plan = build_phase9_mint_capture_plan(
        storage,
        criteria=Phase9MintCaptureCriteria(target_pools=2),
        as_of="2026-09-23T13:00:00+00:00",
    )

    assert plan.inputs_ready is False
    assert any(
        "chain-observed pools 1 are below 2" in reason
        for reason in plan.reasons
    )
