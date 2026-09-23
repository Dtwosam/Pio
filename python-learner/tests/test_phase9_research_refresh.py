from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import meteora_learner.phase9_research_refresh as refresh_module
from meteora_learner.phase9_research_refresh import (
    _mint_evaluation_as_of,
    _persist_if_changed,
    run_phase9_research_refresh,
)
from meteora_learner.phase9_bandit_dataset import (
    Phase9BanditDatasetLineage,
)
from meteora_learner.storage import Storage


def freshness_report(**overrides):
    values = {
        "adaptive_regime": True,
        "mint_risk": True,
        "wallet_flow": True,
        "portfolio_allocation": True,
        "static_hedge": True,
        "contextual_bandit": True,
    }
    values.update(overrides)
    families = tuple(
        SimpleNamespace(
            family=family,
            current=current,
            reason=(
                "test source current"
                if current
                else "test source advanced"
            ),
        )
        for family, current in values.items()
    )
    return SimpleNamespace(
        families=families,
        by_family=lambda: dict(values),
    )


@pytest.fixture(autouse=True)
def current_phase9_sources(monkeypatch):
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_source_freshness",
        lambda storage: freshness_report(),
    )


@pytest.fixture(autouse=True)
def ready_phase9_pool_cohort(monkeypatch):
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_pool_cohort",
        lambda storage: SimpleNamespace(
            research_ready=True,
            research_pools=("pool-a", "pool-b", "pool-c"),
            reasons=(),
        ),
    )


@dataclass
class DummyLineage:
    cycle_id: str
    champion_model_id: str
    dataset_evidence_id: int
    dataset_version: str
    dataset_sha256: str
    cutoff: str
    output_file: str


@dataclass
class DummyReport:
    name: str
    research_qualified: bool = True

    def to_record(self):
        return {
            "name": self.name,
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": self.research_qualified,
        }


def bundle(*, ready=False, mint=0, wallet=0, adaptive=0, bandit=0):
    return SimpleNamespace(
        research_ready=ready,
        reasons=() if ready else ("not ready",),
        adaptive_multi_pool=SimpleNamespace(qualified_records=adaptive),
        mint_risk=SimpleNamespace(qualified_records=mint),
        wallet_flow=SimpleNamespace(qualified_records=wallet),
        contextual_bandit=SimpleNamespace(qualified_records=bandit),
        to_record=lambda: {
            "research_only": True,
            "policy_actionable": False,
            "status": "READY" if ready else "BLOCKED",
            "research_ready": ready,
        },
    )


def test_persist_if_changed_does_not_append_identical_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    evidence = {
        "research_only": True,
        "policy_actionable": False,
        "value": 1,
    }
    evidence_id = storage.save_advanced_edge_evidence(
        edge_type="TEST_EDGE",
        pool_address="pool-a",
        as_of=None,
        status="READY",
        qualified=True,
        evidence=evidence,
    )
    calls = []

    status, result_id = _persist_if_changed(
        storage,
        edge_type="TEST_EDGE",
        pool_address="pool-a",
        evidence=evidence,
        persist=lambda: calls.append(True) or 999,
    )

    assert status == "UNCHANGED"
    assert result_id == evidence_id
    assert calls == []


def test_mint_evaluation_cutoff_uses_latest_timestamp_not_latest_id(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    base_payload = {
        "pool_address": "pool-a",
        "active_bin_id": 0,
        "bin_step": 25,
        "token_x_mint": "x",
        "token_y_mint": "y",
        "bin_arrays": [],
    }
    storage.save_chain_pool_snapshot(
        base_payload,
        observed_at="2026-09-23T14:00:00+00:00",
    )
    # Insert an older historical row later so its database ID is newer.
    storage.save_chain_pool_snapshot(
        base_payload,
        observed_at="2026-09-23T12:00:00+00:00",
    )
    mint_plan = SimpleNamespace(
        candidates=(
            SimpleNamespace(
                latest_snapshot_at="2026-09-23T13:00:00+00:00"
            ),
        )
    )

    cutoff = _mint_evaluation_as_of(
        storage,
        selected_pools=("pool-a",),
        mint_plan=mint_plan,
    )

    assert cutoff == "2026-09-23T14:00:00+00:00"


def test_research_refresh_stops_when_storage_integrity_is_invalid(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_storage_integrity",
        lambda storage: SimpleNamespace(
            verified=False,
            reasons=("immutable trigger missing",),
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "audit_persisted_phase8_promotion",
        lambda storage: calls.append("phase8"),
    )
    monkeypatch.setattr(
        refresh_module,
        "_replay_statuses",
        lambda *args, **kwargs: calls.append("replay"),
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: calls.append("bundle"),
    )

    report = run_phase9_research_refresh(storage)

    assert calls == []
    assert report.storage_integrity_verified is False
    assert report.phase8_current is False
    assert report.automatic_families_ready is False
    assert report.items[0].family == "storage_integrity"
    assert report.items[0].status == "BLOCKED"
    assert "immutable trigger missing" in report.items[0].reason
    assert report.bundle_reasons == (
        "storage integrity: immutable trigger missing",
    )


def test_research_refresh_stops_when_phase8_is_not_current(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    monkeypatch.setattr(
        refresh_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=False),
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: bundle(ready=False),
    )
    monkeypatch.setattr(
        refresh_module,
        "_replay_statuses",
        lambda *args, **kwargs: calls.append("replay"),
    )

    report = run_phase9_research_refresh(storage)

    assert calls == []
    assert report.storage_integrity_verified is True
    assert report.phase8_current is False
    assert report.automatic_families_ready is False
    assert report.bundle_ready_after is False
    assert len(report.items) == 1
    assert report.items[0].family == "phase8"
    assert report.items[0].status == "BLOCKED"


def test_research_refresh_skips_replay_verified_automatic_families(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    monkeypatch.setattr(
        refresh_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(
        refresh_module,
        "_replay_statuses",
        lambda *args, **kwargs: {
            "adaptive_regime": True,
            "mint_risk": True,
            "wallet_flow": True,
            "portfolio_allocation": True,
            "static_hedge": True,
            "contextual_bandit": True,
        },
    )
    monkeypatch.setattr(
        refresh_module,
        "build_phase9_history_plan",
        lambda *args, **kwargs: calls.append("history"),
    )
    monkeypatch.setattr(
        refresh_module,
        "build_phase9_mint_capture_plan",
        lambda *args, **kwargs: calls.append("mint"),
    )
    monkeypatch.setattr(
        refresh_module,
        "_top_chain_pools",
        lambda *args, **kwargs: calls.append("wallet"),
    )
    monkeypatch.setattr(
        refresh_module,
        "_latest_retraining_dataset_cycle",
        lambda *args, **kwargs: calls.append("bandit"),
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: bundle(
            ready=False,
            adaptive=1,
            mint=2,
            wallet=2,
            bandit=1,
        ),
    )

    report = run_phase9_research_refresh(storage)

    assert calls == []
    assert report.automatic_families_ready is True
    assert {
        item.family for item in report.items
    } == {
        "adaptive_regime",
        "mint_risk",
        "wallet_flow",
        "portfolio_allocation",
        "static_hedge",
        "contextual_bandit",
    }
    assert all(item.status == "UNCHANGED" for item in report.items)


def test_research_refresh_runs_ready_missing_families_and_bundle(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    persisted = []
    bundle_calls = {"count": 0}

    monkeypatch.setattr(
        refresh_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(
        refresh_module,
        "_replay_statuses",
        lambda *args, **kwargs: {
            "adaptive_regime": False,
            "mint_risk": False,
            "wallet_flow": False,
            "portfolio_allocation": True,
            "static_hedge": True,
            "contextual_bandit": False,
        },
    )
    monkeypatch.setattr(
        refresh_module,
        "build_phase9_history_plan",
        lambda storage: SimpleNamespace(
            plan_ready=True,
            reasons=(),
            pools=(
                SimpleNamespace(pool_address="pool-a"),
                SimpleNamespace(pool_address="pool-b"),
                SimpleNamespace(pool_address="pool-c"),
            ),
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research",
        lambda *args, **kwargs: DummyReport("adaptive"),
    )
    monkeypatch.setattr(
        refresh_module,
        "persist_phase9_research",
        lambda *args, **kwargs: persisted.append("adaptive") or 11,
    )

    monkeypatch.setattr(
        refresh_module,
        "build_phase9_mint_capture_plan",
        lambda *args, **kwargs: SimpleNamespace(
            inputs_ready=True,
            reasons=(),
            captures_required=0,
            selected_pools=("pool-a", "pool-b"),
            candidates=(
                SimpleNamespace(
                    latest_snapshot_at="2026-09-23T13:00:00+00:00"
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "_mint_evaluation_as_of",
        lambda *args, **kwargs: "2026-09-23T13:00:00+00:00",
    )
    monkeypatch.setattr(
        refresh_module,
        "research_pool_mint_risk",
        lambda storage, *, pool_address, **kwargs: DummyReport(
            f"mint-{pool_address}"
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "persist_pool_mint_risk",
        lambda storage, *, report: (
            persisted.append(report.name) or
            (21 if report.name.endswith("a") else 22)
        ),
    )

    monkeypatch.setattr(
        refresh_module,
        "_top_chain_pools",
        lambda storage, *, limit: ("pool-a", "pool-b"),
    )
    monkeypatch.setattr(
        refresh_module,
        "wallet_flow_source_state",
        lambda *args, **kwargs: SimpleNamespace(
            ready=True,
            events=20,
            unique_users=5,
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "research_wallet_flow",
        lambda storage, *, pool_address, **kwargs: DummyReport(
            f"wallet-{pool_address}"
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "persist_wallet_flow_research",
        lambda storage, *, report: (
            persisted.append(report.name) or
            (31 if report.name.endswith("a") else 32)
        ),
    )

    monkeypatch.setattr(
        refresh_module,
        "_latest_retraining_dataset_cycle",
        lambda storage: "cycle-1",
    )
    bandit_result = SimpleNamespace(
        lineage=DummyLineage(
            cycle_id="cycle-1",
            champion_model_id="model",
            dataset_evidence_id=1,
            dataset_version="v",
            dataset_sha256="a" * 64,
            cutoff="2026-09-23T12:00:00+00:00",
            output_file="/tmp/dataset.csv",
        ),
        report=DummyReport("bandit"),
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_cycle_contextual_bandit",
        lambda *args, **kwargs: bandit_result,
    )
    monkeypatch.setattr(
        refresh_module,
        "persist_cycle_contextual_bandit",
        lambda *args, **kwargs: persisted.append("bandit") or 41,
    )

    def fake_bundle(*args, **kwargs):
        bundle_calls["count"] += 1
        return bundle(
            ready=True,
            adaptive=1,
            mint=2,
            wallet=2,
            bandit=1,
        )

    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research_bundle",
        fake_bundle,
    )
    monkeypatch.setattr(
        refresh_module,
        "persist_phase9_research_bundle",
        lambda *args, **kwargs: persisted.append("bundle") or 51,
    )
    monkeypatch.setattr(
        refresh_module,
        "phase9_research_bundle_sha256",
        lambda payload: "b" * 64,
    )

    report = run_phase9_research_refresh(storage)

    assert report.storage_integrity_verified is True
    assert report.phase8_current is True
    assert report.automatic_families_ready is True
    assert report.bundle_ready_after is True
    assert report.bundle_persisted_evidence_id == 51
    assert bundle_calls["count"] == 1
    assert persisted == [
        "adaptive",
        "mint-pool-a",
        "mint-pool-b",
        "wallet-pool-a",
        "wallet-pool-b",
        "bandit",
        "bundle",
    ]
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False


def test_research_refresh_skips_explicit_families_without_valid_artifact(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    monkeypatch.setattr(
        refresh_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(
        refresh_module,
        "_replay_statuses",
        lambda *args, **kwargs: {
            "adaptive_regime": True,
            "mint_risk": True,
            "wallet_flow": True,
            "portfolio_allocation": False,
            "static_hedge": False,
            "contextual_bandit": True,
        },
    )
    monkeypatch.setattr(
        refresh_module,
        "audit_phase9_explicit_inputs",
        lambda storage: SimpleNamespace(
            valid=False,
            evidence_id=None,
            reasons=("explicit inputs missing",),
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: bundle(
            ready=False,
            adaptive=1,
            mint=2,
            wallet=2,
            bandit=1,
        ),
    )

    report = run_phase9_research_refresh(storage)

    explicit_items = {
        item.family: item
        for item in report.items
        if item.family in {
            "static_hedge",
            "portfolio_allocation",
        }
    }
    assert explicit_items["static_hedge"].status == (
        "SKIPPED_SOURCE_NOT_READY"
    )
    assert explicit_items["portfolio_allocation"].status == (
        "SKIPPED_SOURCE_NOT_READY"
    )
    assert "explicit inputs missing" in (
        explicit_items["static_hedge"].reason
    )
    assert "explicit inputs missing" in (
        explicit_items["portfolio_allocation"].reason
    )


def test_research_refresh_replays_only_missing_explicit_family(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    monkeypatch.setattr(
        refresh_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(
        refresh_module,
        "_replay_statuses",
        lambda *args, **kwargs: {
            "adaptive_regime": True,
            "mint_risk": True,
            "wallet_flow": True,
            "portfolio_allocation": True,
            "static_hedge": False,
            "contextual_bandit": True,
        },
    )
    monkeypatch.setattr(
        refresh_module,
        "audit_phase9_explicit_inputs",
        lambda storage: SimpleNamespace(
            valid=True,
            evidence_id=99,
            reasons=(),
        ),
    )
    artifact = SimpleNamespace(
        evidence_id=99,
        inputs=SimpleNamespace(
            static_hedges=(
                SimpleNamespace(pool_address="pool-a"),
            ),
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "load_phase9_explicit_inputs",
        lambda *args, **kwargs: artifact,
    )
    monkeypatch.setattr(
        refresh_module,
        "_latest_evidence_id",
        lambda *args, **kwargs: None,
    )

    def fake_explicit_run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            static_hedge_reports=(
                {
                    "pool_address": "pool-a",
                    "research_qualified": True,
                },
            ),
            static_hedge_evidence_ids=(101,),
            portfolio_allocation=None,
            portfolio_allocation_evidence_id=None,
        )

    monkeypatch.setattr(
        refresh_module,
        "run_phase9_explicit_research",
        fake_explicit_run,
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: bundle(
            ready=False,
            adaptive=1,
            mint=2,
            wallet=2,
            bandit=1,
        ),
    )

    report = run_phase9_research_refresh(storage)

    assert len(calls) == 1
    call = calls[0]
    assert call["persist"] is True
    assert call["deduplicate_persistence"] is True
    assert call["include_static_hedge"] is True
    assert call["include_portfolio"] is False
    assert call["persist_static_hedge"] is True
    assert call["persist_portfolio"] is False

    static_item = next(
        item for item in report.items
        if item.family == "static_hedge"
    )
    allocation_item = next(
        item for item in report.items
        if item.family == "portfolio_allocation"
    )
    assert static_item.status == "PERSISTED"
    assert static_item.persisted_evidence_id == 101
    assert static_item.research_qualified is True
    assert allocation_item.status == "UNCHANGED"


def test_research_refresh_derives_bandit_dataset_from_explicit_inputs(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    monkeypatch.setattr(
        refresh_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(
        refresh_module,
        "_replay_statuses",
        lambda *args, **kwargs: {
            "adaptive_regime": True,
            "mint_risk": True,
            "wallet_flow": True,
            "portfolio_allocation": True,
            "static_hedge": True,
            "contextual_bandit": False,
        },
    )
    monkeypatch.setattr(
        refresh_module,
        "_latest_retraining_dataset_cycle",
        lambda storage: None,
    )
    monkeypatch.setattr(
        refresh_module,
        "audit_phase9_explicit_inputs",
        lambda storage: SimpleNamespace(
            valid=True,
            evidence_id=77,
            reasons=(),
        ),
    )
    explicit = SimpleNamespace(
        evidence_id=77,
        inputs=SimpleNamespace(pool_inputs=(1, 2, 3)),
    )
    monkeypatch.setattr(
        refresh_module,
        "load_phase9_explicit_inputs",
        lambda *args, **kwargs: explicit,
    )
    monkeypatch.setattr(
        refresh_module,
        "build_phase9_bandit_dataset",
        lambda *args, **kwargs: (
            {"dataset": {"dataset_sha256": "a" * 64}},
            b"csv",
        ),
    )
    dataset_artifact = SimpleNamespace(evidence_id=88)
    monkeypatch.setattr(
        refresh_module,
        "persist_phase9_bandit_dataset",
        lambda *args, **kwargs: (
            calls.append("dataset") or dataset_artifact
        ),
    )
    lineage = Phase9BanditDatasetLineage(
        source_type="PHASE9_BANDIT_DATASET_V1",
        dataset_evidence_id=88,
        dataset_artifact_sha256="b" * 64,
        dataset_version="ML_ACTION_DATASET_V1:test",
        dataset_sha256="a" * 64,
        cutoff="2026-09-23T12:00:00+00:00",
        output_file="/tmp/phase9-bandit.csv",
        explicit_input_evidence_id=77,
        explicit_input_artifact_sha256="c" * 64,
    )
    bandit_result = SimpleNamespace(
        lineage=lineage,
        report=DummyReport("phase9-bandit"),
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_contextual_bandit_from_dataset",
        lambda *args, **kwargs: bandit_result,
    )
    monkeypatch.setattr(
        refresh_module,
        "persist_phase9_contextual_bandit_from_dataset",
        lambda *args, **kwargs: calls.append("bandit") or 89,
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: bundle(
            ready=False,
            adaptive=1,
            mint=2,
            wallet=2,
            bandit=1,
        ),
    )

    report = run_phase9_research_refresh(storage)

    item = next(
        item for item in report.items
        if item.family == "contextual_bandit"
    )
    assert item.scope == "88"
    assert item.status == "PERSISTED"
    assert item.research_qualified is True
    assert item.persisted_evidence_id == 89
    assert calls == ["dataset", "bandit"]
    assert "explicit input artifact 77" in item.reason


def test_research_refresh_recomputes_replay_verified_adaptive_when_source_advances(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    calls = []

    monkeypatch.setattr(
        refresh_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(
        refresh_module,
        "_replay_statuses",
        lambda *args, **kwargs: {
            "adaptive_regime": True,
            "mint_risk": True,
            "wallet_flow": True,
            "portfolio_allocation": True,
            "static_hedge": True,
            "contextual_bandit": True,
        },
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_source_freshness",
        lambda storage: freshness_report(adaptive_regime=False),
    )
    monkeypatch.setattr(
        refresh_module,
        "build_phase9_history_plan",
        lambda storage: SimpleNamespace(
            plan_ready=True,
            reasons=(),
            pools=(
                SimpleNamespace(pool_address="pool-a"),
                SimpleNamespace(pool_address="pool-b"),
                SimpleNamespace(pool_address="pool-c"),
            ),
        ),
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research",
        lambda *args, **kwargs: DummyReport("adaptive-fresh"),
    )
    monkeypatch.setattr(
        refresh_module,
        "persist_phase9_research",
        lambda *args, **kwargs: calls.append("adaptive") or 501,
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: bundle(
            ready=False,
            adaptive=1,
            mint=2,
            wallet=2,
            bandit=1,
        ),
    )

    report = run_phase9_research_refresh(storage)

    adaptive = next(
        item for item in report.items
        if item.family == "adaptive_regime"
    )
    assert adaptive.status == "PERSISTED"
    assert adaptive.persisted_evidence_id == 501
    assert calls == ["adaptive"]
    assert "source freshness: test source advanced" in adaptive.reason


def test_research_refresh_uses_ranked_history_ready_pool_cohort(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seen = {}

    monkeypatch.setattr(
        refresh_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(
        refresh_module,
        "_replay_statuses",
        lambda *args, **kwargs: {
            "adaptive_regime": False,
            "mint_risk": True,
            "wallet_flow": True,
            "portfolio_allocation": True,
            "static_hedge": True,
            "contextual_bandit": True,
        },
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_pool_cohort",
        lambda storage: SimpleNamespace(
            research_ready=True,
            research_pools=(
                "pool-a",
                "pool-b",
                "pool-c",
                "pool-f",
            ),
            reasons=(),
        ),
    )

    def evaluate(*args, **kwargs):
        seen["pools"] = kwargs["pool_addresses"]
        return DummyReport("adaptive-ranked")

    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research",
        evaluate,
    )
    monkeypatch.setattr(
        refresh_module,
        "persist_phase9_research",
        lambda *args, **kwargs: 601,
    )
    monkeypatch.setattr(
        refresh_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: bundle(
            ready=False,
            adaptive=1,
            mint=2,
            wallet=2,
            bandit=1,
        ),
    )

    report = run_phase9_research_refresh(storage)

    assert seen["pools"] == (
        "pool-a",
        "pool-b",
        "pool-c",
        "pool-f",
    )
    item = next(
        item for item in report.items
        if item.family == "adaptive_regime"
    )
    assert item.status == "PERSISTED"
    assert "ranked history-ready" in item.reason
