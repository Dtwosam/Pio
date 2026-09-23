from meteora_learner.continuous_learning import ContinuousLearningCriteria
from meteora_learner.phase_promotion import PHASE7, PHASE7_EVIDENCE_TYPE
from meteora_learner.retraining_cycle import (
    active_retraining_cycle,
    attach_retraining_challenger,
    start_retraining_cycle,
    sync_retraining_cycle,
    cancel_retraining_cycle,
)
from meteora_learner.storage import Storage


NOW = "2026-09-23T15:00:00+00:00"


def seed_ready(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status,
                train_end, validation_end, metrics_json
            ) VALUES (
                'champion', '2026-08-01T00:00:00+00:00',
                '2026-09-01T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION',
                '2026-09-05T00:00:00+00:00',
                '2026-09-10T00:00:00+00:00', '{}'
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
        for observed_at, pool in (
            ("2026-09-11T00:00:00+00:00", "pool-a"),
            ("2026-09-12T00:00:00+00:00", "pool-b"),
            ("2026-09-13T00:00:00+00:00", "pool-a"),
        ):
            conn.execute(
                """
                INSERT INTO chain_pool_snapshots(
                    observed_at, pool_address, active_bin_id, bin_step,
                    token_x_mint, token_y_mint, raw_json
                ) VALUES (?, ?, 0, 25, 'x', 'y', '{}')
                """,
                (observed_at, pool),
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
                'position', 'decision', 'pool-a', 'champion', 'SPOT',
                -1, 1, 3, '10', '1', '1',
                '1', 100, 0, 1, 'USD', 'signature', 'close',
                '2026-09-20T00:00:00+00:00', '{}'
            )
            """
        )


def criteria():
    return ContinuousLearningCriteria(
        min_new_chain_observations=3,
        min_new_chain_pools=2,
        min_new_live_labels=1,
        max_champion_age_days=30,
    )


def register_candidate(
    storage,
    *,
    model_id="challenger",
    dataset_version="dataset-v2",
    validation_end="2026-09-22T00:00:00+00:00",
):
    storage.register_model(
        model_id=model_id,
        model_family="TEST",
        feature_version="TEST",
        dataset_version=dataset_version,
        metrics={},
        train_start="2026-09-01T00:00:00+00:00",
        train_end="2026-09-18T00:00:00+00:00",
        validation_start="2026-09-19T00:00:00+00:00",
        validation_end=validation_end,
        train_rows=100,
        validation_rows=20,
    )


def test_due_plan_starts_single_active_cycle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)

    cycle = start_retraining_cycle(
        storage,
        cycle_id="cycle-1",
        target_dataset_version="dataset-v2",
        criteria=criteria(),
        as_of=NOW,
    )

    assert cycle.status == "PLANNED"
    assert cycle.champion_model_id == "champion"
    assert cycle.champion_dataset_version == "dataset-v1"
    assert cycle.champion_evidence_watermark == (
        "2026-09-10T00:00:00+00:00"
    )
    assert cycle.target_dataset_version == "dataset-v2"
    assert cycle.plan["retrain_due"] is True
    assert active_retraining_cycle(storage).cycle_id == "cycle-1"

    try:
        start_retraining_cycle(
            storage,
            cycle_id="cycle-2",
            target_dataset_version="dataset-v3",
            criteria=criteria(),
            as_of=NOW,
        )
    except ValueError as exc:
        assert "active retraining cycle" in str(exc)
    else:
        raise AssertionError("expected overlapping retraining refusal")


def test_matching_candidate_attaches_with_frozen_lineage(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    start_retraining_cycle(
        storage,
        cycle_id="cycle",
        target_dataset_version="dataset-v2",
        criteria=criteria(),
        as_of=NOW,
    )
    register_candidate(storage)

    cycle = attach_retraining_challenger(
        storage,
        cycle_id="cycle",
        model_id="challenger",
    )

    assert cycle.status == "CHALLENGER_REGISTERED"
    assert cycle.challenger_model_id == "challenger"
    evidence = storage.latest_model_live_evidence(
        "challenger",
        evidence_type="CONTINUOUS_RETRAIN_CYCLE_LINK_V1",
    )
    assert evidence is not None
    assert evidence["status"] == "CHALLENGER_REGISTERED"
    assert evidence["evidence"]["cycle_id"] == "cycle"


def test_candidate_dataset_mismatch_is_rejected(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    start_retraining_cycle(
        storage,
        cycle_id="cycle",
        target_dataset_version="dataset-v2",
        criteria=criteria(),
        as_of=NOW,
    )
    register_candidate(storage, dataset_version="wrong-dataset")

    try:
        attach_retraining_challenger(
            storage,
            cycle_id="cycle",
            model_id="challenger",
        )
    except ValueError as exc:
        assert "dataset_version" in str(exc)
    else:
        raise AssertionError("expected dataset-version refusal")


def test_candidate_cannot_use_data_after_cycle_cutoff(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    start_retraining_cycle(
        storage,
        cycle_id="cycle",
        target_dataset_version="dataset-v2",
        criteria=criteria(),
        as_of=NOW,
    )
    register_candidate(
        storage,
        validation_end="2026-09-24T00:00:00+00:00",
    )

    try:
        attach_retraining_challenger(
            storage,
            cycle_id="cycle",
            model_id="challenger",
        )
    except ValueError as exc:
        assert "cycle cutoff" in str(exc)
    else:
        raise AssertionError("expected no-lookahead cutoff refusal")



def test_rejected_challenger_releases_active_cycle(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    start_retraining_cycle(
        storage,
        cycle_id="cycle",
        target_dataset_version="dataset-v2",
        criteria=criteria(),
        as_of=NOW,
    )
    register_candidate(storage)
    attach_retraining_challenger(
        storage,
        cycle_id="cycle",
        model_id="challenger",
    )
    storage.update_model_status(
        "challenger",
        expected_status="OFFLINE_CANDIDATE",
        new_status="REJECTED",
    )

    cycle = sync_retraining_cycle(
        storage,
        cycle_id="cycle",
    )

    assert cycle.status == "FAILED"
    assert active_retraining_cycle(storage) is None


def test_cancel_refuses_to_orphan_active_candidate(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_ready(storage)
    start_retraining_cycle(
        storage,
        cycle_id="cycle",
        target_dataset_version="dataset-v2",
        criteria=criteria(),
        as_of=NOW,
    )
    register_candidate(storage)
    attach_retraining_challenger(
        storage,
        cycle_id="cycle",
        model_id="challenger",
    )

    try:
        cancel_retraining_cycle(
            storage,
            cycle_id="cycle",
        )
    except ValueError as exc:
        assert "must be rejected" in str(exc)
    else:
        raise AssertionError("expected active challenger cancellation refusal")

    storage.update_model_status(
        "challenger",
        expected_status="OFFLINE_CANDIDATE",
        new_status="REJECTED",
    )
    cycle = cancel_retraining_cycle(
        storage,
        cycle_id="cycle",
        notes="test cancellation",
    )

    assert cycle.status == "CANCELLED"
    assert active_retraining_cycle(storage) is None
