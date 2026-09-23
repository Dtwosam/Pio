from meteora_learner.continuous_promotion import (
    CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
    ContinuousChampionCriteria,
    evaluate_continuous_champion,
    promote_continuous_challenger,
)
from meteora_learner.paper_account import (
    close_paper_position,
    create_paper_account,
    open_paper_position,
)
from meteora_learner.phase_promotion import PHASE7, PHASE7_EVIDENCE_TYPE
from meteora_learner.storage import Storage


def seed_models_and_cycle(storage):
    storage.save_phase_promotion_evidence(
        phase_name=PHASE7,
        evidence_type=PHASE7_EVIDENCE_TYPE,
        qualified=True,
        evidence={"promotion_ready": True},
    )
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status, metrics_json
            ) VALUES (
                'incumbent', '2026-09-01T00:00:00+00:00',
                '2026-09-10T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status, metrics_json
            ) VALUES (
                'challenger', '2026-09-20T00:00:00+00:00',
                '2026-09-22T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v2',
                'PAPER_CHALLENGER', '{}'
            )
            """
        )
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
                'PAPER_CHALLENGER', 'ACTIVE',
                'incumbent', 'dataset-v1',
                '2026-09-10T00:00:00+00:00',
                1, '2026-09-19T00:00:00+00:00',
                'dataset-v2', 'challenger', '{}'
            )
            """
        )


def add_trade(
    storage,
    *,
    position_id,
    policy_source,
    model_id,
    final_mark,
    minute,
):
    open_paper_position(
        storage,
        event_key=f"enter-{position_id}",
        account_id="paper",
        position_id=position_id,
        pool_address="pool",
        policy_source=policy_source,
        model_id=model_id,
        strategy="SPOT",
        min_bin_id=-1,
        max_bin_id=1,
        capital_quote=100,
        event_time=f"2026-09-23T10:{minute:02d}:00+00:00",
    )
    close_paper_position(
        storage,
        event_key=f"exit-{position_id}",
        position_id=position_id,
        final_mark_quote=final_mark,
        event_time=f"2026-09-23T11:{minute:02d}:00+00:00",
    )


def criteria():
    return ContinuousChampionCriteria(
        min_challenger_closed_trades=1,
        min_incumbent_closed_trades=1,
        min_challenger_return_bps=0,
        min_challenger_win_rate=0.0,
        max_challenger_drawdown_bps=10_000,
        max_single_trade_loss_bps=10_000,
        min_return_uplift_vs_incumbent_bps=0,
    )


def test_continuous_challenger_rotates_champion_atomically(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_models_and_cycle(storage)
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    add_trade(
        storage,
        position_id="incumbent-trade",
        policy_source="ML_CHAMPION",
        model_id="incumbent",
        final_mark=102,
        minute=0,
    )
    add_trade(
        storage,
        position_id="challenger-trade",
        policy_source="ML_CHALLENGER",
        model_id="challenger",
        final_mark=105,
        minute=1,
    )

    validation = evaluate_continuous_champion(
        storage,
        cycle_id="cycle",
        account_id="paper",
        criteria=criteria(),
    )

    assert validation.qualified is True
    assert validation.return_uplift_vs_incumbent_bps == 300

    result = promote_continuous_challenger(
        storage,
        validation=validation,
    )

    assert result["champion"]["model_id"] == "challenger"
    assert storage.current_model_champion()["model_id"] == "challenger"
    assert storage.model_registry_entry("incumbent")["status"] == (
        "ROLLED_BACK"
    )
    with storage.connect() as conn:
        cycle = conn.execute(
            """
            SELECT status, active_key
            FROM continuous_learning_cycles
            WHERE cycle_id = 'cycle'
            """
        ).fetchone()
    assert tuple(cycle) == ("COMPLETED", None)
    evidence = storage.latest_model_live_evidence(
        "challenger",
        evidence_type=CONTINUOUS_PROMOTION_EVIDENCE_TYPE,
    )
    assert evidence is not None
    assert evidence["status"] == "CHAMPION"


def test_continuous_challenger_must_beat_incumbent(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_models_and_cycle(storage)
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    add_trade(
        storage,
        position_id="incumbent-trade",
        policy_source="ML_CHAMPION",
        model_id="incumbent",
        final_mark=105,
        minute=0,
    )
    add_trade(
        storage,
        position_id="challenger-trade",
        policy_source="ML_CHALLENGER",
        model_id="challenger",
        final_mark=102,
        minute=1,
    )

    validation = evaluate_continuous_champion(
        storage,
        cycle_id="cycle",
        account_id="paper",
        criteria=criteria(),
    )

    assert validation.qualified is False
    assert validation.return_uplift_vs_incumbent_bps == -300
    assert any("uplift" in reason for reason in validation.reasons)


def test_champion_change_invalidates_previous_rotation_validation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_models_and_cycle(storage)
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    add_trade(
        storage,
        position_id="incumbent-trade",
        policy_source="ML_CHAMPION",
        model_id="incumbent",
        final_mark=101,
        minute=0,
    )
    add_trade(
        storage,
        position_id="challenger-trade",
        policy_source="ML_CHALLENGER",
        model_id="challenger",
        final_mark=102,
        minute=1,
    )
    validation = evaluate_continuous_champion(
        storage,
        cycle_id="cycle",
        account_id="paper",
        criteria=criteria(),
    )
    assert validation.qualified is True

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE model_registry
            SET status = 'ROLLED_BACK'
            WHERE model_id = 'incumbent'
            """
        )
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status, metrics_json
            ) VALUES (
                'other', '2026-09-23T12:00:00+00:00',
                '2026-09-23T12:00:00+00:00',
                'TEST', 'TEST', 'dataset-other', 'CHAMPION', '{}'
            )
            """
        )

    try:
        promote_continuous_challenger(
            storage,
            validation=validation,
        )
    except ValueError as exc:
        assert "incumbent" in str(exc)
    else:
        raise AssertionError("expected stale champion validation refusal")

    assert storage.model_registry_entry("challenger")["status"] == (
        "PAPER_CHALLENGER"
    )
