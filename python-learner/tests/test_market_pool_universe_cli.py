from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from meteora_learner.market_pool_universe_cli import run
from meteora_learner.settings import Settings


@dataclass(frozen=True)
class FakeReport:
    research_only: bool = True
    policy_actionable: bool = False
    execution_wired: bool = False

    def to_record(self) -> dict:
        return {
            "research_only": self.research_only,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
            "unique_pools_seen": 7,
        }


def test_cli_runs_bounded_research_discovery(tmp_path: Path) -> None:
    cfg = Settings(database_path=tmp_path / "pio.db")
    seen: dict = {}

    def discover(storage, **kwargs):
        seen["database_path"] = storage.path
        seen.update(kwargs)
        return FakeReport()

    result = run(
        [
            "--page-size",
            "250",
            "--max-pages",
            "4",
            "--sort-by",
            "volume_24h:desc",
        ],
        settings=cfg,
        discover=discover,
    )

    assert seen["database_path"] == cfg.database_path
    assert seen["settings"] == cfg
    assert seen["page_size"] == 250
    assert seen["max_pages"] == 4
    assert seen["sort_by"] == "volume_24h:desc"
    assert result["research_only"] is True
    assert result["policy_actionable"] is False
    assert result["execution_wired"] is False


@pytest.mark.parametrize(
    ("research_only", "policy_actionable", "execution_wired"),
    [
        (False, False, False),
        (True, True, False),
        (True, False, True),
    ],
)
def test_cli_fails_closed_if_boundary_changes(
    tmp_path: Path,
    research_only: bool,
    policy_actionable: bool,
    execution_wired: bool,
) -> None:
    cfg = Settings(database_path=tmp_path / "pio.db")

    def discover(storage, **kwargs):
        return FakeReport(
            research_only=research_only,
            policy_actionable=policy_actionable,
            execution_wired=execution_wired,
        )

    with pytest.raises(
        RuntimeError,
        match="crossed research-only boundary",
    ):
        run([], settings=cfg, discover=discover)
