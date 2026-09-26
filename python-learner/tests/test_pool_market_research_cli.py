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
