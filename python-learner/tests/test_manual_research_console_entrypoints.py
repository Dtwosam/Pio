from __future__ import annotations

import tomllib
from pathlib import Path


def test_manual_research_console_entrypoints_are_exposed() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = config["project"]["scripts"]

    assert scripts["pio-market-universe-discover"] == (
        "meteora_learner.market_pool_universe_cli:main"
    )
    assert scripts["pio-market-paper-intake"] == (
        "meteora_learner.market_paper_intake_cli:main"
    )
    assert scripts["pio-paper-candidate-cycle"] == (
        "meteora_learner.paper_candidate_cycle_cli:main"
    )
