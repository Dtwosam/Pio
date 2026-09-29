from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from meteora_learner import cli as MODULE


ROOT = Path(__file__).resolve().parents[2]


class _Result:
    def to_record(self):
        return {
            "paper_only": True,
            "live_authorized": False,
            "atomic_pair_open": True,
        }


def test_paper_open_ml_pair_dispatches_explicit_economic_inputs(
    monkeypatch,
    capsys,
):
    captured = {}
    monkeypatch.setattr(
        MODULE.Settings,
        "from_env",
        classmethod(
            lambda cls: SimpleNamespace(database_path=Path("/tmp/pio.db"))
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "Storage",
        lambda path: SimpleNamespace(path=path),
    )

    def run(storage, **kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(MODULE, "open_paired_ml_paper_entries", run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "pio",
            "paper-open-ml-pair",
            "--account",
            "paper-1",
            "--cycle-id",
            "cycle-1",
            "--pool",
            "pool-1",
            "--amount-x",
            "100",
            "--amount-y",
            "200",
            "--network-cost-y-atomic",
            "3",
            "--capital",
            "1000",
            "--entry-cost",
            "5",
            "--incumbent-position",
            "inc-pos",
            "--challenger-position",
            "chal-pos",
            "--incumbent-event-key",
            "inc-event",
            "--challenger-event-key",
            "chal-event",
            "--as-of",
            "2026-09-29T22:30:00+00:00",
        ],
    )

    MODULE.main()
    output = json.loads(capsys.readouterr().out)

    assert captured["account_id"] == "paper-1"
    assert captured["cycle_id"] == "cycle-1"
    assert captured["pool_address"] == "pool-1"
    assert captured["amount_x"] == 100
    assert captured["amount_y"] == 200
    assert captured["network_cost_y_atomic"] == 3
    assert captured["capital_quote"] == 1000.0
    assert captured["entry_cost_quote"] == 5.0
    assert captured["incumbent_position_id"] == "inc-pos"
    assert captured["challenger_position_id"] == "chal-pos"
    assert captured["incumbent_event_key"] == "inc-event"
    assert captured["challenger_event_key"] == "chal-event"
    assert captured["as_of"] == "2026-09-29T22:30:00+00:00"
    assert captured["inference_config"].risk_lambda == 1.5
    assert (
        captured["inference_config"].min_positive_excess_probability
        == 0.55
    )
    assert (
        captured["inference_config"].min_range_survival_probability
        == 0.50
    )
    assert captured["inference_config"].min_score_bps == 0.0
    assert output["paper_only"] is True
    assert output["live_authorized"] is False
    assert output["atomic_pair_open"] is True


def test_paper_open_ml_pair_requires_entry_cost(monkeypatch):
    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True
        return _Result()

    monkeypatch.setattr(MODULE, "open_paired_ml_paper_entries", run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "pio",
            "paper-open-ml-pair",
            "--account",
            "paper-1",
            "--cycle-id",
            "cycle-1",
            "--pool",
            "pool-1",
            "--amount-x",
            "100",
            "--amount-y",
            "200",
            "--network-cost-y-atomic",
            "3",
            "--capital",
            "1000",
            "--incumbent-position",
            "inc-pos",
            "--challenger-position",
            "chal-pos",
            "--incumbent-event-key",
            "inc-event",
            "--challenger-event-key",
            "chal-event",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        MODULE.main()

    assert exc.value.code == 2
    assert called is False


def test_paper_open_ml_pair_requires_capital(monkeypatch):
    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True
        return _Result()

    monkeypatch.setattr(MODULE, "open_paired_ml_paper_entries", run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "pio",
            "paper-open-ml-pair",
            "--account",
            "paper-1",
            "--cycle-id",
            "cycle-1",
            "--pool",
            "pool-1",
            "--amount-x",
            "100",
            "--amount-y",
            "200",
            "--network-cost-y-atomic",
            "3",
            "--entry-cost",
            "5",
            "--incumbent-position",
            "inc-pos",
            "--challenger-position",
            "chal-pos",
            "--incumbent-event-key",
            "inc-event",
            "--challenger-event-key",
            "chal-event",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        MODULE.main()

    assert exc.value.code == 2
    assert called is False


def test_paired_ml_cli_source_has_no_live_submit_path():
    source = (
        ROOT
        / "python-learner"
        / "src"
        / "meteora_learner"
        / "cli.py"
    ).read_text(encoding="utf-8")
    command_start = source.index('if args.command == "paper-open-ml-pair":')
    command_end = source.index(
        'if args.command == "paper-open-phase3":',
        command_start,
    )
    block = source[command_start:command_end]

    assert "send_transaction" not in block
    assert "controlled-live-submit" not in block
    assert "PIO_EXECUTOR_KEYPAIR" not in block
    assert "open_paired_ml_paper_entries" in block
