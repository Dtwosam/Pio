from __future__ import annotations

from pathlib import Path

from meteora_learner import paper_empirical_entry_cli
from meteora_learner.settings import Settings


class FakeReport:
    def to_record(self):
        return {
            "status": "OPENED",
            "paper_only": True,
            "live_authorized": False,
        }


def test_cli_forwards_explicit_virtual_entry_inputs(tmp_path: Path, monkeypatch):
    cfg = Settings(database_path=tmp_path / "pio.db")
    seen = {}

    def workflow(storage, **kwargs):
        seen["database_path"] = storage.path
        seen.update(kwargs)
        return FakeReport()

    monkeypatch.setattr(
        paper_empirical_entry_cli,
        "run_empirical_paper_entry_workflow",
        workflow,
    )
    result = paper_empirical_entry_cli.run(
        [
            "--account", "paper",
            "--position-id", "pos",
            "--event-key", "enter-pos",
            "--pool", "pool",
            "--amount-x", "10",
            "--amount-y", "20",
            "--capital-quote", "100",
            "--network-cost-y-atomic", "5",
            "--as-of", "2026-09-27T08:00:00+00:00",
        ],
        settings=cfg,
    )

    assert seen["database_path"] == cfg.database_path
    assert seen["account_id"] == "paper"
    assert seen["pool_address"] == "pool"
    assert seen["capital_quote"] == 100.0
    assert seen["as_of"] == "2026-09-27T08:00:00+00:00"
    assert result["status"] == "OPENED"
