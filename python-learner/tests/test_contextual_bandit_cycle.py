import json
from pathlib import Path

from meteora_learner.chain_replay import STANDARD_SPL_TOKEN_PROGRAM
from meteora_learner.contextual_bandit import ContextualBanditCriteria
from meteora_learner.contextual_bandit_cycle import (
    evaluate_cycle_contextual_bandit,
    persist_cycle_contextual_bandit,
)
from meteora_learner.continuous_learning import ContinuousLearningCriteria
from meteora_learner.continuous_promotion import CONTINUOUS_PROMOTION_EVIDENCE_TYPE
from meteora_learner.liquidity_math import Q64
from meteora_learner.ml_retraining_dataset import MLRetrainPoolSpec
from meteora_learner.phase8_validation import (
    Phase8PromotionCriteria,
    evaluate_phase8_promotion,
)
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    persist_phase8_promotion,
)
from meteora_learner.retraining_workflow import (
    start_retraining_cycle_with_dataset,
)
from meteora_learner.storage import Storage


NOW = "2026-09-23T00:10:00+00:00"


def save_snapshot(storage, minute, active_id, checkpoint):
    bins = []
    for bin_id in range(-3, 4):
        bins.append(
            {
                "bin_id": bin_id,
                "price": str(Q64),
                "amount_x": "1000",
                "amount_y": "1000",
                "liquidity_supply": str(2000 * Q64),
                "fee_amount_x_per_token_stored": "0",
                "fee_amount_y_per_token_stored": str(checkpoint),
            }
        )
    storage.save_chain_pool_snapshot(
        {
            "pool_address": "pool",
            "active_bin_id": active_id,
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
                    "lower_bin_id": -3,
                    "upper_bin_id": 3,
                    "bins": bins,
                }
            ],
        },
        observed_at=f"2026-09-23T00:{minute:02d}:00+00:00",
    )


def seed(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status,
                train_end, validation_end, metrics_json
            ) VALUES (
                'champion', '2026-08-01T00:00:00+00:00',
                '2026-08-01T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION',
                '2026-09-22T23:00:00+00:00',
                '2026-09-22T23:30:00+00:00', '{}'
            )
            """
        )
    storage.save_phase_promotion_evidence(
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles(
                cycle_id, created_at, updated_at, status,
                active_key, champion_model_id,
                champion_dataset_version,
                champion_evidence_watermark,
                plan_evidence_id, plan_as_of,
                target_dataset_version,
                challenger_model_id, plan_json
            ) VALUES (
                'phase8-cycle',
                '2026-09-21T00:00:00+00:00',
                '2026-09-22T00:00:00+00:00',
                'COMPLETED', NULL,
                'old-champion', 'dataset-v0',
                '2026-09-20T00:00:00+00:00',
                1, '2026-09-21T00:00:00+00:00',
                'dataset-v1', 'champion', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_learning_labels(
                position_address, decision_id, pool_address,
                model_version, strategy,
                min_bin_id, max_bin_id, range_width_bins,
                proposed_capital_quote,
                expected_net_return_pct, expected_downside_pct,
                realized_pnl_quote, realized_return_bps,
                prediction_error_bps, target_positive_return,
                quote_unit, opened_signature, closed_decision_id,
                created_at, raw_json
            ) VALUES (
                'phase8-position', 'phase8-decision',
                'pool', 'champion', 'SPOT',
                -1, 1, 3, '10', '1', '1',
                '1', 100, 0, 1, 'USD',
                'phase8-signature', 'phase8-close',
                '2026-09-23T00:00:00+00:00', '{}'
            )
            """
        )
    storage.save_model_live_evidence(
        model_id="champion",
        evidence_type=CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
        status="CHAMPION",
        evidence={
            "cycle_id": "phase8-cycle",
            "predecessor_model_id": "old-champion",
            "validation": {"qualified": True},
        },
    )
    phase8 = evaluate_phase8_promotion(
        storage,
        criteria=Phase8PromotionCriteria(
            min_completed_cycles=1,
            min_live_labels=1,
            min_live_pools=1,
            max_realized_drawdown_bps=10_000,
            max_single_loss_bps=10_000,
            min_win_rate=0.0,
            min_mean_return_bps=-10_000,
            max_mean_abs_prediction_error_bps=10_000,
        ),
    )
    assert phase8.promotion_ready is True
    persist_phase8_promotion(storage, report=phase8)
    save_snapshot(storage, 0, 0, 0)
    save_snapshot(storage, 5, 0, Q64)
    save_snapshot(storage, 10, 1, 2 * Q64)
    save_snapshot(storage, 15, 1, 3 * Q64)


def build_cycle(storage, output):
    return start_retraining_cycle_with_dataset(
        storage,
        pools=(
            MLRetrainPoolSpec(
                pool_address="pool",
                amount_x=0,
                amount_y=10,
                network_cost_y_atomic=0,
            ),
        ),
        output_file=output,
        cycle_id="cycle",
        as_of=NOW,
        criteria=ContinuousLearningCriteria(
            min_new_chain_observations=3,
            min_new_chain_pools=1,
            min_new_live_labels=0,
            max_champion_age_days=1,
        ),
        lookback_observations=2,
        forward_observations=2,
        half_widths=(1, 2),
        center_offsets=(0,),
        max_share_bps=500,
    )


def criteria():
    return ContextualBanditCriteria(
        warmup_decisions_per_context=1,
        exploration_bonus_bps=0,
        min_decisions=1,
        min_pools=1,
        min_selected_arms=1,
        min_mean_uplift_vs_baseline_bps=-10_000,
        max_mean_regret_vs_oracle_bps=10_000,
    )


def test_cycle_bound_bandit_uses_retraining_dataset_lineage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    output = tmp_path / "retrain.csv"
    cycle = build_cycle(storage, output)

    result = evaluate_cycle_contextual_bandit(
        storage,
        cycle_id=cycle.cycle.cycle_id,
        criteria=criteria(),
    )

    assert result.lineage.cycle_id == "cycle"
    assert result.lineage.dataset_version == (
        cycle.dataset.dataset_version
    )
    assert result.lineage.dataset_sha256 == (
        cycle.dataset.dataset_sha256
    )
    assert result.lineage.cutoff == NOW
    assert result.report.research_only is True
    assert result.report.policy_actionable is False

    evidence_id = persist_cycle_contextual_bandit(
        storage,
        result=result,
    )
    assert evidence_id > 0
    latest = storage.latest_advanced_edge_evidence(
        edge_type="PHASE9_CONTEXTUAL_BANDIT_V1",
        pool_address="__CONTEXTUAL_BANDIT__",
    )
    assert latest is not None
    assert latest["evidence"]["dataset_lineage"]["cycle_id"] == "cycle"
    assert latest["evidence"]["dataset_lineage"]["dataset_sha256"] == (
        cycle.dataset.dataset_sha256
    )


def test_cycle_bound_bandit_rejects_tampered_dataset(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    output = tmp_path / "retrain.csv"
    build_cycle(storage, output)

    output.write_text(
        output.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    try:
        evaluate_cycle_contextual_bandit(
            storage,
            cycle_id="cycle",
            criteria=criteria(),
        )
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("expected tampered dataset refusal")


def test_cycle_bound_bandit_requires_dataset_file(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    output = tmp_path / "retrain.csv"
    build_cycle(storage, output)
    Path(output).unlink()

    try:
        evaluate_cycle_contextual_bandit(
            storage,
            cycle_id="cycle",
            criteria=criteria(),
        )
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("expected missing dataset refusal")



def test_cycle_bound_bandit_rejects_symlinked_dataset_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    output = tmp_path / "retrain.csv"
    build_cycle(storage, output)

    link = tmp_path / "retrain-link.csv"
    link.symlink_to(output)

    latest = storage.latest_model_live_evidence(
        "champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
    )
    assert latest is not None
    payload = dict(latest["evidence"])
    payload["output_file"] = str(link.absolute())
    forged_id = storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence=payload,
    )
    assert forged_id > int(latest["id"])

    try:
        evaluate_cycle_contextual_bandit(
            storage,
            cycle_id="cycle",
            criteria=criteria(),
        )
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("expected symlinked dataset refusal")
