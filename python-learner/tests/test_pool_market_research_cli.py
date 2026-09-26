from __future__ import annotations

import json

from meteora_learner.pool_market_research_cli import main


def test_research_cli_reports_collecting_history_on_empty_store(
    tmp_path,
    capsys,
) -> None:
    database = tmp_path / "pio.db"

    code = main(
        [
            "--database",
            str(database),
            "--no-capture",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "COLLECTING_HISTORY"
    assert payload["research_only"] is True
    assert payload["policy_actionable"] is False
    assert payload["execution_wired"] is False
    assert payload["coverage"]["pools_seen"] == 0


def test_research_cli_can_save_versioned_report_artifact(
    tmp_path,
    capsys,
) -> None:
    database = tmp_path / "pio.db"
    artifacts = tmp_path / "reports"

    code = main(
        [
            "--database",
            str(database),
            "--no-capture",
            "--output-dir",
            str(artifacts),
            "--report-id",
            "test-cycle",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["artifact"]["report_id"] == "test-cycle"
    assert (artifacts / "test-cycle.json").is_file()
    assert (artifacts / "test-cycle.meta.json").is_file()
