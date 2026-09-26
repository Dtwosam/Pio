from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .quote_registry import DEFAULT_QUOTE_UNIT
from .research_store import ResearchStore
from .rotation_fee_semantics import build_rotation_fee_semantics_report
from .storage import Storage


ROTATION_FEE_SEMANTICS_CORPUS_EVIDENCE_TYPE = (
    "ROTATION_FEE_SEMANTICS_CORPUS_V1"
)


@dataclass(frozen=True)
class RotationFeeSemanticsCorpusFailure:
    position_address: str
    category: str


@dataclass(frozen=True)
class RotationFeeSemanticsClassCount:
    evidence_class: str
    count: int


@dataclass(frozen=True)
class RotationFeeSemanticsCorpusReport:
    pool_address: str
    quote_unit: str
    positions_seen: int
    positions_reported: int
    positions_failed: int
    transactions_seen: int
    eligible_transactions: int
    quote_eligible_transactions: int
    quote_coverage_rate: float
    claim_fee_true_samples: int
    claim_fee_false_samples: int
    claim_fee_unknown_samples: int
    evidence_class_counts: tuple[RotationFeeSemanticsClassCount, ...]
    quoted_base_residual_net: float
    quoted_fee_separate_residual_net: float
    semantics_resolved: bool
    conclusion: str
    failures: tuple[RotationFeeSemanticsCorpusFailure, ...]
    position_reports: tuple[dict[str, Any], ...]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def build_rotation_fee_semantics_corpus(
    storage: Storage,
    *,
    pool_address: str,
    max_quote_age_seconds: int = 300,
) -> RotationFeeSemanticsCorpusReport:
    """
    Aggregate unresolved owner-flow hypotheses across one pool.

    This is evidence aggregation only. It never converts an exact-match pattern
    into a claim that the reported fee fields are income, cost, reward, or
    slippage.
    """
    if not pool_address.strip():
        raise ValueError("pool_address is required")
    if max_quote_age_seconds < 0:
        raise ValueError("max_quote_age_seconds cannot be negative")

    store = ResearchStore(str(storage.path))
    positions = store.transaction_event_position_addresses(
        event_type="Rebalancing",
        pool_address=pool_address,
    )
    if not positions:
        raise ValueError(
            f"no decoded Rebalancing positions for pool {pool_address}"
        )

    failures: list[RotationFeeSemanticsCorpusFailure] = []
    reports: list[Any] = []
    class_counts: dict[str, int] = {}
    transaction_signatures: set[str] = set()

    for position_address in positions:
        try:
            report = build_rotation_fee_semantics_report(
                storage,
                position_address=position_address,
                max_quote_age_seconds=max_quote_age_seconds,
            )
        except ValueError:
            failures.append(
                RotationFeeSemanticsCorpusFailure(
                    position_address=position_address,
                    category="POSITION_REPORT_UNAVAILABLE",
                )
            )
            continue
        except Exception:
            failures.append(
                RotationFeeSemanticsCorpusFailure(
                    position_address=position_address,
                    category="POSITION_REPORT_FAILED",
                )
            )
            continue

        sample_pools = {
            str(sample.pool_address)
            for sample in report.samples
            if sample.pool_address
        }
        if sample_pools != {pool_address}:
            failures.append(
                RotationFeeSemanticsCorpusFailure(
                    position_address=position_address,
                    category="POSITION_POOL_MISMATCH",
                )
            )
            continue

        reports.append(report)
        for sample in report.samples:
            transaction_signatures.add(str(sample.signature))
            if sample.eligible:
                class_counts[sample.evidence_class] = (
                    class_counts.get(sample.evidence_class, 0) + 1
                )

    eligible_transactions = sum(
        int(report.eligible_transactions)
        for report in reports
    )
    quote_eligible_transactions = sum(
        int(report.quote_eligible_transactions)
        for report in reports
    )
    claim_true = sum(
        int(report.claim_fee_true_samples)
        for report in reports
    )
    claim_false = sum(
        int(report.claim_fee_false_samples)
        for report in reports
    )
    claim_unknown = sum(
        1
        for report in reports
        for sample in report.samples
        if sample.eligible and sample.should_claim_fee is None
    )

    return RotationFeeSemanticsCorpusReport(
        pool_address=pool_address,
        quote_unit=DEFAULT_QUOTE_UNIT,
        positions_seen=len(positions),
        positions_reported=len(reports),
        positions_failed=len(failures),
        transactions_seen=len(transaction_signatures),
        eligible_transactions=eligible_transactions,
        quote_eligible_transactions=quote_eligible_transactions,
        quote_coverage_rate=(
            quote_eligible_transactions / eligible_transactions
            if eligible_transactions
            else 0.0
        ),
        claim_fee_true_samples=claim_true,
        claim_fee_false_samples=claim_false,
        claim_fee_unknown_samples=claim_unknown,
        evidence_class_counts=tuple(
            RotationFeeSemanticsClassCount(
                evidence_class=name,
                count=count,
            )
            for name, count in sorted(
                class_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        quoted_base_residual_net=float(sum(
            float(report.quoted_base_residual_net)
            for report in reports
        )),
        quoted_fee_separate_residual_net=float(sum(
            float(report.quoted_fee_separate_residual_net)
            for report in reports
        )),
        semantics_resolved=False,
        conclusion="UNRESOLVED_OBSERVATIONAL_CORPUS",
        failures=tuple(failures),
        position_reports=tuple(
            report.to_record() for report in reports
        ),
    )


def persist_rotation_fee_semantics_corpus(
    storage: Storage,
    *,
    report: RotationFeeSemanticsCorpusReport,
) -> int:
    if report.semantics_resolved:
        raise ValueError(
            "observational fee-semantics corpus cannot self-resolve semantics"
        )
    if report.conclusion != "UNRESOLVED_OBSERVATIONAL_CORPUS":
        raise ValueError(
            "unexpected conclusion for observational fee-semantics corpus"
        )
    return storage.save_advanced_edge_evidence(
        edge_type=ROTATION_FEE_SEMANTICS_CORPUS_EVIDENCE_TYPE,
        pool_address=report.pool_address,
        status=report.conclusion,
        qualified=False,
        evidence=report.to_record(),
    )
