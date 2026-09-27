from __future__ import annotations

from pathlib import Path

from meteora_learner import market_paper_exploration_cli
from meteora_learner.settings import Settings


class FakeReport:
    def to_record(self):
        return {
            "paper_only": True,
            "policy_actionable": False,
            "live_authorized": False,
            "positions_opened": 1,
        }


def test_cli_requires_explicit_capital_and_cost(tmp_path: Path, monkeypatch):
    cfg = Settings(database_path=tmp_path / "pio.db")
    seen = {}

    def explore(storage, **kwargs):
        seen["database_path"] = storage.path
        seen.update(kwargs)
        return FakeReport()

    monkeypatch.setattr(
        market_paper_exploration_cli,
        "run_market_paper_exploration",
        explore,
    )
    result = market_paper_exploration_cli.run(
        [
            "--account", "paper",
            "--run-id", "r1",
            "--capital-per-position", "100",
            "--network-cost-quote", "1",
            "--max-new-positions", "2",
            "--observed-at", "2026-09-27T08:05:00+00:00",
        ],
        settings=cfg,
    )

    assert seen["database_path"] == cfg.database_path
    assert seen["account_id"] == "paper"
    assert seen["run_id"] == "r1"
    assert seen["per_position_capital_quote"] == 100.0
    assert seen["network_cost_quote"] == 1.0
    assert seen["max_new_positions"] == 2
    assert seen["observed_at"] == "2026-09-27T08:05:00+00:00"
    assert result["positions_opened"] == 1
