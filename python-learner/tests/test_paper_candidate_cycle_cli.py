from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from meteora_learner.paper_candidate_cycle_cli import run
from meteora_learner.settings import Settings


@dataclass(frozen=True)
class FakeReport:
    paper_only: bool = True
    policy_actionable: bool = False
    live_authorized: bool = False

    def to_record(self) -> dict:
        return {
            "pool_address": "pool-1",
            "paper_only": self.paper_only,
            "policy_actionable": self.policy_actionable,
            "live_authorized": self.live_authorized,
        }


def _args(*extra: str) -> list[str]:
    return [
        "--pool",
        "pool-1",
        "--amount-x",
        "100",
        "--amount-y",
        "200",
        "--network-cost-y-atomic",
        "5",
        *extra,
    ]


def test_cli_runs_one_bounded_paper_candidate_cycle(tmp_path: Path) -> None:
    cfg = Settings(database_path=tmp_path / "pio.db")
    seen: dict = {}

    def cycle(database_path, **kwargs):
        seen["database_path"] = database_path
        seen.update(kwargs)
        return FakeReport()

    result = run(
        _args(
            "--lookback-observations",
            "20",
            "--forward-observations",
            "3",
            "--step-observations",
            "2",
            "--as-of",
            "2026-09-27T08:00:00+00:00",
        ),
        settings=cfg,
        cycle=cycle,
    )

    assert seen["database_path"] == cfg.database_path
    assert seen["pool_address"] == "pool-1"
    assert seen["amount_x"] == 100
    assert seen["amount_y"] == 200
    assert seen["network_cost_y_atomic"] == 5
    assert seen["lookback_observations"] == 20
    assert seen["forward_observations"] == 3
    assert seen["step_observations"] == 2
    assert seen["as_of"] == "2026-09-27T08:00:00+00:00"
    assert result["paper_only"] is True
    assert result["policy_actionable"] is False
    assert result["live_authorized"] is False
    assert result["persisted_evidence_id"] is None


def test_cli_persists_only_when_explicitly_requested(tmp_path: Path) -> None:
    cfg = Settings(database_path=tmp_path / "pio.db")
    calls: list[tuple[Path, FakeReport]] = []
    report = FakeReport()

    def persist_fn(storage, *, report):
        calls.append((storage.path, report))
        return 42

    result = run(
        _args("--persist"),
        settings=cfg,
        cycle=lambda database_path, **kwargs: report,
        persist_fn=persist_fn,
    )

    assert calls == [(cfg.database_path, report)]
    assert result["persisted_evidence_id"] == 42


@pytest.mark.parametrize(
    ("paper_only", "policy_actionable", "live_authorized"),
    [
        (False, False, False),
        (True, True, False),
        (True, False, True),
    ],
)
def test_cli_fails_closed_if_paper_boundary_changes(
    tmp_path: Path,
    paper_only: bool,
    policy_actionable: bool,
    live_authorized: bool,
) -> None:
    cfg = Settings(database_path=tmp_path / "pio.db")

    report = FakeReport(
        paper_only=paper_only,
        policy_actionable=policy_actionable,
        live_authorized=live_authorized,
    )

    with pytest.raises(
        RuntimeError,
        match="crossed safety boundary",
    ):
        run(
            _args(),
            settings=cfg,
            cycle=lambda database_path, **kwargs: report,
        )
