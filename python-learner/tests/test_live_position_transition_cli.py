from __future__ import annotations

import json
from types import SimpleNamespace

from meteora_learner import live_position_transition_cli as cli


def test_live_transition_link_cli_is_explicit_and_research_only(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    calls = []

    class Result:
        def to_record(self):
            return {
                "transition": {
                    "transition_id": "abc",
                    "previous_position_address": "OLD",
                    "next_position_address": "NEW",
                    "transition_kind": "POOL_SWITCH",
                    "annotation_source": "EXPLICIT_RESEARCH_REVIEW",
                },
                "reused_existing": False,
            }

    def fake_record(
        storage,
        *,
        previous_position_address,
        next_position_address,
    ):
        calls.append(
            (
                str(storage.path),
                previous_position_address,
                next_position_address,
            )
        )
        return Result()

    monkeypatch.setattr(
        cli,
        "record_live_position_transition",
        fake_record,
    )
    monkeypatch.setattr(
        cli.Settings,
        "from_env",
        staticmethod(
            lambda: SimpleNamespace(
                database_path=tmp_path / "unused.db"
            )
        ),
    )

    database = tmp_path / "pio.db"
    code = cli.main(
        [
            "--database",
            str(database),
            "--previous-position",
            "OLD",
            "--next-position",
            "NEW",
        ]
    )

    assert code == 0
    assert calls == [(str(database), "OLD", "NEW")]
    payload = json.loads(capsys.readouterr().out)
    assert payload["research_only"] is True
    assert payload["policy_actionable"] is False
    assert payload["execution_wired"] is False
    assert payload["transition_pairs_inferred"] is False
    assert payload["live_position_state_modified"] is False
    assert payload["execution_state_modified"] is False
    assert payload["transition"]["previous_position_address"] == "OLD"
    assert payload["transition"]["next_position_address"] == "NEW"


def test_live_transition_link_cli_source_has_no_execution_primitive() -> None:
    source = (
        cli.__file__
        and open(cli.__file__, encoding="utf-8").read()
    )

    assert "send_transaction" not in source
    assert "send_and_confirm" not in source
    assert "live_execution_receipts" not in source
    assert "live_positions SET" not in source
    assert "execute" not in source.lower() or "does not execute" in source.lower()
