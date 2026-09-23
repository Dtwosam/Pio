from types import SimpleNamespace

import meteora_learner.phase9_evidence_plan as plan_module
from meteora_learner.phase9_evidence_plan import (
    build_phase9_evidence_plan,
)
from meteora_learner.phase9_pool_cohort import Phase9PoolCohortCriteria
from meteora_learner.phase9_validation import Phase9ResearchBundleCriteria
from meteora_learner.storage import Storage


def family(name, ready):
    return SimpleNamespace(
        family=name,
        ready=ready,
        qualified_records=1 if ready else 0,
        required_records=1,
    )


def pool(
    address,
    rank,
    *,
    remaining=0,
    mint_target=False,
    mint_ready=None,
    mint_captures=0,
    wallet_target=False,
    wallet_ready=None,
    wallet_events=None,
    wallet_users=None,
):
    return SimpleNamespace(
        pool_address=address,
        rank=rank,
        chain_observations_remaining=remaining,
        mint_target=mint_target,
        mint_inputs_ready=mint_ready,
        mint_captures_required=mint_captures,
        wallet_target=wallet_target,
        wallet_source_ready=wallet_ready,
        wallet_events=wallet_events,
        wallet_unique_users=wallet_users,
    )


def status(**overrides):
    base = dict(
        phase8_current=True,
        fresh_api_pools=3,
        stale_api_pools_excluded=0,
        desired_pools=("pool-a", "pool-b", "pool-c"),
        research_pools=("pool-a", "pool-b", "pool-c"),
        sampling_pools=("pool-a", "pool-b", "pool-c"),
        missing_chain_pools=(),
        chain_history_ready=True,
        max_history_samples_remaining=0,
        mint_ready_pools=2,
        mint_required_pools=2,
        wallet_ready_pools=2,
        wallet_required_pools=2,
        explicit_inputs_valid=True,
        explicit_input_evidence_id=77,
        research_sources_current=True,
        research_bundle_ready=True,
        pools=(
            pool(
                "pool-a",
                1,
                mint_target=True,
                mint_ready=True,
                wallet_target=True,
                wallet_ready=True,
                wallet_events=20,
                wallet_users=5,
            ),
            pool(
                "pool-b",
                2,
                mint_target=True,
                mint_ready=True,
                wallet_target=True,
                wallet_ready=True,
                wallet_events=20,
                wallet_users=5,
            ),
            pool("pool-c", 3),
        ),
        families=tuple(
            family(name, True)
            for name in (
                "adaptive_regime",
                "mint_risk",
                "wallet_flow",
                "portfolio_allocation",
                "static_hedge",
                "contextual_bandit",
            )
        ),
        reasons=(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_evidence_plan_prioritizes_first_actionable_source_debt(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    report = status(
        phase8_current=False,
        fresh_api_pools=1,
        missing_chain_pools=("pool-b", "pool-c"),
        chain_history_ready=False,
        max_history_samples_remaining=33,
        mint_ready_pools=1,
        wallet_ready_pools=1,
        explicit_inputs_valid=False,
        research_sources_current=False,
        research_bundle_ready=False,
        pools=(
            pool(
                "pool-a",
                1,
                remaining=0,
                mint_target=True,
                mint_ready=True,
                wallet_target=True,
                wallet_ready=True,
                wallet_events=20,
                wallet_users=5,
            ),
            pool(
                "pool-b",
                2,
                remaining=13,
                mint_target=True,
                mint_ready=False,
                mint_captures=2,
                wallet_target=True,
                wallet_ready=False,
                wallet_events=10,
                wallet_users=2,
            ),
            pool("pool-c", 3, remaining=33),
        ),
        families=(
            family("adaptive_regime", False),
            family("mint_risk", False),
            family("wallet_flow", False),
            family("portfolio_allocation", False),
            family("static_hedge", False),
            family("contextual_bandit", False),
        ),
        reasons=("research bundle is incomplete",),
    )
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase9_evidence_status",
        lambda *args, **kwargs: report,
    )

    plan = build_phase9_evidence_plan(
        storage,
        criteria=Phase9ResearchBundleCriteria(
            min_mint_risk_pools=2,
            min_wallet_flow_pools=2,
            min_static_hedge_pools=1,
        ),
        cohort_criteria=Phase9PoolCohortCriteria(
            min_research_pools=3,
        ),
        as_of="2026-09-24T00:00:00+00:00",
    )

    assert plan.research_only is True
    assert plan.read_only_commands is True
    assert plan.policy_actionable is False
    assert plan.execution_wired is False
    assert plan.research_bundle_ready is False
    assert plan.source_ready is False
    assert plan.history_capture_cycles_remaining == 33
    assert plan.next_action is not None
    assert plan.next_action.debt_type == "API_POOL_COVERAGE"
    assert plan.next_action.shell_command == "pio collect-once"

    debt_types = [item.debt_type for item in plan.items]
    assert debt_types[:4] == [
        "PHASE8_DEPENDENCY",
        "API_POOL_COVERAGE",
        "CHAIN_POOL_COVERAGE",
        "CHAIN_HISTORY_DEPTH",
    ]
    assert "MINT_INPUTS" in debt_types
    assert "WALLET_FLOW_SOURCE" in debt_types
    assert "EXPLICIT_RESEARCH_INPUTS" in debt_types
    assert "RESEARCH_REFRESH" in debt_types

    history = next(
        item for item in plan.items
        if item.debt_type == "CHAIN_HISTORY_DEPTH"
    )
    assert history.remaining == 33
    assert "pool-b:13" in history.reason
    assert "pool-c:33" in history.reason
    assert (
        history.shell_command
        == "pio phase9-chain-history-run "
        "--min-observation-interval-seconds 3600"
    )

    mint = next(
        item for item in plan.items
        if item.debt_type == "MINT_INPUTS"
    )
    assert "--pools pool-a,pool-b" in mint.shell_command
    assert "--target-pools 2" in mint.shell_command

    wallet = next(
        item for item in plan.items
        if item.debt_type == "WALLET_FLOW_SOURCE"
    )
    assert wallet.scope == "pool-b"
    assert "events 10 remaining" in wallet.reason
    assert "unique users 3 remaining" in wallet.reason


def test_evidence_plan_uses_explicit_inputs_as_next_action(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    report = status(
        explicit_inputs_valid=False,
        research_bundle_ready=False,
        families=(
            family("adaptive_regime", True),
            family("mint_risk", True),
            family("wallet_flow", True),
            family("portfolio_allocation", False),
            family("static_hedge", False),
            family("contextual_bandit", False),
        ),
    )
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase9_evidence_status",
        lambda *args, **kwargs: report,
    )

    plan = build_phase9_evidence_plan(storage)

    assert plan.source_ready is False
    assert plan.next_action is not None
    assert plan.next_action.debt_type == "EXPLICIT_RESEARCH_INPUTS"
    assert "phase9-research-input-template" in (
        plan.next_action.shell_command
    )
    assert "--pools pool-a,pool-b,pool-c" in (
        plan.next_action.shell_command
    )


def test_evidence_plan_refreshes_research_after_sources_are_ready(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    report = status(
        research_sources_current=False,
        research_bundle_ready=False,
        families=(
            family("adaptive_regime", True),
            family("mint_risk", True),
            family("wallet_flow", True),
            family("portfolio_allocation", True),
            family("static_hedge", True),
            family("contextual_bandit", False),
        ),
        reasons=("contextual bandit is missing",),
    )
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase9_evidence_status",
        lambda *args, **kwargs: report,
    )

    plan = build_phase9_evidence_plan(storage)

    assert plan.source_ready is True
    assert plan.next_action is not None
    assert plan.next_action.debt_type == "RESEARCH_REFRESH"
    assert (
        plan.next_action.shell_command
        == "pio phase9-research-refresh-run"
    )
    assert "contextual_bandit" in plan.next_action.scope


def test_evidence_plan_is_empty_when_bundle_is_ready(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase9_evidence_status",
        lambda *args, **kwargs: status(),
    )

    plan = build_phase9_evidence_plan(storage)

    assert plan.research_bundle_ready is True
    assert plan.source_ready is True
    assert plan.next_action is None
    assert plan.items == ()
    assert plan.reasons == ()


def test_evidence_plan_validates_history_interval(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    try:
        build_phase9_evidence_plan(
            storage,
            history_interval_seconds=-1,
        )
    except ValueError as exc:
        assert "history_interval_seconds" in str(exc)
    else:
        raise AssertionError("expected invalid history interval failure")


def test_evidence_plan_stops_at_phase8_after_independent_debt_is_clear(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    report = status(
        phase8_current=False,
        research_sources_current=False,
        research_bundle_ready=False,
        families=(
            family("adaptive_regime", False),
            family("mint_risk", False),
            family("wallet_flow", False),
            family("portfolio_allocation", False),
            family("static_hedge", False),
            family("contextual_bandit", False),
        ),
    )
    monkeypatch.setattr(
        plan_module,
        "evaluate_phase9_evidence_status",
        lambda *args, **kwargs: report,
    )

    plan = build_phase9_evidence_plan(storage)

    assert plan.next_action is not None
    assert plan.next_action.debt_type == "PHASE8_DEPENDENCY"
    assert plan.next_action.actionable is False
    assert plan.next_action.shell_command == "pio phase8-evidence-plan"
    refresh = next(
        item for item in plan.items
        if item.debt_type == "RESEARCH_REFRESH"
    )
    assert refresh.actionable is False
    assert refresh.shell_command is None
    assert "blocked until Phase 8" in refresh.reason
    assert "contextual_bandit" in refresh.reason
