import json
import sys

from meteora_learner import cli
import sqlite3

import pytest

from meteora_learner.phase8_transition_history import (
    audit_phase8_transition_history,
    list_phase8_cycle_status_history,
    list_phase8_model_status_history,
)
from meteora_learner.storage import Storage


def register_model(storage, model_id="model-a"):
    storage.register_model(
        model_id=model_id,
        model_family="ML_V1_HIST_GRADIENT_BOOSTING",
        feature_version="ML_ACTION_FEATURES_V1",
        dataset_version="dataset-v1",
        metrics={},
    )


def insert_cycle(storage, cycle_id="cycle-a"):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO continuous_learning_cycles(
                cycle_id,
                created_at,
                updated_at,
                status,
                active_key,
                champion_model_id,
                champion_dataset_version,
                champion_evidence_watermark,
                plan_evidence_id,
                plan_as_of,
                target_dataset_version,
                challenger_model_id,
                plan_json,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id,
                "2026-09-24T10:00:00+00:00",
                "2026-09-24T10:00:00+00:00",
                "PLANNED",
                "ACTIVE",
                "champion-a",
                "dataset-v1",
                "2026-09-23T00:00:00+00:00",
                1,
                "2026-09-24T10:00:00+00:00",
                "dataset-v2",
                None,
                "{}",
                None,
            ),
        )


def test_phase8_model_status_history_tracks_insert_and_transition(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    register_model(storage)

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE model_registry
            SET status = 'OFFLINE_QUALIFIED'
            WHERE model_id = 'model-a'
            """
        )

    history = list_phase8_model_status_history(
        storage,
        model_id="model-a",
    )

    assert len(history) == 2
    assert history[0].old_status == "OFFLINE_CANDIDATE"
    assert history[0].new_status == "OFFLINE_QUALIFIED"
    assert history[1].old_status is None
    assert history[1].new_status == "OFFLINE_CANDIDATE"
    assert history[0].dataset_version == "dataset-v1"


def test_phase8_cycle_history_tracks_challenger_and_status_transition(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    insert_cycle(storage)

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE continuous_learning_cycles
            SET status = 'CHALLENGER_REGISTERED',
                challenger_model_id = 'challenger-a'
            WHERE cycle_id = 'cycle-a'
            """
        )

    history = list_phase8_cycle_status_history(
        storage,
        cycle_id="cycle-a",
    )

    assert len(history) == 2
    assert history[0].old_status == "PLANNED"
    assert history[0].new_status == "CHALLENGER_REGISTERED"
    assert history[0].old_challenger_model_id is None
    assert history[0].new_challenger_model_id == "challenger-a"
    assert history[1].old_status is None
    assert history[1].new_status == "PLANNED"
    assert history[1].new_active_key == "ACTIVE"


def test_phase8_transition_history_is_immutable(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    register_model(storage)
    insert_cycle(storage)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with storage.connect() as conn:
            conn.execute(
                """
                UPDATE phase8_model_status_history
                SET new_status = 'REJECTED'
                WHERE model_id = 'model-a'
                """
            )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with storage.connect() as conn:
            conn.execute(
                """
                DELETE FROM phase8_cycle_status_history
                WHERE cycle_id = 'cycle-a'
                """
            )


def test_phase8_transition_history_backfills_legacy_current_rows(tmp_path):
    path = tmp_path / "pio.db"
    storage = Storage(path)

    with storage.connect() as conn:
        conn.execute(
            "DROP TRIGGER phase8_model_status_history_on_insert"
        )
    register_model(storage, model_id="legacy-model")

    with storage.connect() as conn:
        missing = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM phase8_model_status_history
                WHERE model_id = 'legacy-model'
                """
            ).fetchone()[0]
        )
    assert missing == 0

    reloaded = Storage(path)
    history = list_phase8_model_status_history(
        reloaded,
        model_id="legacy-model",
    )

    assert len(history) == 1
    assert history[0].old_status is None
    assert history[0].new_status == "OFFLINE_CANDIDATE"


def test_phase8_transition_history_audit_requires_coverage_and_triggers(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    register_model(storage)
    insert_cycle(storage)

    audit = audit_phase8_transition_history(storage)

    assert audit.journal_ready is True
    assert audit.started_at is not None
    assert audit.model_registry_rows == 1
    assert audit.covered_model_rows == 1
    assert audit.cycle_rows == 1
    assert audit.covered_cycle_rows == 1
    assert audit.reasons == ()

    with storage.connect() as conn:
        conn.execute(
            "DROP TRIGGER phase8_cycle_status_history_on_transition"
        )

    broken = audit_phase8_transition_history(storage)

    assert broken.journal_ready is False
    assert any("triggers are missing" in reason for reason in broken.reasons)


def test_phase8_transition_history_validates_limits(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="limit"):
        list_phase8_model_status_history(storage, limit=0)
    with pytest.raises(ValueError, match="limit"):
        list_phase8_cycle_status_history(storage, limit=501)


def test_phase8_transition_history_cli_reports_ready_journal(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    register_model(storage)
    insert_cycle(storage)

    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pio",
            "phase8-transition-history",
            "--require-ready",
        ],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["audit"]["journal_ready"] is True
    assert len(payload["model_events"]) == 1
    assert len(payload["cycle_events"]) == 1
    assert payload["model_events"][0]["model_id"] == "model-a"
    assert payload["cycle_events"][0]["cycle_id"] == "cycle-a"
