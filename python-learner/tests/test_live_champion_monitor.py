import pytest

from meteora_learner.live_champion_monitor import (
    LIVE_MONITOR_EVIDENCE_TYPE,
    LIVE_ROLLBACK_EVIDENCE_TYPE,
    LiveChampionCriteria,
    evaluate_live_champion,
    persist_live_champion_report,
    rollback_live_champion,
)
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
)
from meteora_learner.storage import Storage


def seed_champion(storage, model_id="model-a"):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at,
                model_family, feature_version, dataset_version,
                status, metrics_json
            ) VALUES (
                ?, '2026-09-23T10:00:00+00:00',
                '2026-09-23T10:00:00+00:00',
                'TEST', 'TEST', 'dataset', 'CHAMPION', '{}'
            )
            """,
            (model_id,),
        )


def seed_phase7(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence={
            "promotion_ready": True,
            "phase6_promoted": True,
            "ledger_audit": {"clean": True},
            "confirmed_receipts": 6,
            "failed_receipts": 0,
            "closed_positions": 3,
            "open_positions": 0,
            "distinct_closed_pools": 2,
            "valued_closed_positions": 3,
            "labeled_closed_positions": 3,
            "criteria": {
                "min_closed_positions": 3,
                "min_distinct_pools": 2,
                "min_confirmed_receipts": 6,
                "max_failed_receipts": 0,
                "max_open_positions_at_validation": 0,
            },
        },
    )


def add_labels(storage, returns, model_id="model-a"):
    with storage.connect() as conn:
        for index, realized_return_bps in enumerate(returns):
            position = f"position-{index}"
            decision = f"decision-{index}"
            expected_bps = 100
            error = realized_return_bps - expected_bps
            conn.execute(
                """
                INSERT INTO live_learning_labels(
                    position_address, decision_id, pool_address,
                    model_version, strategy,
                    min_bin_id, max_bin_id, range_width_bins,
                    proposed_capital_quote,
                    expected_net_return_pct,
                    expected_downside_pct,
                    realized_pnl_quote, realized_return_bps,
                    prediction_error_bps, target_positive_return,
                    quote_unit, opened_signature, closed_decision_id,
                    created_at, raw_json
                ) VALUES (
                    ?, ?, ?, ?, 'SPOT',
                    -1, 1, 3, '10', '1', '1',
                    ?, ?, ?, ?, 'USD', ?, ?,
                    ?, '{}'
                )
                """,
                (
                    position,
                    decision,
                    f"pool-{index % 2}",
                    model_id,
                    str(realized_return_bps / 100),
                    realized_return_bps,
                    error,
                    int(realized_return_bps > 0),
                    f"signature-{index}",
                    f"close-{index}",
                    f"2026-09-23T{10 + index:02d}:00:00+00:00",
                ),
            )


def criteria(min_labels=10):
    return LiveChampionCriteria(
        min_live_labels=min_labels,
        max_realized_drawdown_bps=2_000,
        max_single_loss_bps=1_500,
        min_win_rate=0.30,
        min_mean_return_bps=-100,
        max_mean_abs_prediction_error_bps=1_500,
    )


def test_live_monitor_waits_for_enough_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    seed_phase7(storage)
    add_labels(storage, [100, -50, 75])

    report = evaluate_live_champion(
        storage,
        criteria=criteria(),
    )

    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.rollback_recommended is False
    assert report.label_count == 3


def test_healthy_live_champion_persists_monitor_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    seed_phase7(storage)
    add_labels(
        storage,
        [100, 80, -50, 120, 60, -40, 90, 70, -30, 110],
    )

    report = evaluate_live_champion(
        storage,
        criteria=criteria(),
    )

    assert report.status == "HEALTHY"
    assert report.rollback_recommended is False
    evidence_id = persist_live_champion_report(
        storage,
        report=report,
    )
    assert evidence_id > 0
    latest = storage.latest_model_live_evidence(
        "model-a",
        evidence_type=LIVE_MONITOR_EVIDENCE_TYPE,
    )
    assert latest is not None
    assert latest["status"] == "HEALTHY"
    assert latest["evidence"]["model_id"] == "model-a"


def test_persisted_breach_can_roll_back_to_deterministic_fallback(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    seed_phase7(storage)
    add_labels(
        storage,
        [-2_000, -500, -300, -200, -100, 50, -100, -50, -25, -25],
    )

    report = evaluate_live_champion(
        storage,
        criteria=criteria(),
    )

    assert report.status == "BREACH"
    assert report.rollback_recommended is True
    persist_live_champion_report(storage, report=report)

    rolled_back = rollback_live_champion(
        storage,
        model_id="model-a",
        notes="live safety rollback",
    )

    assert rolled_back.status == "ROLLED_BACK"
    assert storage.current_model_champion() is None
    latest = storage.latest_model_live_evidence("model-a")
    assert latest is not None
    assert latest["evidence_type"] == LIVE_ROLLBACK_EVIDENCE_TYPE
    assert latest["status"] == "ROLLED_BACK"
    assert latest["evidence"]["fallback_policy"] == "DETERMINISTIC"


def test_rollback_requires_persisted_breach(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    seed_phase7(storage)
    add_labels(
        storage,
        [-2_000, -500, -300, -200, -100, 50, -100, -50, -25, -25],
    )

    with pytest.raises(ValueError, match="persisted BREACH"):
        rollback_live_champion(
            storage,
            model_id="model-a",
        )


def test_monitor_is_blocked_before_phase7_promotion(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    add_labels(storage, [-2_000] * 10)

    report = evaluate_live_champion(
        storage,
        criteria=criteria(),
    )

    assert report.status == "BLOCKED_PHASE7"
    assert report.rollback_recommended is False
