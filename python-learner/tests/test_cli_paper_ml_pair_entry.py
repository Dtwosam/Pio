from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from meteora_learner import paper_ml_pair_entry_cli as MODULE


ROOT = Path(__file__).resolve().parents[2]


class _Result:
    def to_record(self):
        return {
            "paper_only": True,
            "policy_actionable": False,
            "live_authorized": False,
            "atomic_pair_open": True,
        }


def _args(*, include_capital=True, include_entry_cost=True):
    values = [
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
    ]
    if include_capital:
        values += ["--capital", "1000"]
    if include_entry_cost:
        values += ["--entry-cost", "5"]
    values += [
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
    ]
    return values


def test_standalone_pair_cli_dispatches_explicit_economic_inputs():
    captured = {}

    def run(storage, **kwargs):
        captured["storage_path"] = storage.path
        captured.update(kwargs)
        return _Result()

    result = MODULE.run(
        _args(),
        settings=SimpleNamespace(database_path=Path("/tmp/pio.db")),
        pair_entry=run,
    )

    assert captured["storage_path"] == Path("/tmp/pio.db")
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
    assert result["paper_only"] is True
    assert result["policy_actionable"] is False
    assert result["live_authorized"] is False


def test_standalone_pair_cli_requires_entry_cost():
    with pytest.raises(SystemExit) as exc:
        MODULE.run(
            _args(include_entry_cost=False),
            settings=SimpleNamespace(database_path=Path("/tmp/pio.db")),
            pair_entry=lambda *args, **kwargs: _Result(),
        )
    assert exc.value.code == 2


def test_standalone_pair_cli_requires_capital():
    with pytest.raises(SystemExit) as exc:
        MODULE.run(
            _args(include_capital=False),
            settings=SimpleNamespace(database_path=Path("/tmp/pio.db")),
            pair_entry=lambda *args, **kwargs: _Result(),
        )
    assert exc.value.code == 2


def test_standalone_pair_cli_rejects_crossed_safety_boundary():
    class Unsafe:
        def to_record(self):
            return {
                "paper_only": True,
                "policy_actionable": False,
                "live_authorized": True,
            }

    with pytest.raises(RuntimeError, match="safety boundary"):
        MODULE.run(
            _args(),
            settings=SimpleNamespace(database_path=Path("/tmp/pio.db")),
            pair_entry=lambda *args, **kwargs: Unsafe(),
        )


def test_standalone_pair_cli_has_no_live_submit_path():
    source = (
        ROOT
        / "python-learner"
        / "src"
        / "meteora_learner"
        / "paper_ml_pair_entry_cli.py"
    ).read_text(encoding="utf-8")

    assert "send_transaction" not in source
    assert "controlled-live-submit" not in source
    assert "PIO_EXECUTOR_KEYPAIR" not in source
    assert "open_paired_ml_paper_entries" in source
