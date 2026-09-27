from __future__ import annotations

from pathlib import Path

from meteora_learner import manual_market_paper_cycle_cli
from meteora_learner.settings import Settings


class FakeReport:
    def to_record(self):
        return {
            "manual_only": True,
            "paper_only": True,
            "policy_actionable": False,
            "live_authorized": False,
            "status": "COMPLETE",
        }


def test_cli_requires_explicit_virtual_capital_and_network_cost(
    tmp_path: Path,
    monkeypatch,
):
    cfg = Settings(database_path=tmp_path / "pio.db")
    seen = {}

    def cycle(storage, **kwargs):
        seen["database_path"] = storage.path
        seen.update(kwargs)
        return FakeReport()

    monkeypatch.setattr(
        manual_market_paper_cycle_cli,
        "run_manual_market_paper_cycle",
        cycle,
    )
    result = manual_market_paper_cycle_cli.run(
        [
            "--account", "paper",
            "--run-id", "r1",
            "--capital-per-position", "100",
            "--network-cost-quote", "1",
            "--max-new-positions", "2",
        ],
        settings=cfg,
    )

    assert seen["database_path"] == cfg.database_path
    assert seen["account_id"] == "paper"
    assert seen["per_position_capital_quote"] == 100.0
    assert seen["network_cost_quote"] == 1.0
    assert seen["max_new_positions"] == 2
    assert result["status"] == "COMPLETE"
