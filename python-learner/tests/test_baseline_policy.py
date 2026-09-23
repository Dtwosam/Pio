from types import SimpleNamespace

from meteora_learner.baseline_policy import (
    BaselinePolicyConfig,
    q64_value_in_y_atomic,
    replay_economics,
    select_deterministic_baseline,
)
from meteora_learner.chain_replay import SmallLPReplayResult
from meteora_learner.chain_scan import ChainCandidateOutcome, ChainScanResult
from meteora_learner.liquidity_math import Q64
from meteora_learner.storage import Storage


TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def replay(*, fee_y, composition_y=0, reward_one=0):
    return SmallLPReplayResult(
        pool_address="pool",
        start_observed_at="2026-09-23T00:00:00+00:00",
        end_observed_at="2026-09-23T00:05:00+00:00",
        observation_count=2,
        start_active_bin_id=0,
        end_active_bin_id=0,
        deposited_x=0,
        deposited_y=100,
        idle_x=0,
        idle_y=0,
        ending_x=0,
        ending_y=100,
        fee_x=0,
        fee_y=fee_y,
        reward_one=reward_one,
        reward_two=0,
        reward_mint_0=None,
        reward_mint_1=None,
        reward_fidelity="test",
        entry_composition_fee_x=0,
        entry_composition_fee_y=composition_y,
        entry_composition_protocol_fee_x=0,
        entry_composition_protocol_fee_y=0,
        entry_composition_lp_fee_x=0,
        entry_composition_lp_fee_y=composition_y,
        deposit_total_fee_rate=0,
        protocol_share_bps=0,
        max_share_bps=500,
        max_observed_share_bps=100,
        projected_share_fidelity="test",
        replay_fidelity="test",
        intervals=(),
        bins=(),
    )


def seed_prices(storage):
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
                "token_x_program": TOKEN_PROGRAM,
                "token_y_program": TOKEN_PROGRAM,
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


def candidate(*, fee_y, half_width, composition_y=0, reward_one=0):
    return ChainCandidateOutcome(
        strategy="SPOT",
        half_width=half_width,
        center_offset=0,
        min_bin_id=-half_width,
        max_bin_id=half_width,
        status="ACCEPTED",
        rejection_reason=None,
        range_survival_ratio=1.0,
        replay=replay(
            fee_y=fee_y,
            composition_y=composition_y,
            reward_one=reward_one,
        ),
    )


def test_q64_pair_valuation_uses_token_y_atomic_units():
    assert q64_value_in_y_atomic(
        amount_x=3,
        amount_y=4,
        price_q64=2 * Q64,
    ) == 10


def test_baseline_selects_cost_adjusted_trailing_candidate_but_blocks_action(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_prices(storage)
    scan = ChainScanResult(
        pool_address="pool",
        entry_active_bin_id=0,
        decision_active_bin_id=0,
        observation_count=2,
        attempted=2,
        accepted=2,
        rejected=0,
        candidates=(
            candidate(fee_y=5, half_width=1, composition_y=1),
            candidate(fee_y=10, half_width=2, composition_y=1),
        ),
    )
    gate = SimpleNamespace(
        promotion_ready=False,
        reasons=("Phase 2 evidence incomplete",),
    )

    result = select_deterministic_baseline(
        str(storage.path),
        scan=scan,
        phase2_gate=gate,
        config=BaselinePolicyConfig(
            min_range_survival_ratio=1.0,
            estimated_network_cost_y_atomic=1,
        ),
    )

    assert result.research_choice is not None
    assert result.research_choice.half_width == 2
    assert result.research_proposal is not None
    assert result.research_proposal.min_bin_id == -2
    assert result.research_proposal.max_bin_id == 2
    assert result.actionable_proposal is None
    assert result.phase2_ready is False


def test_baseline_becomes_actionable_only_after_phase2_gate(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_prices(storage)
    scan = ChainScanResult(
        pool_address="pool",
        entry_active_bin_id=0,
        decision_active_bin_id=0,
        observation_count=2,
        attempted=1,
        accepted=1,
        rejected=0,
        candidates=(candidate(fee_y=10, half_width=2),),
    )
    gate = SimpleNamespace(promotion_ready=True, reasons=())

    result = select_deterministic_baseline(
        str(storage.path),
        scan=scan,
        phase2_gate=gate,
        config=BaselinePolicyConfig(
            estimated_network_cost_y_atomic=1,
        ),
    )

    assert result.actionable_proposal is not None
    assert result.actionable_proposal.half_width == 2


def test_baseline_rejects_unvalued_reward_income(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_prices(storage)
    scan = ChainScanResult(
        pool_address="pool",
        entry_active_bin_id=0,
        decision_active_bin_id=0,
        observation_count=2,
        attempted=1,
        accepted=1,
        rejected=0,
        candidates=(candidate(fee_y=10, half_width=1, reward_one=1),),
    )
    gate = SimpleNamespace(promotion_ready=True, reasons=())

    result = select_deterministic_baseline(
        str(storage.path),
        scan=scan,
        phase2_gate=gate,
        config=BaselinePolicyConfig(
            estimated_network_cost_y_atomic=1,
        ),
    )

    assert result.candidates_eligible == 0
    assert "reward income is non-zero" in result.assessments[0].rejection_reasons[0]


def test_baseline_requires_network_cost_valuation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_prices(storage)
    scan = ChainScanResult(
        pool_address="pool",
        entry_active_bin_id=0,
        decision_active_bin_id=0,
        observation_count=2,
        attempted=1,
        accepted=1,
        rejected=0,
        candidates=(candidate(fee_y=10, half_width=1),),
    )
    gate = SimpleNamespace(promotion_ready=True, reasons=())

    result = select_deterministic_baseline(
        str(storage.path),
        scan=scan,
        phase2_gate=gate,
    )

    assert result.candidates_eligible == 0
    assert "network cost has no token-Y valuation" in result.assessments[0].rejection_reasons



def test_replay_economics_exposes_entry_normalized_returns():
    item = candidate(fee_y=10, half_width=1)
    economics = replay_economics(
        item,
        entry_price_q64=Q64,
        exit_price_q64=Q64,
        network_cost_y_atomic=0,
    )

    assert economics.initial_value_y_atomic == 100
    assert economics.hold_return_bps == 0
    assert economics.net_return_bps == 1000
    assert economics.excess_vs_hold_initial_bps == 1000
