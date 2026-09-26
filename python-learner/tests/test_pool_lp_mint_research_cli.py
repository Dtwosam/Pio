from __future__ import annotations

import json

import pandas as pd
import pytest

from meteora_learner.pool_lp_mint_report_artifact import (
    load_mint_feature_research_report,
    save_mint_feature_research_report,
)
from meteora_learner.pool_lp_mint_research import (
    MintFeatureResearchReport,
)
from meteora_learner.pool_lp_mint_research_cli import main


def _collecting_report() -> MintFeatureResearchReport:
    return MintFeatureResearchReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        status="COLLECTING_CONTEXT",
        reason="not enough context",
        source_dataset_rows=0,
        source_dataset_sha256="a" * 64,
        market_enrichment=None,
        mint_enrichment=None,
        ablation=None,
    )


def test_mint_feature_report_artifact_round_trip(tmp_path) -> None:
    saved = save_mint_feature_research_report(
        _collecting_report(),
        directory=tmp_path,
        report_id="mint-cycle-001",
    )

    loaded = load_mint_feature_research_report(
        report_path=saved.report_path,
        metadata_path=saved.metadata_path,
    )

    assert loaded["status"] == "COLLECTING_CONTEXT"
    assert loaded["research_only"] is True
    assert loaded["policy_actionable"] is False
    assert loaded["execution_wired"] is False
    assert loaded["source_dataset_sha256"] == "a" * 64


def test_mint_feature_report_artifact_rejects_tampering(
    tmp_path,
) -> None:
    saved = save_mint_feature_research_report(
        _collecting_report(),
        directory=tmp_path,
        report_id="mint-cycle-002",
    )
    saved.report_path.write_text(
        '{"research_only": true}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_mint_feature_research_report(
            report_path=saved.report_path,
            metadata_path=saved.metadata_path,
        )


def test_mint_feature_research_cli_can_save_artifact(
    tmp_path,
    capsys,
) -> None:
    database = tmp_path / "pio.db"
    dataset = tmp_path / "canonical.csv"
    reports = tmp_path / "reports"

    pd.DataFrame(
        columns=[
            "pool_address",
            "decision_observed_at",
            "forward_end_observed_at",
        ]
    ).to_csv(dataset, index=False)

    code = main(
        [
            "--database",
            str(database),
            "--dataset",
            str(dataset),
            "--output-dir",
            str(reports),
            "--report-id",
            "mint-cli-test",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "COLLECTING_CONTEXT"
    assert payload["policy_actionable"] is False
    assert payload["execution_wired"] is False
    assert payload["artifact"]["report_id"] == "mint-cli-test"
    assert (reports / "mint-cli-test.json").is_file()
    assert (reports / "mint-cli-test.meta.json").is_file()
