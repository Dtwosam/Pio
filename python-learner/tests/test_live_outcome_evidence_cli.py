from __future__ import annotations

import hashlib
import json

import pytest

from meteora_learner.live_outcome_evidence import (
    build_live_outcome_evidence,
)
from meteora_learner.live_outcome_evidence_artifact import (
    load_live_outcome_evidence_report,
    save_live_outcome_evidence_report,
)
from meteora_learner.live_outcome_evidence_cli import main
from meteora_learner.storage import Storage


def test_live_outcome_artifact_round_trip(tmp_path) -> None:
    storage = Storage(tmp_path / "pio.db")
    report = build_live_outcome_evidence(str(storage.path))

    saved = save_live_outcome_evidence_report(
        report,
        directory=tmp_path / "reports",
        report_id="live-outcome-001",
    )
    loaded = load_live_outcome_evidence_report(
        report_path=saved.report_path,
        metadata_path=saved.metadata_path,
    )

    assert loaded["samples_seen"] == 0
    assert loaded["research_only"] is True
    assert loaded["policy_actionable"] is False
    assert loaded["execution_wired"] is False


def test_live_outcome_cli_is_read_only_and_can_save_report(
    tmp_path,
    capsys,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    reports = tmp_path / "reports"

    code = main(
        [
            "--database",
            str(storage.path),
            "--output-dir",
            str(reports),
            "--report-id",
            "live-cli-test",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["samples_seen"] == 0
    assert payload["policy_actionable"] is False
    assert payload["execution_wired"] is False
    assert payload["action_cost_evidence"]["samples_seen"] == 0
    assert (
        payload["action_cost_evidence"]["transition_pairs_inferred"]
        is False
    )
    assert payload["transition_cost_evidence"]["links_seen"] == 0
    assert payload["transition_cost_evidence"]["samples_seen"] == 0
    assert payload["transition_cost_evidence"]["gaps_seen"] == 0
    assert (
        payload["transition_cost_evidence"]["transition_pairs_inferred"]
        is False
    )
    assert (
        payload["transition_cost_evidence"]["policy_actionable"]
        is False
    )
    assert (
        payload["transition_cost_evidence"]["execution_wired"]
        is False
    )
    assert (
        payload["transition_cost_evidence"][
            "full_transition_economics_included"
        ]
        is False
    )
    assert payload["artifact"]["report_id"] == "live-cli-test"
    assert (reports / "live-cli-test.json").is_file()
    assert (reports / "live-cli-test.meta.json").is_file()

    with storage.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM live_learning_labels"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM live_position_valuations"
        ).fetchone()[0] == 0



def _rewrite_saved_report(saved, mutate) -> None:
    report = json.loads(saved.report_path.read_text(encoding="utf-8"))
    mutate(report)
    payload = (
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    saved.report_path.write_bytes(payload)

    metadata = json.loads(
        saved.metadata_path.read_text(encoding="utf-8")
    )
    metadata["report_sha256"] = hashlib.sha256(payload).hexdigest()
    saved.metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_live_outcome_artifact_rejects_nested_transition_policy_escalation(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    report = build_live_outcome_evidence(str(storage.path))
    saved = save_live_outcome_evidence_report(
        report,
        directory=tmp_path / "reports",
        report_id="live-transition-policy-escalation",
    )

    _rewrite_saved_report(
        saved,
        lambda value: value["transition_cost_evidence"].__setitem__(
            "policy_actionable",
            True,
        ),
    )

    with pytest.raises(
        ValueError,
        match="transition-cost evidence cannot be policy-actionable",
    ):
        load_live_outcome_evidence_report(
            report_path=saved.report_path,
            metadata_path=saved.metadata_path,
        )


def test_live_outcome_artifact_rejects_false_full_transition_economics_claim(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    report = build_live_outcome_evidence(str(storage.path))
    saved = save_live_outcome_evidence_report(
        report,
        directory=tmp_path / "reports",
        report_id="live-transition-full-economics",
    )

    _rewrite_saved_report(
        saved,
        lambda value: value["transition_cost_evidence"].__setitem__(
            "full_transition_economics_included",
            True,
        ),
    )

    with pytest.raises(
        ValueError,
        match="cannot claim full economics",
    ):
        load_live_outcome_evidence_report(
            report_path=saved.report_path,
            metadata_path=saved.metadata_path,
        )


def test_live_outcome_artifact_rejects_inferred_transition_pairs(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "pio.db")
    report = build_live_outcome_evidence(str(storage.path))
    saved = save_live_outcome_evidence_report(
        report,
        directory=tmp_path / "reports",
        report_id="live-transition-inferred",
    )

    def mutate(value):
        value["action_cost_evidence"]["transition_pairs_inferred"] = True

    _rewrite_saved_report(saved, mutate)

    with pytest.raises(
        ValueError,
        match="action-cost evidence cannot infer transition pairs",
    ):
        load_live_outcome_evidence_report(
            report_path=saved.report_path,
            metadata_path=saved.metadata_path,
        )
