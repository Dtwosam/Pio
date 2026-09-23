from meteora_learner.storage import Storage


def test_advanced_edge_evidence_api_is_append_only(tmp_path):
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
        rows = conn.execute(
            """
            SELECT id, evidence_json
            FROM advanced_edge_evidence
            WHERE edge_type = 'PHASE9_TEST_V1'
              AND pool_address = 'pool-a'
            ORDER BY id ASC
            """
        ).fetchall()

    assert [int(row[0]) for row in rows] == [first, second]
    assert '"version":1' in str(rows[0][1])
    assert '"version":2' in str(rows[1][1])
