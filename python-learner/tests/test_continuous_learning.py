from meteora_learner.continuous_learning import (
    RETRAIN_PLAN_EVIDENCE_TYPE,
    ContinuousLearningCriteria,
    build_continuous_learning_plan,
    persist_continuous_learning_plan,
)
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
)
from meteora_learner.storage import Storage


NOW = "2026-09-23T15:00:00+00:00"


def seed_champion(storage, *, updated_at="2026-09-01T00:00:00+00:00"):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status,
                train_end, validation_end, metrics_json
            ) VALUES (
                'champion', '2026-08-01T00:00:00+00:00', ?,
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION',
                '2026-09-05T00:00:00+00:00',
                '2026-09-10T00:00:00+00:00', '{}'
            )
            """,
            (updated_at,),
        )


def seed_phase7(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )


def add_chain(storage, observed_at, pool):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO chain_pool_snapshots(
                observed_at, pool_address, active_bin_id, bin_step,
                token_x_mint, token_y_mint, raw_json
            ) VALUES (?, ?, 0, 25, 'x', 'y', '{}')
            """,
            (observed_at, pool),
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
                '2026-09-20T00:00:00+00:00', '{}'
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
    return ContinuousLearningCriteria(
        min_new_chain_observations=3,
        min_new_chain_pools=2,
        min_new_live_labels=1,
        max_champion_age_days=30,
    )


def seed_new_chain(storage):
    add_chain(storage, "2026-09-11T00:00:00+00:00", "pool-a")
    add_chain(storage, "2026-09-12T00:00:00+00:00", "pool-b")
    add_chain(storage, "2026-09-13T00:00:00+00:00", "pool-a")


def test_retraining_is_blocked_before_phase7(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    seed_new_chain(storage)
    add_live_label(storage)

    plan = build_continuous_learning_plan(
        storage,
        criteria=criteria(),
        as_of=NOW,
    )

    assert plan.status == "BLOCKED_PHASE7"
    assert plan.retrain_due is False


def test_retraining_waits_for_enough_new_chain_evidence(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    seed_phase7(storage)
    add_chain(storage, "2026-09-11T00:00:00+00:00", "pool-a")
    add_live_label(storage)

    plan = build_continuous_learning_plan(
        storage,
        criteria=criteria(),
        as_of=NOW,
    )

    assert plan.status == "WAITING_CHAIN_EVIDENCE"
    assert plan.new_chain_observations == 1
    assert plan.new_live_labels == 1


def test_new_chain_plus_live_labels_make_retraining_due(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    seed_phase7(storage)
    seed_new_chain(storage)
    add_live_label(storage)

    plan = build_continuous_learning_plan(
        storage,
        criteria=criteria(),
        as_of=NOW,
    )

    assert plan.status == "RETRAIN_DUE"
    assert plan.retrain_due is True
    assert plan.chain_evidence_ready is True
    assert plan.live_label_trigger is True
    assert plan.champion_evidence_watermark == (
        "2026-09-10T00:00:00+00:00"
    )

    evidence_id = persist_continuous_learning_plan(
        storage,
        plan=plan,
    )
    assert evidence_id > 0
    latest = storage.latest_model_live_evidence(
        "champion",
        evidence_type=RETRAIN_PLAN_EVIDENCE_TYPE,
    )
    assert latest is not None
    assert latest["status"] == "RETRAIN_DUE"


def test_age_can_trigger_retraining_after_chain_evidence_is_ready(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage, updated_at="2026-08-01T00:00:00+00:00")
    seed_phase7(storage)
    seed_new_chain(storage)

    plan = build_continuous_learning_plan(
        storage,
        criteria=criteria(),
        as_of=NOW,
    )

    assert plan.retrain_due is True
    assert plan.live_label_trigger is False
    assert plan.age_trigger is True


def test_active_challenger_blocks_overlapping_retraining_cycle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_champion(storage)
    seed_phase7(storage)
    seed_new_chain(storage)
    add_live_label(storage)
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status, metrics_json
            ) VALUES (
                'challenger', '2026-09-22T00:00:00+00:00',
                '2026-09-22T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v2',
                'OFFLINE_CANDIDATE', '{}'
            )
            """
        )

    plan = build_continuous_learning_plan(
        storage,
        criteria=criteria(),
        as_of=NOW,
    )

    assert plan.status == "CHALLENGER_IN_PROGRESS"
    assert plan.retrain_due is False
    assert plan.active_challenger_model_ids == ("challenger",)
