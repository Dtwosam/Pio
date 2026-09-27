from __future__ import annotations

from pathlib import Path

from meteora_learner import market_chain_history_cli
from meteora_learner.settings import Settings


class FakeReport:
    def to_record(self):
        return {
            "research_only": True,
            "read_only_capture": True,
            "policy_actionable": False,
            "execution_wired": False,
            "status": "COMPLETE",
        }


def test_cli_forwards_collection_bounds(tmp_path: Path, monkeypatch):
    cfg = Settings(database_path=tmp_path / "pio.db")
    seen = {}

    def cycle(storage, **kwargs):
        seen["database_path"] = storage.path
        seen.update(kwargs)
        return FakeReport()

    monkeypatch.setattr(
        market_chain_history_cli,
        "run_market_chain_history_cycle",
        cycle,
    )
    result = market_chain_history_cli.run(
        [
            "--seed-batch-limit", "8",
            "--refresh-batch-limit", "4",
            "--bin-array-radius", "1",
            "--timeout-seconds", "30",
            "--observed-at", "2026-09-27T08:00:00+00:00",
        ],
        settings=cfg,
    )

    assert seen["database_path"] == cfg.database_path
    assert seen["seed_batch_limit"] == 8
    assert seen["refresh_batch_limit"] == 4
    assert seen["bin_array_radius"] == 1
    assert seen["timeout_seconds"] == 30
    assert result["status"] == "COMPLETE"
