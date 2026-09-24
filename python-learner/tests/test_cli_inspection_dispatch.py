import json
import sys
from types import SimpleNamespace

from meteora_learner import cli
from meteora_learner.storage import Storage


def _report(**values):
    return SimpleNamespace(
        to_record=lambda: {"ok": True},
        **values,
    )


def test_phase8_evidence_plan_cli_preserves_live_mode(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    seen = {}
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))

    def build(*args, **kwargs):
        seen["as_of"] = kwargs["as_of"]
        return _report(persisted_phase8_current=True)

    monkeypatch.setattr(cli, "build_phase8_evidence_plan", build)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pio", "phase8-evidence-plan"],
    )

    cli.main()

    assert seen["as_of"] is None
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_phase8_evidence_status_cli_preserves_live_mode(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    seen = {}
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))

    def evaluate(*args, **kwargs):
        seen["as_of"] = kwargs["as_of"]
        return _report(
            promotion_ready=True,
            persisted_phase8_current=True,
        )

    monkeypatch.setattr(cli, "evaluate_phase8_evidence_status", evaluate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pio", "phase8-evidence-status"],
    )

    cli.main()

    assert seen["as_of"] is None
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_phase9_source_freshness_cli_preserves_live_mode(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    seen = {}
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))

    def evaluate(*args, **kwargs):
        seen["as_of"] = kwargs["as_of"]
        return _report(current=True)

    monkeypatch.setattr(cli, "evaluate_phase9_source_freshness", evaluate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pio", "phase9-source-freshness"],
    )

    cli.main()

    assert seen["as_of"] is None
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_phase9_evidence_status_cli_preserves_live_mode(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    seen = {}
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))

    def evaluate(*args, **kwargs):
        seen["as_of"] = kwargs["as_of"]
        return _report(
            chain_history_ready=True,
            mint_ready_pools=2,
            mint_required_pools=2,
            wallet_ready_pools=2,
            wallet_required_pools=2,
            explicit_inputs_valid=True,
            research_sources_current=True,
            research_bundle_ready=True,
        )

    monkeypatch.setattr(cli, "evaluate_phase9_evidence_status", evaluate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pio", "phase9-evidence-status"],
    )

    cli.main()

    assert seen["as_of"] is None
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_phase9_evidence_plan_cli_preserves_live_mode(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    seen = {}
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))

    def build(*args, **kwargs):
        seen["as_of"] = kwargs["as_of"]
        return _report(research_bundle_ready=True)

    monkeypatch.setattr(cli, "build_phase9_evidence_plan", build)
    monkeypatch.setattr(
        sys,
        "argv",
        ["pio", "phase9-evidence-plan"],
    )

    cli.main()

    assert seen["as_of"] is None
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_phase9_evidence_plan_cli_preserves_explicit_cutoff(
    monkeypatch,
    tmp_path,
    capsys,
):
    storage = Storage(tmp_path / "pio.db")
    seen = {}
    cutoff = "2026-09-23T12:00:00+00:00"
    monkeypatch.setenv("PIO_DATABASE_PATH", str(storage.path))

    def build(*args, **kwargs):
        seen["as_of"] = kwargs["as_of"]
        return _report(research_bundle_ready=False)

    monkeypatch.setattr(cli, "build_phase9_evidence_plan", build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pio",
            "phase9-evidence-plan",
            "--as-of",
            cutoff,
        ],
    )

    cli.main()

    assert seen["as_of"] == cutoff
    assert json.loads(capsys.readouterr().out) == {"ok": True}
