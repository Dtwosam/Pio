from meteora_learner.baseline_policy import BaselinePolicyConfig
from meteora_learner.capital_sizing import CapitalSizingConfig
from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.liquidity_math import Q64
from meteora_learner.phase3_plan import build_phase3_research_plan
from meteora_learner.phase_promotion import persist_phase2_promotion
from meteora_learner.pool_safety import PoolSafetyConfig
from meteora_learner.storage import Storage
from meteora_learner.strategy import StrategyType
from types import SimpleNamespace


def seed(storage):
    storage.save_pool_snapshot(
        {
            "address": "pool",
            "name": "X-Y",
            "tvl": 100000,
            "volume_24h": 20000,
            "fees_24h": 100,
            "current_price": 1.0,
            "bin_step": 25,
            "active_bin_id": 0,
            "token_x": {"symbol": "X", "decimals": 6},
            "token_y": {"symbol": "Y", "decimals": 6},
            "dynamic_fee_pct": 0.5,
            "is_blacklisted": False,
            "created_at": 1790035200,
        },
        observed_at="2026-09-23T00:05:00+00:00",
    )
    for observed in (
        "2026-09-23T00:00:00+00:00",
        "2026-09-23T00:05:00+00:00",
    ):
        storage.save_chain_pool_snapshot(
            {
                "pool_address": "pool",
                "active_bin_id": 0,
                "bin_step": 25,
                "token_x_mint": "x",
                "token_y_mint": "y",
                "token_x_program": STANDARD_SPL_TOKEN_PROGRAM,
                "token_y_program": STANDARD_SPL_TOKEN_PROGRAM,
                "base_fee_rate": "0",
                "variable_fee_rate": "0",
                "total_fee_rate": "0",
                "deposit_total_fee_rate": "0",
                "protocol_share_bps": 0,
                "collect_fee_mode": 0,
                "bin_arrays": [
                    {
                        "address": "array",
                        "index": 0,
                        "lower_bin_id": 0,
                        "upper_bin_id": 0,
                        "bins": [
                            {
                                "bin_id": 0,
                                "price": str(Q64),
                                "amount_x": "1000",
                                "amount_y": "1000",
                                "liquidity_supply": str(2000 * Q64),
                                "fee_amount_x_per_token_stored": "0",
                                "fee_amount_y_per_token_stored": "0",
                            }
                        ],
                    }
                ],
            },
            observed_at=observed,
        )


def configs():
    return (
        PoolSafetyConfig(
            min_tvl_usd=0,
            min_volume_24h_usd=0,
            min_pool_age_hours=0,
            min_chain_observations=2,
            max_dynamic_fee_pct=1.0,
        ),
        BaselinePolicyConfig(
            min_range_survival_ratio=0.0,
            require_fee_cost_recovery=False,
            estimated_network_cost_y_atomic=0,
        ),
        CapitalSizingConfig(
            max_position_bps=1000,
            max_total_deployed_bps=7000,
            min_cash_reserve_bps=3000,
        ),
    )


def test_phase3_plan_builds_research_entry_but_keeps_phase2_block(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    safety, baseline, sizing = configs()

    plan = build_phase3_research_plan(
        str(storage.path),
        pool_address="pool",
        amount_x=0,
        amount_y=10,
        requested_quote=100,
        account_equity_quote=1000,
        cash_quote=1000,
        current_deployed_quote=0,
        portfolio_drawdown_bps=0,
        phase2_gate=None,
        safety_config=safety,
        baseline_config=baseline,
        sizing_config=sizing,
        observation_limit=2,
        half_widths=(0,),
        strategies=(StrategyType.SPOT,),
    )

    assert plan.status == "RESEARCH_READY_PHASE2_BLOCKED"
    assert plan.policy_authorized is False
    assert plan.live_execution_ready is False
    assert plan.baseline is not None
    assert plan.baseline.research_proposal is not None


def test_phase3_plan_rejects_scan_notional_above_sizing_cap(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    safety, baseline, sizing = configs()

    plan = build_phase3_research_plan(
        str(storage.path),
        pool_address="pool",
        amount_x=0,
        amount_y=10,
        requested_quote=500,
        account_equity_quote=1000,
        cash_quote=1000,
        current_deployed_quote=0,
        portfolio_drawdown_bps=0,
        phase2_gate=None,
        safety_config=safety,
        baseline_config=baseline,
        sizing_config=sizing,
        observation_limit=2,
        half_widths=(0,),
        strategies=(StrategyType.SPOT,),
    )

    assert plan.status == "ENTRY_REJECTED"
    assert plan.entry_gate is not None
    assert plan.entry_gate.sizing_ready is False
    assert any("rescaled" in reason for reason in plan.reasons)



def test_phase3_plan_uses_persisted_phase2_promotion(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    safety, baseline, sizing = configs()
    persist_phase2_promotion(
        storage,
        report=SimpleNamespace(
            promotion_ready=True,
            to_record=lambda: {"promotion_ready": True},
        ),
    )

    plan = build_phase3_research_plan(
        str(storage.path),
        pool_address="pool",
        amount_x=0,
        amount_y=10,
        requested_quote=100,
        account_equity_quote=1000,
        cash_quote=1000,
        current_deployed_quote=0,
        portfolio_drawdown_bps=0,
        phase2_gate=None,
        safety_config=safety,
        baseline_config=baseline,
        sizing_config=sizing,
        observation_limit=2,
        half_widths=(0,),
        strategies=(StrategyType.SPOT,),
    )

    assert plan.status == "POLICY_AUTHORIZED"
    assert plan.policy_authorized is True
    assert plan.entry_gate is not None
    assert plan.entry_gate.phase2_ready is True
    assert plan.entry_gate.proposal is not None
