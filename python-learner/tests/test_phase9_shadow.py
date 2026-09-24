from dataclasses import replace
from types import SimpleNamespace

import meteora_learner.phase9_shadow as shadow_module
from meteora_learner.phase9_shadow import (
    Phase9ShadowCriteria,
    evaluate_phase9_shadow,
    persist_phase9_shadow,
)
from meteora_learner.phase9_validation import Phase9ResearchBundleCriteria
from meteora_learner.storage import Storage


class DummyAudit:
    def __init__(self, current=True, reasons=()):
        self.current = current
        self.reasons = tuple(reasons)

    def to_record(self):
        return {
            "current": self.current,
            "reasons": list(self.reasons),
        }


class DummyDatasetResult:
    def __init__(self, *, cutoff, qualified=True, reasons=()):
        self.lineage = SimpleNamespace(
            source_type="PHASE9_BANDIT_DATASET_V1",
            dataset_evidence_id=88,
            dataset_artifact_sha256="b" * 64,
            dataset_version="ML_ACTION_DATASET_V1:shadow",
            dataset_sha256="c" * 64,
            cutoff=cutoff,
            output_file="/tmp/phase9-shadow.csv",
            explicit_input_evidence_id=77,
            explicit_input_artifact_sha256="d" * 64,
        )
        self.report = SimpleNamespace(
            research_qualified=qualified,
            reasons=tuple(reasons),
        )

    def to_record(self):
        return {
            "lineage": {
                "source_type": self.lineage.source_type,
                "dataset_evidence_id": self.lineage.dataset_evidence_id,
                "dataset_artifact_sha256": (
                    self.lineage.dataset_artifact_sha256
                ),
                "dataset_version": self.lineage.dataset_version,
                "dataset_sha256": self.lineage.dataset_sha256,
                "cutoff": self.lineage.cutoff,
                "output_file": self.lineage.output_file,
                "explicit_input_evidence_id": (
                    self.lineage.explicit_input_evidence_id
                ),
                "explicit_input_artifact_sha256": (
                    self.lineage.explicit_input_artifact_sha256
                ),
            },
            "report": {
                "research_qualified": self.report.research_qualified,
                "reasons": list(self.report.reasons),
            },
        }


class DummyCycleResult:
    def __init__(self, *, cutoff, qualified=True, reasons=()):
        self.lineage = SimpleNamespace(
            cycle_id="shadow-cycle",
            champion_model_id="champion",
            dataset_evidence_id=7,
            dataset_version="ML_ACTION_DATASET_V1:shadow",
            dataset_sha256="a" * 64,
            cutoff=cutoff,
            output_file="/tmp/shadow.csv",
        )
        self.report = SimpleNamespace(
            research_qualified=qualified,
            reasons=tuple(reasons),
        )

    def to_record(self):
        return {
            "lineage": {
                "cycle_id": self.lineage.cycle_id,
                "champion_model_id": self.lineage.champion_model_id,
                "dataset_evidence_id": self.lineage.dataset_evidence_id,
                "dataset_version": self.lineage.dataset_version,
                "dataset_sha256": self.lineage.dataset_sha256,
                "cutoff": self.lineage.cutoff,
                "output_file": self.lineage.output_file,
            },
            "report": {
                "research_qualified": self.report.research_qualified,
                "reasons": list(self.report.reasons),
            },
        }


def patch_context(
    monkeypatch,
    *,
    promoted_at="2026-09-23T12:00:00+00:00",
    phase9_current=True,
    cutoff="2026-09-23T13:00:00+00:00",
    bandit_qualified=True,
):
    monkeypatch.setattr(
        shadow_module,
        "_persisted_phase9_context",
        lambda storage: (
            promoted_at,
            Phase9ResearchBundleCriteria(),
            (),
        ),
    )
    monkeypatch.setattr(
        shadow_module,
        "audit_persisted_phase9_promotion",
        lambda storage, criteria: DummyAudit(
            current=phase9_current,
            reasons=(
                ()
                if phase9_current
                else ("persisted Phase 9 promotion is stale",)
            ),
        ),
    )
    monkeypatch.setattr(
        shadow_module,
        "evaluate_cycle_contextual_bandit",
        lambda storage, cycle_id, criteria: DummyCycleResult(
            cutoff=cutoff,
            qualified=bandit_qualified,
            reasons=(
                ()
                if bandit_qualified
                else ("shadow replay missed threshold",)
            ),
        ),
    )


def test_post_promotion_shadow_corpus_can_be_ready(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    patch_context(monkeypatch)

    report = evaluate_phase9_shadow(
        storage,
        cycle_id="shadow-cycle",
        criteria=Phase9ShadowCriteria(
            min_decisions=1,
            min_pools=1,
            min_selected_arms=1,
        ),
    )

    assert report.shadow_ready is True
    assert report.phase9_current is True
    assert report.cutoff_after_promotion is True
    assert report.seconds_after_promotion == 3600
    assert report.bandit_research_qualified is True
    assert report.research_only is True
    assert report.policy_actionable is False
    assert report.reasons == ()


def test_shadow_corpus_must_be_strictly_post_promotion(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_context(
        monkeypatch,
        cutoff="2026-09-23T12:00:00+00:00",
    )

    report = evaluate_phase9_shadow(
        storage,
        cycle_id="shadow-cycle",
        criteria=Phase9ShadowCriteria(
            min_decisions=1,
            min_pools=1,
            min_selected_arms=1,
        ),
    )

    assert report.shadow_ready is False
    assert report.cutoff_after_promotion is False
    assert report.seconds_after_promotion == 0
    assert any(
        "not sufficiently after" in reason
        for reason in report.reasons
    )


def test_stale_phase9_blocks_shadow_readiness(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    patch_context(
        monkeypatch,
        phase9_current=False,
    )

    report = evaluate_phase9_shadow(
        storage,
        cycle_id="shadow-cycle",
        criteria=Phase9ShadowCriteria(
            min_decisions=1,
            min_pools=1,
            min_selected_arms=1,
        ),
    )

    assert report.shadow_ready is False
    assert report.phase9_current is False
    assert any(
        "Phase 9 currentness" in reason
        for reason in report.reasons
    )


def test_shadow_persistence_stays_non_actionable(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    patch_context(monkeypatch)
    report = evaluate_phase9_shadow(
        storage,
        cycle_id="shadow-cycle",
        criteria=Phase9ShadowCriteria(
            min_decisions=1,
            min_pools=1,
            min_selected_arms=1,
        ),
    )

    evidence_id = persist_phase9_shadow(
        storage,
        report=report,
    )
    assert evidence_id > 0
    persisted = storage.latest_advanced_edge_evidence(
        edge_type="PHASE9_POST_PROMOTION_SHADOW_V1",
        pool_address="__PHASE9_SHADOW__",
    )
    assert persisted is not None
    assert persisted["qualified"] is True
    assert persisted["evidence"]["research_only"] is True
    assert persisted["evidence"]["policy_actionable"] is False

    forged = replace(
        report,
        policy_actionable=True,
    )
    try:
        persist_phase9_shadow(storage, report=forged)
    except ValueError as exc:
        assert "must not grant policy authority" in str(exc)
    else:
        raise AssertionError("expected policy-actionable shadow refusal")


def test_post_promotion_shadow_can_use_phase9_bandit_dataset(
    monkeypatch,
    tmp_path,
):
    storage = Storage(tmp_path / "pio.db")
    patch_context(monkeypatch)
    monkeypatch.setattr(
        shadow_module,
        "evaluate_phase9_contextual_bandit_from_dataset",
        lambda storage, dataset_evidence_id, criteria: DummyDatasetResult(
            cutoff="2026-09-23T13:00:00+00:00",
        ),
    )

    report = evaluate_phase9_shadow(
        storage,
        dataset_evidence_id=88,
        criteria=Phase9ShadowCriteria(
            min_decisions=1,
            min_pools=1,
            min_selected_arms=1,
        ),
    )

    assert report.shadow_ready is True
    assert report.cycle_id == "phase9-dataset:88"
    assert report.dataset_source_type == "PHASE9_BANDIT_DATASET_V1"
    assert report.dataset_evidence_id == 88
    assert report.dataset_sha256 == "c" * 64
    assert report.dataset_cutoff == "2026-09-23T13:00:00+00:00"
    assert report.cutoff_after_promotion is True
    assert report.bandit_research_qualified is True
    assert report.policy_actionable is False


def test_shadow_requires_exactly_one_dataset_source(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "pio.db")
    patch_context(monkeypatch)

    for kwargs in (
        {},
        {"cycle_id": "cycle-a", "dataset_evidence_id": 88},
    ):
        try:
            evaluate_phase9_shadow(
                storage,
                criteria=Phase9ShadowCriteria(
                    min_decisions=1,
                    min_pools=1,
                    min_selected_arms=1,
                ),
                **kwargs,
            )
        except ValueError as exc:
            assert "exactly one" in str(exc)
        else:
            raise AssertionError("expected shadow source validation failure")
