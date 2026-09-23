from meteora_learner.phase9_storage_integrity import (
    REQUIRED_PHASE9_IMMUTABILITY_TRIGGERS,
    evaluate_phase9_storage_integrity,
)
from meteora_learner.storage import Storage


def test_phase9_storage_integrity_verifies_required_triggers(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    report = evaluate_phase9_storage_integrity(storage)

    assert report.verified is True
    assert report.required_trigger_count == len(
        REQUIRED_PHASE9_IMMUTABILITY_TRIGGERS
    )
    assert report.verified_trigger_count == report.required_trigger_count
    assert report.reasons == ()
    assert all(item.verified for item in report.checks)
    assert any(
        item.trigger_name == "model_live_evidence_no_update"
        for item in report.checks
    )
    assert any(
        item.trigger_name == "advanced_edge_evidence_no_delete"
        for item in report.checks
    )


def test_phase9_storage_integrity_fails_when_trigger_is_missing(tmp_path):
    storage = Storage(tmp_path / "pio.db")
    with storage.connect() as conn:
        conn.execute(
            "DROP TRIGGER model_live_evidence_no_delete"
        )

    report = evaluate_phase9_storage_integrity(storage)

    assert report.verified is False
    check = next(
        item for item in report.checks
        if item.trigger_name == "model_live_evidence_no_delete"
    )
    assert check.present is False
    assert check.verified is False
    assert any(
        "model_live_evidence_no_delete" in reason
        for reason in report.reasons
    )
