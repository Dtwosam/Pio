from meteora_learner.continuous_promotion import (
    CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
)
from meteora_learner.phase8_validation import (
    Phase8PromotionCriteria,
    evaluate_phase8_promotion,
)
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
    PHASE8,
    persist_phase8_promotion,
    phase_promotion_state,
)
from meteora_learner.storage import Storage


def seed_phase7(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def seed_champion(storage, *, with_cycle=True):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status, metrics_json
            ) VALUES (
                'champion', '2026-09-20T00:00:00+00:00',
                '2026-09-22T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v2', 'CHAMPION', '{}'
            )
            """
        )
        if with_cycle:
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
                    'cycle', '2026-09-19T00:00:00+00:00',
                    '2026-09-22T00:00:00+00:00',
                    'COMPLETED', NULL,
                    'old-champion', 'dataset-v1',
                    '2026-09-18T00:00:00+00:00',
                    1, '2026-09-19T00:00:00+00:00',
                    'dataset-v2', 'champion', '{}'
                )
                """
            )
    if with_cycle:
        storage.save_model_live_evidence(
            model_id="champion",
            evidence_type=CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
            status="CHAMPION",
            evidence={
                "cycle_id": "cycle",
                "predecessor_model_id": "old-champion",
                "validation": {"qualified": True},
            },
        )


def add_live_label(storage, index=0):
    with storage.connect() as conn:
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
                ?, ?, 'pool-a', 'champion', 'SPOT',
                -1, 1, 3, '10', '1', '1',
                '1', 100, 0, 1, 'USD', ?, ?,
                '2026-09-23T00:00:00+00:00', '{}'
            )
            """,
            (
                f"position-{index}",
                f"decision-{index}",
                f"signature-{index}",
                f"close-{index}",
            ),
        )


def criteria():
    return Phase8PromotionCriteria(
        min_completed_cycles=1,
        min_live_labels=1,
        min_live_pools=1,
        max_realized_drawdown_bps=10_000,
        max_single_loss_bps=10_000,
        min_win_rate=0.0,
        min_mean_return_bps=-10_000,
        max_mean_abs_prediction_error_bps=10_000,
    )


def test_phase8_ready_evidence_can_be_persisted(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase7(storage)
    seed_champion(storage, with_cycle=True)
    add_live_label(storage)

    report = evaluate_phase8_promotion(
        storage,
        criteria=criteria(),
    )

    assert report.promotion_ready is True
    assert report.champion_cycle_id == "cycle"
    assert report.live_champion.status == "HEALTHY"

    state = persist_phase8_promotion(storage, report=report)
    assert state.phase_name == PHASE8
    assert state.promoted is True
    assert phase_promotion_state(
        storage,
        phase_name=PHASE8,
    ).promoted is True


def test_phase8_rejects_champion_without_continuous_cycle_lineage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase7(storage)
    seed_champion(storage, with_cycle=False)
    add_live_label(storage)

    report = evaluate_phase8_promotion(
        storage,
        criteria=criteria(),
    )

    assert report.promotion_ready is False
    assert report.champion_cycle_id is None
    assert any(
        "continuous cycle" in reason
        for reason in report.reasons
    )


def test_phase8_persistence_refuses_unready_report(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase7(storage)
    seed_champion(storage, with_cycle=False)
    add_live_label(storage)

    report = evaluate_phase8_promotion(
        storage,
        criteria=criteria(),
    )
    assert report.promotion_ready is False

    try:
        persist_phase8_promotion(storage, report=report)
    except ValueError as exc:
        assert "not ready" in str(exc)
    else:
        raise AssertionError("expected Phase 8 promotion refusal")

    assert phase_promotion_state(
        storage,
        phase_name=PHASE8,
    ).promoted is False


def test_phase8_requires_live_pool_diversity(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_phase7(storage)
    seed_champion(storage, with_cycle=True)
    add_live_label(storage, 0)
    add_live_label(storage, 1)

    strict = Phase8PromotionCriteria(
        min_completed_cycles=1,
        min_live_labels=2,
        min_live_pools=2,
        max_realized_drawdown_bps=10_000,
        max_single_loss_bps=10_000,
        min_win_rate=0.0,
        min_mean_return_bps=-10_000,
        max_mean_abs_prediction_error_bps=10_000,
    )
    report = evaluate_phase8_promotion(
        storage,
        criteria=strict,
    )

    assert report.promotion_ready is False
    assert report.live_champion is not None
    assert report.live_champion.status == "INSUFFICIENT_EVIDENCE"
    assert report.live_champion.distinct_pools == 1
