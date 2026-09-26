from dataclasses import replace
from types import SimpleNamespace

from meteora_learner.research_store import ResearchStore
from meteora_learner.rotation_fee_semantics_corpus import (
    ROTATION_FEE_SEMANTICS_CORPUS_EVIDENCE_TYPE,
    build_rotation_fee_semantics_corpus,
    persist_rotation_fee_semantics_corpus,
)
from meteora_learner.storage import Storage


POOL = "pool"


class FakeStore:
    def transaction_event_position_addresses(
        self,
        *,
        event_type=None,
        pool_address=None,
    ):
        assert event_type == "Rebalancing"
        assert pool_address == POOL
        return ["position-a", "position-b"]


class FakeReport(SimpleNamespace):
    def to_record(self):
        return dict(self.__dict__)


def sample(
    signature,
    *,
    pool=POOL,
    evidence_class="FEE_SEPARATE_ONLY_EXACT",
    claim=True,
    eligible=True,
):
    return SimpleNamespace(
        signature=signature,
        pool_address=pool,
        evidence_class=evidence_class,
        should_claim_fee=claim,
        eligible=eligible,
    )


def report(
    position,
    samples,
    *,
    quote_eligible,
    claim_true,
    claim_false,
    base_residual,
    fee_residual,
):
    eligible = sum(item.eligible for item in samples)
    return FakeReport(
        position_address=position,
        eligible_transactions=eligible,
        quote_eligible_transactions=quote_eligible,
        claim_fee_true_samples=claim_true,
        claim_fee_false_samples=claim_false,
        quoted_base_residual_net=base_residual,
        quoted_fee_separate_residual_net=fee_residual,
        samples=tuple(samples),
    )


def install_store(monkeypatch):
    monkeypatch.setattr(
        "meteora_learner.rotation_fee_semantics_corpus.ResearchStore",
        lambda _: FakeStore(),
    )


def test_corpus_aggregates_quote_normalized_hypotheses_without_resolving(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_store(monkeypatch)
    reports = {
        "position-a": report(
            "position-a",
            (
                sample("sig-a", claim=True),
                sample(
                    "sig-b",
                    evidence_class="BASE_FLOW_ONLY_EXACT",
                    claim=False,
                ),
            ),
            quote_eligible=1,
            claim_true=1,
            claim_false=1,
            base_residual=31.0,
            fee_residual=-4.0,
        ),
        "position-b": report(
            "position-b",
            (
                sample(
                    "sig-c",
                    evidence_class="FEE_SEPARATE_ONLY_EXACT",
                    claim=None,
                ),
            ),
            quote_eligible=1,
            claim_true=0,
            claim_false=0,
            base_residual=9.0,
            fee_residual=0.0,
        ),
    }
    monkeypatch.setattr(
        "meteora_learner.rotation_fee_semantics_corpus.build_rotation_fee_semantics_report",
        lambda storage, *, position_address, max_quote_age_seconds: reports[
            position_address
        ],
    )

    corpus = build_rotation_fee_semantics_corpus(
        storage,
        pool_address=POOL,
    )

    assert corpus.positions_seen == 2
    assert corpus.positions_reported == 2
    assert corpus.positions_failed == 0
    assert corpus.transactions_seen == 3
    assert corpus.eligible_transactions == 3
    assert corpus.quote_eligible_transactions == 2
    assert corpus.quote_coverage_rate == 2 / 3
    assert corpus.claim_fee_true_samples == 1
    assert corpus.claim_fee_false_samples == 1
    assert corpus.claim_fee_unknown_samples == 1
    assert {
        item.evidence_class: item.count
        for item in corpus.evidence_class_counts
    } == {
        "FEE_SEPARATE_ONLY_EXACT": 2,
        "BASE_FLOW_ONLY_EXACT": 1,
    }
    assert corpus.quoted_base_residual_net == 40.0
    assert corpus.quoted_fee_separate_residual_net == -4.0
    assert corpus.semantics_resolved is False
    assert corpus.conclusion == "UNRESOLVED_OBSERVATIONAL_CORPUS"


def test_corpus_excludes_position_that_does_not_resolve_to_requested_pool(
    tmp_path,
    monkeypatch,
):
    storage = Storage(tmp_path / "pio.db")
    install_store(monkeypatch)
    monkeypatch.setattr(
        "meteora_learner.rotation_fee_semantics_corpus.build_rotation_fee_semantics_report",
        lambda storage, *, position_address, max_quote_age_seconds: (
            report(
                position_address,
                (sample(f"sig-{position_address}", pool="other-pool"),),
                quote_eligible=1,
                claim_true=1,
                claim_false=0,
                base_residual=10.0,
                fee_residual=0.0,
            )
            if position_address == "position-a"
            else report(
                position_address,
                (sample("sig-b"),),
                quote_eligible=1,
                claim_true=1,
                claim_false=0,
                base_residual=5.0,
                fee_residual=0.0,
            )
        ),
    )

    corpus = build_rotation_fee_semantics_corpus(
        storage,
        pool_address=POOL,
    )

    assert corpus.positions_seen == 2
    assert corpus.positions_reported == 1
    assert corpus.positions_failed == 1
    assert corpus.failures[0].position_address == "position-a"
    assert corpus.failures[0].category == "POSITION_POOL_MISMATCH"
    assert corpus.transactions_seen == 1
    assert corpus.quoted_base_residual_net == 5.0


def test_corpus_hides_position_report_exception_text(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "pio.db")
    install_store(monkeypatch)
    secret = "https://user:secret@example.invalid/rpc"

    def build(storage, *, position_address, max_quote_age_seconds):
        if position_address == "position-a":
            raise RuntimeError(f"failed at {secret}")
        return report(
            position_address,
            (sample("sig-b"),),
            quote_eligible=0,
            claim_true=1,
            claim_false=0,
            base_residual=0.0,
            fee_residual=0.0,
        )

    monkeypatch.setattr(
        "meteora_learner.rotation_fee_semantics_corpus.build_rotation_fee_semantics_report",
        build,
    )

    corpus = build_rotation_fee_semantics_corpus(
        storage,
        pool_address=POOL,
    )

    assert corpus.failures[0].category == "POSITION_REPORT_FAILED"
    encoded = str(corpus.to_record())
    assert secret not in encoded
    assert "failed at" not in encoded


def test_corpus_persistence_is_non_qualified(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "pio.db")
    install_store(monkeypatch)
    monkeypatch.setattr(
        "meteora_learner.rotation_fee_semantics_corpus.build_rotation_fee_semantics_report",
        lambda storage, *, position_address, max_quote_age_seconds: report(
            position_address,
            (sample(f"sig-{position_address}"),),
            quote_eligible=1,
            claim_true=1,
            claim_false=0,
            base_residual=1.0,
            fee_residual=0.0,
        ),
    )
    corpus = build_rotation_fee_semantics_corpus(
        storage,
        pool_address=POOL,
    )

    evidence_id = persist_rotation_fee_semantics_corpus(
        storage,
        report=corpus,
    )

    assert evidence_id > 0
    saved = storage.latest_advanced_edge_evidence(
        edge_type=ROTATION_FEE_SEMANTICS_CORPUS_EVIDENCE_TYPE,
        pool_address=POOL,
    )
    assert saved is not None
    assert saved["qualified"] is False
    assert saved["status"] == "UNRESOLVED_OBSERVATIONAL_CORPUS"
    assert saved["evidence"]["semantics_resolved"] is False

    try:
        persist_rotation_fee_semantics_corpus(
            storage,
            report=replace(corpus, semantics_resolved=True),
        )
    except ValueError as exc:
        assert "cannot self-resolve" in str(exc)
    else:
        raise AssertionError("resolved observational corpus must be rejected")


def test_transaction_event_position_addresses_can_filter_pool(tmp_path):
    storage = Storage(tmp_path / "pio.db")

    def snapshot(signature, pool, position):
        return {
            "signature": signature,
            "slot": 100,
            "block_time": 1_700_000_000,
            "succeeded": True,
            "rebalance_requests": [],
            "events": [
                {
                    "event_index": 0,
                    "parent_ix_index": 1,
                    "event": {
                        "event_type": "Rebalancing",
                        "event": {
                            "lb_pair": pool,
                            "position": position,
                            "owner": "owner",
                            "active_bin_id": 1,
                            "x_withdrawn_amount": "0",
                            "x_added_amount": "0",
                            "y_withdrawn_amount": "0",
                            "y_added_amount": "0",
                            "x_fee_amount": "0",
                            "y_fee_amount": "0",
                            "old_min_id": 0,
                            "old_max_id": 1,
                            "new_min_id": 0,
                            "new_max_id": 1,
                            "reward_one": "0",
                            "reward_two": "0",
                        },
                    },
                }
            ],
        }

    storage.save_chain_transaction_events(
        snapshot("sig-a", "pool-a", "position-a")
    )
    storage.save_chain_transaction_events(
        snapshot("sig-b", "pool-b", "position-b")
    )

    store = ResearchStore(str(storage.path))
    assert store.transaction_event_position_addresses(
        event_type="Rebalancing",
        pool_address="pool-a",
    ) == ["position-a"]
