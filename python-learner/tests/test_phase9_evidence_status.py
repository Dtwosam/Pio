from types import SimpleNamespace

import meteora_learner.phase9_evidence_status as status_module
from meteora_learner.phase9_evidence_status import (
    evaluate_phase9_evidence_status,
)
from meteora_learner.phase9_validation import Phase9ResearchBundleCriteria
from meteora_learner.storage import Storage


def summary(qualified, pools=()):
    return SimpleNamespace(
        qualified_records=qualified,
        qualified_pools=tuple(pools),
    )


def test_phase9_evidence_status_reports_quantitative_source_gaps(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    cohort = SimpleNamespace(
        api_pools_seen=3,
        stale_api_pools_excluded=2,
        criteria=SimpleNamespace(min_research_pools=3),
        required_observations=43,
        desired_pools=("pool-a", "pool-b", "pool-c"),
        research_pools=("pool-a",),
        sampling_pools=("pool-a", "pool-b", "pool-c"),
        missing_chain_pools=(),
        research_ready=False,
        items=(
            SimpleNamespace(
                pool_address="pool-a",
                rank=1,
                chain_observations=43,
                history_ready=True,
            ),
            SimpleNamespace(
                pool_address="pool-b",
                rank=2,
                chain_observations=30,
                history_ready=False,
            ),
            SimpleNamespace(
                pool_address="pool-c",
                rank=3,
                chain_observations=10,
                history_ready=False,
            ),
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_pool_cohort",
        lambda *args, **kwargs: cohort,
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion_at",
        lambda storage, *, as_of: SimpleNamespace(
            valid_at_cutoff=True
        ),
    )
    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs",
        lambda storage: SimpleNamespace(
            valid=True,
            evidence_id=77,
        ),
    )
    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs_at",
        lambda storage, *, as_of: SimpleNamespace(
            valid=True,
            evidence_id=77,
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_source_freshness",
        lambda *args, **kwargs: SimpleNamespace(
            current=False,
            families=(
                SimpleNamespace(
                    family="adaptive_regime",
                    current=False,
                    reason="pool-a chain history advanced",
                ),
                SimpleNamespace(
                    family="mint_risk",
                    current=True,
                    reason="current",
                ),
                SimpleNamespace(
                    family="wallet_flow",
                    current=False,
                    reason="pool-b event history advanced",
                ),
            ),
        ),
    )

    def mint_plan(*args, **kwargs):
        pool = kwargs["pool_addresses"][0]
        return SimpleNamespace(
            inputs_ready=(pool == "pool-a"),
            captures_required=(0 if pool == "pool-a" else 1),
        )

    monkeypatch.setattr(
        status_module,
        "build_phase9_mint_capture_plan",
        mint_plan,
    )

    def wallet_state(*args, **kwargs):
        pool = kwargs["pool_address"]
        return (
            SimpleNamespace(events=20, unique_users=5, ready=True)
            if pool == "pool-a"
            else SimpleNamespace(events=10, unique_users=2, ready=False)
        )

    monkeypatch.setattr(
        status_module,
        "wallet_flow_source_state",
        wallet_state,
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            adaptive_multi_pool=summary(0),
            mint_risk=summary(1, ("pool-a",)),
            wallet_flow=summary(1, ("pool-a",)),
            portfolio_allocation=summary(0),
            static_hedge=summary(0),
            contextual_bandit=summary(0),
            research_ready=False,
            reasons=("research bundle is incomplete",),
        ),
    )

    report = evaluate_phase9_evidence_status(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=2,
            min_wallet_flow_pools=2,
            min_static_hedge_pools=1,
        ),
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.execution_wired is False
    assert report.phase8_current is True
    assert report.max_history_samples_remaining == 33
    assert report.mint_ready_pools == 1
    assert report.mint_required_pools == 2
    assert report.wallet_ready_pools == 1
    assert report.wallet_required_pools == 2
    assert report.explicit_inputs_valid is True
    assert report.explicit_input_evidence_id == 77
    assert report.research_sources_current is False
    assert report.research_bundle_ready is False

    by_pool = {item.pool_address: item for item in report.pools}
    assert by_pool["pool-a"].chain_observations_remaining == 0
    assert by_pool["pool-a"].mint_inputs_ready is True
    assert by_pool["pool-a"].wallet_source_ready is True
    assert by_pool["pool-b"].chain_observations_remaining == 13
    assert by_pool["pool-b"].mint_inputs_ready is False
    assert by_pool["pool-b"].wallet_events == 10
    assert by_pool["pool-c"].chain_observations_remaining == 33
    assert by_pool["pool-c"].mint_target is False
    assert by_pool["pool-c"].wallet_target is False

    by_family = {item.family: item for item in report.families}
    assert by_family["mint_risk"].qualified_records == 1
    assert by_family["mint_risk"].required_records == 2
    assert by_family["mint_risk"].ready is False
    assert by_family["adaptive_regime"].required_records == 1

    assert any("ranked mint inputs ready 1/2" in r for r in report.reasons)
    assert any("ranked wallet sources ready 1/2" in r for r in report.reasons)
    assert any("research source refresh is pending" in r for r in report.reasons)
    assert any(
        "source freshness adaptive_regime: pool-a chain history advanced"
        in r
        for r in report.reasons
    )
    assert any(
        "source freshness wallet_flow: pool-b event history advanced"
        in r
        for r in report.reasons
    )


def test_phase9_evidence_status_reports_ready_counts(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    cohort = SimpleNamespace(
        api_pools_seen=3,
        stale_api_pools_excluded=0,
        criteria=SimpleNamespace(min_research_pools=3),
        required_observations=43,
        desired_pools=("pool-a", "pool-b", "pool-c"),
        research_pools=("pool-a", "pool-b", "pool-c"),
        sampling_pools=("pool-a", "pool-b", "pool-c"),
        missing_chain_pools=(),
        research_ready=True,
        items=tuple(
            SimpleNamespace(
                pool_address=pool,
                rank=index,
                chain_observations=43,
                history_ready=True,
            )
            for index, pool in enumerate(
                ("pool-a", "pool-b", "pool-c"),
                start=1,
            )
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_pool_cohort",
        lambda *args, **kwargs: cohort,
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion_at",
        lambda storage, *, as_of: SimpleNamespace(
            valid_at_cutoff=True
        ),
    )
    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs",
        lambda storage: SimpleNamespace(valid=True, evidence_id=88),
    )
    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs_at",
        lambda storage, *, as_of: SimpleNamespace(
            valid=True,
            evidence_id=88,
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_source_freshness",
        lambda *args, **kwargs: SimpleNamespace(
            current=True,
            families=(),
        ),
    )
    monkeypatch.setattr(
        status_module,
        "build_phase9_mint_capture_plan",
        lambda *args, **kwargs: SimpleNamespace(
            inputs_ready=True,
            captures_required=0,
        ),
    )
    monkeypatch.setattr(
        status_module,
        "wallet_flow_source_state",
        lambda *args, **kwargs: SimpleNamespace(
            events=20,
            unique_users=5,
            ready=True,
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            adaptive_multi_pool=summary(1, ("__MULTI_POOL__",)),
            mint_risk=summary(2, ("pool-a", "pool-b")),
            wallet_flow=summary(2, ("pool-a", "pool-b")),
            portfolio_allocation=summary(1, ("__PORTFOLIO__",)),
            static_hedge=summary(1, ("pool-a",)),
            contextual_bandit=summary(1, ("__CONTEXTUAL_BANDIT__",)),
            research_ready=True,
            reasons=(),
        ),
    )

    report = evaluate_phase9_evidence_status(
        storage,
        as_of="2026-09-23T23:00:00+00:00",
    )

    assert report.chain_history_ready is True
    assert report.max_history_samples_remaining == 0
    assert report.mint_ready_pools == 2
    assert report.wallet_ready_pools == 2
    assert report.research_sources_current is True
    assert report.research_bundle_ready is True
    assert report.reasons == ()


def test_phase9_historical_status_uses_cutoff_phase8_audit_only(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    seen = {}

    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion",
        lambda storage: (_ for _ in ()).throw(
            AssertionError("current Phase 8 audit must not run")
        ),
    )
    def historical_phase8_audit(storage, *, as_of):
        seen["as_of"] = as_of
        return SimpleNamespace(valid_at_cutoff=False)

    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion_at",
        historical_phase8_audit,
    )
    cohort = SimpleNamespace(
        api_pools_seen=0,
        stale_api_pools_excluded=0,
        criteria=SimpleNamespace(min_research_pools=3),
        required_observations=43,
        desired_pools=(),
        research_pools=(),
        sampling_pools=(),
        missing_chain_pools=(),
        research_ready=False,
        items=(),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_pool_cohort",
        lambda *args, **kwargs: cohort,
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_source_freshness",
        lambda *args, **kwargs: SimpleNamespace(
            current=False,
            families=(),
        ),
    )
    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs",
        lambda storage: (_ for _ in ()).throw(
            AssertionError("current explicit-input audit must not run")
        ),
    )
    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs_at",
        lambda storage, *, as_of: SimpleNamespace(
            valid=False,
            evidence_id=None,
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            adaptive_multi_pool=summary(0),
            mint_risk=summary(0),
            wallet_flow=summary(0),
            portfolio_allocation=summary(0),
            static_hedge=summary(0),
            contextual_bandit=summary(0),
            research_ready=False,
            reasons=(),
        ),
    )

    cutoff = "2026-09-23T23:00:00+00:00"
    report = evaluate_phase9_evidence_status(
        storage,
        as_of=cutoff,
    )

    assert seen["as_of"] == cutoff
    assert report.phase8_current is False
    assert any(
        "was not valid at the historical cutoff" in reason
        for reason in report.reasons
    )


def test_phase9_current_status_uses_live_phase8_audit_only(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion",
        lambda storage: SimpleNamespace(current=False),
    )
    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion_at",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("historical Phase 8 audit must not run")
        ),
    )
    cohort = SimpleNamespace(
        api_pools_seen=0,
        stale_api_pools_excluded=0,
        criteria=SimpleNamespace(min_research_pools=3),
        required_observations=43,
        desired_pools=(),
        research_pools=(),
        sampling_pools=(),
        missing_chain_pools=(),
        research_ready=False,
        items=(),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_pool_cohort",
        lambda *args, **kwargs: cohort,
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_source_freshness",
        lambda *args, **kwargs: SimpleNamespace(
            current=False,
            families=(),
        ),
    )
    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs",
        lambda storage: SimpleNamespace(
            valid=False,
            evidence_id=None,
        ),
    )
    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs_at",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("historical explicit-input audit must not run")
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            adaptive_multi_pool=summary(0),
            mint_risk=summary(0),
            wallet_flow=summary(0),
            portfolio_allocation=summary(0),
            static_hedge=summary(0),
            contextual_bandit=summary(0),
            research_ready=False,
            reasons=(),
        ),
    )

    report = evaluate_phase9_evidence_status(storage)

    assert report.phase8_current is False
    assert any(
        reason == "Phase 8 promotion is not current"
        for reason in report.reasons
    )


def test_phase9_historical_status_excludes_future_explicit_inputs(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")

    monkeypatch.setattr(
        status_module,
        "audit_persisted_phase8_promotion_at",
        lambda storage, *, as_of: SimpleNamespace(
            valid_at_cutoff=True
        ),
    )
    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs",
        lambda storage: (_ for _ in ()).throw(
            AssertionError("current explicit-input audit must not run")
        ),
    )

    seen = {}

    def historical_explicit(storage, *, as_of):
        seen["as_of"] = as_of
        return SimpleNamespace(
            valid=False,
            evidence_id=None,
        )

    monkeypatch.setattr(
        status_module,
        "audit_phase9_explicit_inputs_at",
        historical_explicit,
    )
    cohort = SimpleNamespace(
        api_pools_seen=0,
        stale_api_pools_excluded=0,
        criteria=SimpleNamespace(min_research_pools=3),
        required_observations=43,
        desired_pools=(),
        research_pools=(),
        sampling_pools=(),
        missing_chain_pools=(),
        research_ready=False,
        items=(),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_pool_cohort",
        lambda *args, **kwargs: cohort,
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_source_freshness",
        lambda *args, **kwargs: SimpleNamespace(
            current=False,
            families=(),
        ),
    )
    monkeypatch.setattr(
        status_module,
        "evaluate_phase9_research_bundle",
        lambda *args, **kwargs: SimpleNamespace(
            adaptive_multi_pool=summary(0),
            mint_risk=summary(0),
            wallet_flow=summary(0),
            portfolio_allocation=summary(0),
            static_hedge=summary(0),
            contextual_bandit=summary(0),
            research_ready=False,
            reasons=(),
        ),
    )

    cutoff = "2026-09-24T11:00:00+00:00"
    report = evaluate_phase9_evidence_status(
        storage,
        as_of=cutoff,
    )

    assert seen["as_of"] == cutoff
    assert report.explicit_inputs_valid is False
    assert report.explicit_input_evidence_id is None
    assert any(
        "explicit research inputs are not valid" in reason
        for reason in report.reasons
    )
