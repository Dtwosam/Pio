from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from meteora_learner.market_paper_intake_cli import run
from meteora_learner.settings import Settings


@dataclass(frozen=True)
class FakeReport:
    research_only: bool = True
    paper_only: bool = True
    policy_actionable: bool = False
    execution_wired: bool = False

    def to_record(self) -> dict:
        return {
            "research_only": self.research_only,
            "paper_only": self.paper_only,
            "policy_actionable": self.policy_actionable,
            "execution_wired": self.execution_wired,
            "pools_ready": 3,
        }


def test_cli_forwards_bounded_intake_arguments(tmp_path: Path) -> None:
    cfg = Settings(database_path=tmp_path / "pio.db")
    seen: dict = {}

    def intake(database_path, **kwargs):
        seen["database_path"] = database_path
        seen.update(kwargs)
        return FakeReport()

    result = run(
        [
            "--minimum-chain-observations",
            "20",
            "--max-pools",
            "125",
        ],
        settings=cfg,
        intake=intake,
    )

    assert seen["database_path"] == cfg.database_path
    assert seen["minimum_chain_observations"] == 20
    assert seen["max_pools"] == 125
    assert result["pools_ready"] == 3
    assert result["research_only"] is True
    assert result["paper_only"] is True
    assert result["policy_actionable"] is False
    assert result["execution_wired"] is False


@pytest.mark.parametrize(
    ("research_only", "paper_only", "policy_actionable", "execution_wired"),
    [
        (False, True, False, False),
        (True, False, False, False),
        (True, True, True, False),
        (True, True, False, True),
    ],
)
def test_cli_fails_closed_if_intake_boundary_changes(
    tmp_path: Path,
    research_only: bool,
    paper_only: bool,
    policy_actionable: bool,
    execution_wired: bool,
) -> None:
    cfg = Settings(database_path=tmp_path / "pio.db")
    report = FakeReport(
        research_only=research_only,
        paper_only=paper_only,
        policy_actionable=policy_actionable,
        execution_wired=execution_wired,
    )

    with pytest.raises(
        RuntimeError,
        match="crossed safety boundary",
    ):
        run(
            [],
            settings=cfg,
            intake=lambda database_path, **kwargs: report,
        )
