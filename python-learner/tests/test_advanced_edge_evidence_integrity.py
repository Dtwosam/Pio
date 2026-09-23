import sqlite3

import pytest

from meteora_learner.storage import Storage


def test_advanced_edge_evidence_is_append_only(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    evidence_id = storage.save_advanced_edge_evidence(
        edge_type="PHASE9_TEST_V1",
        pool_address="pool-a",
        as_of="2026-09-23T12:00:00+00:00",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "value": 1,
        },
    )

    with storage.connect() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                """
                UPDATE advanced_edge_evidence
                SET evidence_json = '{"value":2}'
                WHERE id = ?
                """,
                (evidence_id,),
            )

    with storage.connect() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                "DELETE FROM advanced_edge_evidence WHERE id = ?",
                (evidence_id,),
            )

    latest = storage.latest_advanced_edge_evidence(
        edge_type="PHASE9_TEST_V1",
        pool_address="pool-a",
    )
    assert latest is not None
    assert latest["id"] == evidence_id
    assert latest["evidence"]["value"] == 1


def test_new_advanced_edge_evidence_can_supersede_without_mutation(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    first = storage.save_advanced_edge_evidence(
        edge_type="PHASE9_TEST_V1",
        pool_address="pool-a",
        status="NOT_QUALIFIED",
        qualified=False,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": False,
            "version": 1,
        },
    )
    second = storage.save_advanced_edge_evidence(
        edge_type="PHASE9_TEST_V1",
        pool_address="pool-a",
        status="QUALIFIED_RESEARCH",
        qualified=True,
        evidence={
            "research_only": True,
            "policy_actionable": False,
            "research_qualified": True,
            "version": 2,
        },
    )

    latest = storage.latest_advanced_edge_evidence(
        edge_type="PHASE9_TEST_V1",
        pool_address="pool-a",
    )

    assert second > first
    assert latest is not None
    assert latest["id"] == second
    assert latest["evidence"]["version"] == 2

    with storage.connect() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM advanced_edge_evidence
            WHERE edge_type = 'PHASE9_TEST_V1'
              AND pool_address = 'pool-a'
            """
        ).fetchone()[0]
    assert count == 2
