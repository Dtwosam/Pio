from types import SimpleNamespace

import pytest

import meteora_learner.phase9_source_capture as source_module
from meteora_learner.phase9_source_capture import run_phase9_source_capture
from meteora_learner.settings import Settings
from meteora_learner.storage import Storage


class DummyRecord:
    def __init__(self, **values):
        self.values = values

    def to_record(self):
        return dict(self.values)


def test_source_capture_runs_bounded_read_only_acquisition_pass(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    settings = Settings(database_path=storage.path)
    calls = []

    monkeypatch.setattr(
        source_module,
        "collect_once",
        lambda settings: SimpleNamespace(
            run_id="api",
            pools_seen=3,
        ),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_chain_capture_batch",
        lambda *args, **kwargs: (
            calls.append("chain")
            or DummyRecord(target_met=True)
        ),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_history_capture",
        lambda *args, **kwargs: (
            calls.append("history")
            or DummyRecord(history_ready_after=True)
        ),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_mint_capture",
        lambda *args, **kwargs: (
            calls.append("mint")
            or DummyRecord(inputs_ready_after=True)
        ),
    )
    monkeypatch.setattr(
        source_module,
        "_top_chain_pools",
        lambda storage, limit: ("pool-a", "pool-b"),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_wallet_flow_capture",
        lambda storage, *, pool_address, **kwargs: (
            calls.append(f"wallet:{pool_address}")
            or DummyRecord(pool_address=pool_address)
        ),
    )
    monkeypatch.setattr(
        source_module,
        "build_phase9_history_plan",
        lambda storage: SimpleNamespace(plan_ready=True),
    )
    monkeypatch.setattr(
        source_module,
        "build_phase9_mint_capture_plan",
        lambda *args, **kwargs: SimpleNamespace(inputs_ready=True),
    )
    monkeypatch.setattr(
        source_module,
        "wallet_flow_source_state",
        lambda *args, **kwargs: SimpleNamespace(ready=True),
    )

    report = run_phase9_source_capture(
        storage,
        settings=settings,
    )

    assert calls == [
        "chain",
        "history",
        "mint",
        "wallet:pool-a",
        "wallet:pool-b",
    ]
    assert report.api_refresh["run_id"] == "api"
    assert report.chain_history_ready is True
    assert report.mint_inputs_ready is True
    assert report.wallet_source_ready_pools == 2
    assert report.automatic_source_ready is True
    assert report.errors == ()
    assert report.research_only is True
    assert report.read_only_capture is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_source_capture_isolates_family_failure_and_recomputes_readiness(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    settings = Settings(database_path=storage.path)

    monkeypatch.setattr(
        source_module,
        "collect_once",
        lambda settings: (_ for _ in ()).throw(
            RuntimeError("api down")
        ),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_chain_capture_batch",
        lambda *args, **kwargs: DummyRecord(target_met=False),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_history_capture",
        lambda *args, **kwargs: DummyRecord(history_ready_after=False),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_mint_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("rpc down")
        ),
    )
    monkeypatch.setattr(
        source_module,
        "_top_chain_pools",
        lambda storage, limit: ("pool-a",),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_wallet_flow_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("history unavailable")
        ),
    )
    monkeypatch.setattr(
        source_module,
        "build_phase9_history_plan",
        lambda storage: SimpleNamespace(plan_ready=False),
    )
    monkeypatch.setattr(
        source_module,
        "build_phase9_mint_capture_plan",
        lambda *args, **kwargs: SimpleNamespace(inputs_ready=False),
    )
    monkeypatch.setattr(
        source_module,
        "wallet_flow_source_state",
        lambda *args, **kwargs: SimpleNamespace(ready=False),
    )

    report = run_phase9_source_capture(
        storage,
        settings=settings,
    )

    assert report.automatic_source_ready is False
    assert report.chain_history_ready is False
    assert report.mint_inputs_ready is False
    assert report.wallet_source_ready_pools == 0
    assert len(report.errors) == 3
    assert any("api refresh failed" in value for value in report.errors)
    assert any("mint capture failed" in value for value in report.errors)
    assert any("wallet-flow capture failed" in value for value in report.errors)


def test_source_capture_forwards_history_observation_interval(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    settings = Settings(database_path=storage.path)
    seen = {}

    monkeypatch.setattr(
        source_module,
        "collect_once",
        lambda settings: SimpleNamespace(run_id="api"),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_chain_capture_batch",
        lambda *args, **kwargs: DummyRecord(target_met=True),
    )

    def fake_history(*args, **kwargs):
        seen["interval"] = kwargs["min_observation_interval_seconds"]
        return DummyRecord(history_ready_after=False)

    monkeypatch.setattr(
        source_module,
        "run_phase9_history_capture",
        fake_history,
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_mint_capture",
        lambda *args, **kwargs: DummyRecord(inputs_ready_after=True),
    )
    monkeypatch.setattr(
        source_module,
        "_top_chain_pools",
        lambda storage, limit: (),
    )
    monkeypatch.setattr(
        source_module,
        "build_phase9_history_plan",
        lambda storage: SimpleNamespace(plan_ready=False),
    )
    monkeypatch.setattr(
        source_module,
        "build_phase9_mint_capture_plan",
        lambda *args, **kwargs: SimpleNamespace(inputs_ready=True),
    )

    run_phase9_source_capture(
        storage,
        settings=settings,
        history_min_observation_interval_seconds=5400,
    )

    assert seen["interval"] == 5400


def test_source_capture_can_skip_api_refresh(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    settings = Settings(database_path=storage.path)
    api_calls = []

    monkeypatch.setattr(
        source_module,
        "collect_once",
        lambda settings: api_calls.append(settings),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_chain_capture_batch",
        lambda *args, **kwargs: DummyRecord(target_met=True),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_history_capture",
        lambda *args, **kwargs: DummyRecord(history_ready_after=True),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_mint_capture",
        lambda *args, **kwargs: DummyRecord(inputs_ready_after=True),
    )
    monkeypatch.setattr(
        source_module,
        "_top_chain_pools",
        lambda storage, limit: ("pool-a", "pool-b"),
    )
    monkeypatch.setattr(
        source_module,
        "run_phase9_wallet_flow_capture",
        lambda *args, **kwargs: DummyRecord(ok=True),
    )
    monkeypatch.setattr(
        source_module,
        "build_phase9_history_plan",
        lambda storage: SimpleNamespace(plan_ready=True),
    )
    monkeypatch.setattr(
        source_module,
        "build_phase9_mint_capture_plan",
        lambda *args, **kwargs: SimpleNamespace(inputs_ready=True),
    )
    monkeypatch.setattr(
        source_module,
        "wallet_flow_source_state",
        lambda *args, **kwargs: SimpleNamespace(ready=True),
    )

    report = run_phase9_source_capture(
        storage,
        settings=settings,
        refresh_api=False,
    )

    assert api_calls == []
    assert report.api_refresh is None
    assert report.automatic_source_ready is True


def test_source_capture_requires_matching_database_path(tmp_path):
    storage = Storage(tmp_path / "one.db")
    settings = Settings(database_path=tmp_path / "other.db")

    with pytest.raises(ValueError, match="database_path must match"):
        run_phase9_source_capture(
            storage,
            settings=settings,
            refresh_api=False,
        )
