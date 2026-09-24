import json
import sys

from meteora_learner import cli
from datetime import datetime, timedelta, timezone

import pytest

from meteora_learner.phase8_historical_state import (
    build_phase8_historical_state_snapshot,
)
from meteora_learner.phase8_transition_history import (
    audit_phase8_transition_history,
)
from meteora_learner.phase_promotion import (
    PHASE7,
    PHASE7_EVIDENCE_TYPE,
)
from meteora_learner.storage import Storage


def text(value):
    return value.astimezone(timezone.utc).isoformat()


def journal_base(storage):
    audit = audit_phase8_transition_history(storage)
    assert audit.started_at is not None
    started = datetime.fromisoformat(
        audit.started_at.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    return started + timedelta(minutes=1)


def insert_model(storage, *, model_id, created_at, status):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_registry(
                model_id,
                created_at,
                updated_at,
                model_family,
                feature_version,
                dataset_version,
                status,
                metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_id,
                text(created_at),
                text(created_at),
                "ML_V1_HIST_GRADIENT_BOOSTING",
                "ML_ACTION_FEATURES_V1",
                "dataset-v1",
                status,
                "{}",
            ),
        )


def insert_cycle(storage, *, created_at):
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
                "cycle-a",
                text(created_at),
                text(created_at),
                "PLANNED",
                "ACTIVE",
                "champion-a",
                "dataset-v1",
                text(created_at - timedelta(days=1)),
                1,
                text(created_at),
                "dataset-v2",
                None,
                "{}",
                None,
            ),
        )


def promote_phase7_history(storage, *, promoted_at):
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO phase_promotion_evidence_history(
                phase_name,
                promoted_at,
                evidence_type,
                qualified,
                evidence_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                PHASE7,
                text(promoted_at),
                PHASE7_EVIDENCE_TYPE,
                1,
                "{}",
            ),
        )


def test_phase8_historical_snapshot_rejects_prejournal_cutoff(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    audit = audit_phase8_transition_history(storage)
    assert audit.started_at is not None
    started = datetime.fromisoformat(
        audit.started_at.replace("Z", "+00:00")
    ).astimezone(timezone.utc)

    with pytest.raises(ValueError, match="predates transition journal"):
        build_phase8_historical_state_snapshot(
            storage,
            as_of=text(started - timedelta(seconds=1)),
        )


def test_phase8_historical_snapshot_reconstructs_model_cycle_and_phase7(
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    base = journal_base(storage)
    promote_phase7_history(storage, promoted_at=base)
    insert_model(
        storage,
        model_id="champion-a",
        created_at=base,
        status="OFFLINE_QUALIFIED",
    )
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE model_registry
            SET status = 'CHAMPION', updated_at = ?
            WHERE model_id = 'champion-a'
            """,
            (text(base + timedelta(minutes=1)),),
        )
    insert_model(
        storage,
        model_id="challenger-a",
        created_at=base + timedelta(minutes=2),
        status="OFFLINE_CANDIDATE",
    )
    insert_cycle(
        storage,
        created_at=base + timedelta(minutes=2),
    )
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE continuous_learning_cycles
            SET status = 'CHALLENGER_REGISTERED',
                challenger_model_id = 'challenger-a',
                updated_at = ?
            WHERE cycle_id = 'cycle-a'
            """,
            (text(base + timedelta(minutes=3)),),
        )

    snapshot = build_phase8_historical_state_snapshot(
        storage,
        as_of=text(base + timedelta(minutes=4)),
    )

    assert snapshot.consistent is True
    assert snapshot.phase7_promoted is True
    assert snapshot.champion_model_id == "champion-a"
    assert snapshot.active_challenger_model_ids == ("challenger-a",)
    assert snapshot.active_cycle_id == "cycle-a"
    assert snapshot.completed_cycle_ids == ()
    assert snapshot.reasons == ()


def test_phase8_historical_snapshot_tracks_cycle_completion(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    base = journal_base(storage)
    insert_cycle(storage, created_at=base)

    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE continuous_learning_cycles
            SET status = 'COMPLETED',
                active_key = NULL,
                updated_at = ?
            WHERE cycle_id = 'cycle-a'
            """,
            (text(base + timedelta(minutes=2)),),
        )

    before = build_phase8_historical_state_snapshot(
        storage,
        as_of=text(base + timedelta(minutes=1)),
    )
    after = build_phase8_historical_state_snapshot(
        storage,
        as_of=text(base + timedelta(minutes=3)),
    )

    assert before.active_cycle_id == "cycle-a"
    assert before.completed_cycle_ids == ()
    assert after.active_cycle_id is None
    assert after.completed_cycle_ids == ("cycle-a",)


def test_phase8_historical_snapshot_detects_multiple_champions(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    base = journal_base(storage)
    insert_model(
        storage,
        model_id="model-a",
        created_at=base,
        status="CHAMPION",
    )
    with storage.connect() as conn:
        conn.execute(
            """
            UPDATE model_registry
            SET status = 'ROLLED_BACK', updated_at = ?
            WHERE model_id = 'model-a'
            """,
            (text(base + timedelta(minutes=10)),),
        )
    insert_model(
        storage,
        model_id="model-b",
        created_at=base + timedelta(minutes=5),
        status="CHAMPION",
    )

    snapshot = build_phase8_historical_state_snapshot(
        storage,
        as_of=text(base + timedelta(minutes=6)),
    )

    assert snapshot.consistent is False
    assert snapshot.champion_model_id is None
    assert any("multiple CHAMPION" in reason for reason in snapshot.reasons)


def test_phase8_historical_snapshot_requires_timezone(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    with pytest.raises(ValueError, match="timezone-aware"):
        build_phase8_historical_state_snapshot(
            storage,
            as_of="2026-09-24T12:00:00",
        )


def test_phase8_transition_snapshot_cli_reports_consistent_state(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    audit = audit_phase8_transition_history(storage)
    assert audit.started_at is not None
    started = datetime.fromisoformat(
        audit.started_at.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    cutoff = text(started + timedelta(seconds=1))

    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pio",
            "phase8-transition-snapshot",
            "--as-of",
            cutoff,
            "--require-consistent",
        ],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["consistent"] is True
    assert payload["as_of"] == cutoff
    assert payload["journal_started_at"] == audit.started_at
