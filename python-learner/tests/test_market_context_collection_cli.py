from __future__ import annotations

import json

import pytest

from meteora_learner.market_context_collection_cli import main


def test_context_collection_cli_noops_on_empty_store_without_discovery(
    tmp_path,
    capsys,
) -> None:
    code = main(
        [
            "--database",
            str(tmp_path / "pio.db"),
            "--no-discovery",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "NO_CHANGE"
    assert payload["research_only"] is True
    assert payload["read_only_capture"] is True
    assert payload["policy_actionable"] is False
    assert payload["execution_wired"] is False


def test_context_collection_cli_rejects_all_stages_disabled(
    tmp_path,
) -> None:
    with pytest.raises(ValueError, match="at least one collection stage"):
        main(
            [
                "--database",
                str(tmp_path / "pio.db"),
                "--no-discovery",
                "--no-chain",
                "--no-mints",
            ]
        )
