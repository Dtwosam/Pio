from __future__ import annotations

from pathlib import Path

from meteora_learner import market_research_cycle_cli
from meteora_learner.settings import Settings


class FakeReport:
    def to_record(self):
        return {
            "research_only": True,
            "paper_only": True,
            "policy_actionable": False,
            "execution_wired": False,
            "status": "COMPLETE",
        }


def test_cli_forwards_bounded_market_cycle_arguments(tmp_path: Path, monkeypatch):
    cfg = Settings(database_path=tmp_path / "pio.db")
    seen = {}

    def cycle(storage, **kwargs):
        seen["database_path"] = storage.path
        seen.update(kwargs)
        return FakeReport()

    monkeypatch.setattr(
        market_research_cycle_cli,
        "run_market_research_cycle",
        cycle,
    )
    result = market_research_cycle_cli.run(
        [
            "--discovery-page-size", "200",
            "--discovery-max-pages", "3",
            "--seed-batch-limit", "6",
            "--refresh-batch-limit", "2",
            "--minimum-chain-observations", "10",
            "--intake-max-pools", "80",
        ],
        settings=cfg,
    )

    assert seen["database_path"] == cfg.database_path
    assert seen["discovery_page_size"] == 200
    assert seen["discovery_max_pages"] == 3
    assert seen["seed_batch_limit"] == 6
    assert seen["refresh_batch_limit"] == 2
    assert seen["minimum_chain_observations"] == 10
    assert seen["intake_max_pools"] == 80
    assert result["status"] == "COMPLETE"
