from __future__ import annotations

import json

from meteora_learner.live_outcome_evidence import (
    build_live_outcome_evidence,
)
from meteora_learner.live_outcome_evidence_artifact import (
    load_live_outcome_evidence_report,
    save_live_outcome_evidence_report,
)
from meteora_learner.live_outcome_evidence_cli import main
from meteora_learner.storage import Storage


def test_live_outcome_artifact_round_trip(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    report = build_live_outcome_evidence(str(storage.path))

    saved = save_live_outcome_evidence_report(
        report,
        directory=tmp_path / "reports",
        report_id="live-outcome-001",
    )
    loaded = load_live_outcome_evidence_report(
        report_path=saved.report_path,
        metadata_path=saved.metadata_path,
    )

    assert loaded["samples_seen"] == 0
    assert loaded["research_only"] is True
    assert loaded["policy_actionable"] is False
    assert loaded["execution_wired"] is False


def test_live_outcome_cli_is_read_only_and_can_save_report(
    tmp_path,
    capsys,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    reports = tmp_path / "reports"

    code = main(
        [
            "--database",
            str(storage.path),
            "--output-dir",
            str(reports),
            "--report-id",
            "live-cli-test",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["samples_seen"] == 0
    assert payload["policy_actionable"] is False
    assert payload["execution_wired"] is False
    assert payload["action_cost_evidence"]["samples_seen"] == 0
    assert (
        payload["action_cost_evidence"]["transition_pairs_inferred"]
        is False
    )
    assert payload["artifact"]["report_id"] == "live-cli-test"
    assert (reports / "live-cli-test.json").is_file()
    assert (reports / "live-cli-test.meta.json").is_file()

    with storage.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM live_learning_labels"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM live_position_valuations"
        ).fetchone()[0] == 0
