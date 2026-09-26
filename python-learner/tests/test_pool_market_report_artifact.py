from __future__ import annotations

import json

import pytest

from meteora_learner.pool_market_data_quality import (
    PoolMarketCoverageReport,
)
from meteora_learner.pool_market_report_artifact import (
    load_pool_market_research_report,
    save_pool_market_research_report,
)
from meteora_learner.pool_market_research_cycle import (
    PoolMarketResearchCycleReport,
)


def _report() -> PoolMarketResearchCycleReport:
    return PoolMarketResearchCycleReport(
        research_only=True,
        policy_actionable=False,
        execution_wired=False,
        status="COLLECTING_HISTORY",
        reason="not enough history",
        discovery=None,
        coverage=PoolMarketCoverageReport(
            research_only=True,
            policy_actionable=False,
            pools_seen=0,
            observations_seen=0,
            coverage=(),
        ),
        dataset=None,
        walk_forward=None,
    )


def test_report_artifact_round_trip(tmp_path) -> None:
    saved = save_pool_market_research_report(
        _report(),
        directory=tmp_path,
        report_id="cycle-001",
    )

    loaded = load_pool_market_research_report(
        report_path=saved.report_path,
        metadata_path=saved.metadata_path,
    )

    assert loaded["status"] == "COLLECTING_HISTORY"
    assert loaded["research_only"] is True
    assert loaded["policy_actionable"] is False
    assert loaded["execution_wired"] is False

    metadata = json.loads(
        saved.metadata_path.read_text(encoding="utf-8")
    )
    assert metadata["report_sha256"] == saved.report_sha256


def test_report_artifact_rejects_tampering(tmp_path) -> None:
    saved = save_pool_market_research_report(
        _report(),
        directory=tmp_path,
        report_id="cycle-002",
    )
    saved.report_path.write_text(
        '{"research_only": true}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_pool_market_research_report(
            report_path=saved.report_path,
            metadata_path=saved.metadata_path,
        )


def test_report_artifact_refuses_overwrite(tmp_path) -> None:
    save_pool_market_research_report(
        _report(),
        directory=tmp_path,
        report_id="cycle-003",
    )

    with pytest.raises(ValueError, match="already exists"):
        save_pool_market_research_report(
            _report(),
            directory=tmp_path,
            report_id="cycle-003",
        )


def test_report_id_is_path_safe(tmp_path) -> None:
    with pytest.raises(ValueError, match="report_id"):
        save_pool_market_research_report(
            _report(),
            directory=tmp_path,
            report_id="../escape",
        )
