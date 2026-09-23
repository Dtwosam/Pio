import sqlite3

import pytest

from meteora_learner.storage import Storage


def seed_model(storage):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id, created_at, updated_at, model_family,
                feature_version, dataset_version, status,
                train_end, validation_end, metrics_json
            ) VALUES (
                'champion', '2026-09-01T00:00:00+00:00',
                '2026-09-01T00:00:00+00:00',
                'TEST', 'TEST', 'dataset-v1', 'CHAMPION',
                '2026-09-01T00:00:00+00:00',
                '2026-09-01T00:00:00+00:00', '{}'
            )
            """
        )


def test_model_live_evidence_is_database_immutable(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_model(storage)
    evidence_id = storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={"version": 1},
    )

    with storage.connect() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                """
                UPDATE model_live_evidence
                SET status = 'TAMPERED'
                WHERE id = ?
                """,
                (evidence_id,),
            )

    with storage.connect() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                "DELETE FROM model_live_evidence WHERE id = ?",
                (evidence_id,),
            )

    latest = storage.latest_model_live_evidence(
        "champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
    )
    assert latest is not None
    assert latest["id"] == evidence_id
    assert latest["status"] == "BUILT"
    assert latest["evidence"] == {"version": 1}


def test_model_live_evidence_supersedes_by_append(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    seed_model(storage)
    first = storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={"version": 1},
    )
    second = storage.save_model_live_evidence(
        model_id="champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
        status="BUILT",
        evidence={"version": 2},
    )

    latest = storage.latest_model_live_evidence(
        "champion",
        evidence_type="CONTINUOUS_RETRAIN_DATASET_V1",
    )
    assert second > first
    assert latest is not None
    assert latest["id"] == second
    assert latest["evidence"] == {"version": 2}
