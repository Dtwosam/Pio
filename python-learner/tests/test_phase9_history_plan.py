from meteora_learner.adaptive_range import AdaptiveRangeCriteria
from meteora_learner.adaptive_range_validation import (
    AdaptiveRangeValidationCriteria,
)
from meteora_learner.market_regime import DLMMRegimeCriteria
from meteora_learner.phase9_history_plan import (
    adaptive_minimum_observations,
    build_phase9_history_plan,
)
from meteora_learner.phase9_research import Phase9ResearchCriteria
from meteora_learner.storage import Storage


def save_chain_observations(storage, pool, count):
    for index in range(count):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": pool,
                "active_bin_id": index % 4,
                "bin_step": 25,
                "token_x_mint": f"{pool}-x",
                "token_y_mint": f"{pool}-y",
                "bin_arrays": [],
            },
            observed_at=(
                f"2026-09-23T12:{index // 60:02d}:"
                f"{index % 60:02d}+00:00"
            ),
        )


def test_default_adaptive_history_minimum_is_43_observations():
    required = adaptive_minimum_observations(
        AdaptiveRangeCriteria(),
        AdaptiveRangeValidationCriteria(),
    )

    assert required == 43


def test_history_plan_default_minimum_pool_set_requires_three_ready_pools(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    save_chain_observations(storage, "pool-a", 43)
    save_chain_observations(storage, "pool-b", 43)
    save_chain_observations(storage, "pool-c", 42)

    plan = build_phase9_history_plan(
        storage,
        rpc_url="https://rpc.example.invalid",
    )

    assert plan.research_pools_required == 3
    assert plan.qualified_pools_required_at_minimum == 3
    assert plan.pools_history_ready == 2
    assert plan.plan_ready is False
    deficient = next(
        item for item in plan.pools
        if item.pool_address == "pool-c"
    )
    assert deficient.required_observations == 43
    assert deficient.additional_observations_needed == 1
    assert deficient.history_ready is False
    assert deficient.shell_command is not None
    assert "inspect-pool" in deficient.shell_command
    assert "https://rpc.example.invalid" in deficient.shell_command


def test_history_plan_is_ready_when_three_default_pools_have_depth(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 43)

    plan = build_phase9_history_plan(storage)

    assert plan.plan_ready is True
    assert plan.pools_history_ready == 3
    assert plan.reasons == ()
    assert all(
        item.additional_observations_needed == 0
        for item in plan.pools
    )
    assert all(item.shell_command is None for item in plan.pools)


def test_history_plan_selects_deepest_chain_pools_first(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_chain_observations(storage, "pool-a", 10)
    save_chain_observations(storage, "pool-b", 30)
    save_chain_observations(storage, "pool-c", 20)
    save_chain_observations(storage, "pool-d", 40)

    plan = build_phase9_history_plan(storage)

    assert [item.pool_address for item in plan.pools] == [
        "pool-d",
        "pool-b",
        "pool-c",
    ]


def test_history_formula_respects_custom_holding_windows_and_decisions():
    adaptive = AdaptiveRangeCriteria(
        lookback_observations=30,
        holding_observations=4,
        min_historical_windows=5,
    )
    validation = AdaptiveRangeValidationCriteria(
        min_decisions=7,
    )

    required = adaptive_minimum_observations(
        adaptive,
        validation,
    )

    assert required == 19


def test_history_plan_fails_closed_when_lookback_cannot_form_windows(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    for pool in ("pool-a", "pool-b", "pool-c"):
        save_chain_observations(storage, pool, 50)

    plan = build_phase9_history_plan(
        storage,
        adaptive_criteria=AdaptiveRangeCriteria(
            lookback_observations=10,
            holding_observations=6,
            min_historical_windows=5,
        ),
        validation_criteria=AdaptiveRangeValidationCriteria(
            min_decisions=1,
        ),
        regime_criteria=DLMMRegimeCriteria(
            lookback_observations=20,
            recent_observations=4,
            min_observations=6,
        ),
    )

    assert plan.plan_ready is False
    assert any(
        "lookback cannot contain" in reason
        for reason in plan.reasons
    )
    assert all(item.history_ready is False for item in plan.pools)


def test_history_plan_reports_missing_chain_pool_diversity(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_chain_observations(storage, "pool-a", 43)
    save_chain_observations(storage, "pool-b", 43)

    plan = build_phase9_history_plan(storage)

    assert plan.plan_ready is False
    assert plan.chain_pools_seen == 2
    assert any(
        "chain-observed pools 2 are below 3" in reason
        for reason in plan.reasons
    )


def test_history_plan_excludes_chain_snapshots_after_as_of(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    save_chain_observations(storage, "pool-a", 5)
    for index in range(50):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": "pool-a",
                "active_bin_id": index % 4,
                "bin_step": 25,
                "token_x_mint": "pool-a-x",
                "token_y_mint": "pool-a-y",
                "bin_arrays": [],
            },
            observed_at=(
                f"2026-09-23T14:{index // 60:02d}:"
                f"{index % 60:02d}+00:00"
            ),
        )

    plan = build_phase9_history_plan(
        storage,
        research_criteria=Phase9ResearchCriteria(
            min_pools=1,
            min_qualified_pools=1,
            min_qualified_pool_rate=1.0,
        ),
        as_of="2026-09-23T13:00:00+00:00",
    )

    assert plan.as_of == "2026-09-23T13:00:00+00:00"
    assert plan.chain_pools_seen == 1
    assert plan.pools[0].observations == 5
    assert plan.pools[0].required_observations == 43
    assert plan.pools[0].additional_observations_needed == 38
    assert plan.plan_ready is False
