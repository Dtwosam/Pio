from meteora_learner.paper_account import (
    create_paper_account,
    mark_paper_position,
    open_paper_position,
    paper_account_snapshot,
)
from meteora_learner.paper_runner import (
    PaperBatchObservation,
    run_paper_observation_batch,
)
from meteora_learner.position_policy import PositionManagementConfig
from meteora_learner.storage import Storage


def seed(storage):
    create_paper_account(
        storage,
        account_id="paper",
        starting_cash_quote=1000,
    )
    for position in ("a", "b"):
        open_paper_position(
            storage,
            event_key=f"enter-{position}",
            account_id="paper",
            position_id=position,
            pool_address="pool",
            policy_source="DETERMINISTIC",
            strategy="SPOT",
            min_bin_id=-1,
            max_bin_id=1,
            capital_quote=100,
        )


def observations():
    return (
        PaperBatchObservation(
            position_id="a",
            active_bin_id=0,
            holding_observations=1,
            mark_quote=102,
            fee_delta_quote=1,
        ),
        PaperBatchObservation(
            position_id="b",
            active_bin_id=0,
            holding_observations=1,
            mark_quote=94,
            estimated_exit_cost_quote=1,
        ),
    )


def test_batch_is_idempotent_across_repeated_run_id(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    config = PositionManagementConfig(stop_loss_bps=500)

    first = run_paper_observation_batch(
        storage,
        run_id="run-1",
        observed_at="2026-09-23T09:00:00+00:00",
        observations=observations(),
        config=config,
    )
    assert first.status == "COMPLETE"
    assert first.items_applied == 2
    assert first.reused_existing_run is False

    account_after_first = paper_account_snapshot(
        storage,
        account_id="paper",
    )
    second = run_paper_observation_batch(
        storage,
        run_id="run-1",
        observed_at="2026-09-23T09:00:00+00:00",
        observations=observations(),
        config=config,
    )
    account_after_second = paper_account_snapshot(
        storage,
        account_id="paper",
    )

    assert second.status == "COMPLETE"
    assert second.reused_existing_run is True
    assert account_after_second == account_after_first


def test_batch_persists_observation_timestamp(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)

    run_paper_observation_batch(
        storage,
        run_id="run-time",
        observed_at="2026-09-23T09:05:00+00:00",
        observations=(
            PaperBatchObservation(
                position_id="a",
                active_bin_id=0,
                holding_observations=1,
                mark_quote=100,
            ),
        ),
        config=PositionManagementConfig(stop_loss_bps=5000),
    )

    with storage.connect() as conn:
        event_time = conn.execute(
            """
            SELECT event_time
            FROM paper_events
            WHERE event_key = 'paper-run:run-time:a:mark'
            """
        ).fetchone()[0]
    assert event_time == "2026-09-23T09:05:00+00:00"


def test_batch_recovers_from_mark_written_before_item_checkpoint(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)
    item = PaperBatchObservation(
        position_id="a",
        active_bin_id=0,
        holding_observations=1,
        mark_quote=101,
        fee_delta_quote=2,
    )
    config = PositionManagementConfig(stop_loss_bps=5000)
    payload = __import__(
        "meteora_learner.paper_runner",
        fromlist=["_payload"],
    )._payload(item, config)

    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_runs(
                run_id, observed_at, started_at, status, items_total
            ) VALUES ('recover', '2026-09-23T09:10:00+00:00',
                      '2026-09-23T09:10:00+00:00', 'RUNNING', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO paper_run_items(
                run_id, position_id, event_key_prefix, status, input_json
            ) VALUES ('recover', 'a', 'paper-run:recover:a', 'PENDING', ?)
            """,
            (payload,),
        )

    mark_paper_position(
        storage,
        event_key="paper-run:recover:a:mark",
        position_id="a",
        mark_quote=101,
        fee_delta_quote=2,
        event_time="2026-09-23T09:10:00+00:00",
    )

    result = run_paper_observation_batch(
        storage,
        run_id="recover",
        observed_at="2026-09-23T09:10:00+00:00",
        observations=(item,),
        config=config,
    )

    assert result.status == "COMPLETE"
    assert result.items[0].recovered is True
    with storage.connect() as conn:
        fees = conn.execute(
            "SELECT fee_income_quote FROM paper_positions WHERE position_id = 'a'"
        ).fetchone()[0]
    assert float(fees) == 2.0


def test_batch_rejects_same_run_id_with_different_inputs(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed(storage)

    run_paper_observation_batch(
        storage,
        run_id="same",
        observed_at="2026-09-23T09:15:00+00:00",
        observations=(
            PaperBatchObservation(
                position_id="a",
                active_bin_id=0,
                holding_observations=1,
                mark_quote=100,
            ),
        ),
    )

    import pytest

    with pytest.raises(ValueError, match="inputs do not match"):
        run_paper_observation_batch(
            storage,
            run_id="same",
            observed_at="2026-09-23T09:15:00+00:00",
            observations=(
                PaperBatchObservation(
                    position_id="a",
                    active_bin_id=0,
                    holding_observations=1,
                    mark_quote=101,
                ),
            ),
        )
