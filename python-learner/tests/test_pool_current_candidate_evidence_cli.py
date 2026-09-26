from __future__ import annotations

import json

from meteora_learner import pool_current_candidate_evidence_cli as cli


class _Report:
    def to_record(self):
        return {
            "research_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "pools_seen": 2,
            "pools_with_all_context_groups": 1,
            "candidates": [],
        }


def test_candidate_evidence_cli_forwards_research_arguments(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    captured = {}

    def fake(database_path: str, **kwargs):
        captured["database_path"] = database_path
        captured.update(kwargs)
        return _Report()

    monkeypatch.setattr(
        cli,
        "build_current_pool_candidate_evidence",
        fake,
    )

    code = cli.main(
        [
            "--database",
            str(tmp_path / "pio.db"),
            "--as-of",
            "2026-09-26T12:00:00+00:00",
            "--horizon-rows",
            "4",
            "--min-train-rows",
            "25",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["research_only"] is True
    assert payload["policy_actionable"] is False
    assert payload["execution_wired"] is False
    assert payload["pools_seen"] == 2
    assert captured["database_path"] == str(tmp_path / "pio.db")
    assert captured["as_of"] == "2026-09-26T12:00:00+00:00"
    assert captured["horizon_rows"] == 4
    assert captured["min_train_rows"] == 25
