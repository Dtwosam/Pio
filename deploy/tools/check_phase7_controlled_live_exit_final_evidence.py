from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EXIT_FINAL_EVIDENCE_RECHECK_V1"

POST_LABEL_AUDIT_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_exit_learning_label_post_audit.py"
)
PHASE7_EVIDENCE_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_evidence_status.py"
)
REVIEWED_SOURCE_BLOBS = {
    POST_LABEL_AUDIT_TOOL: "fa4cf17e9d8ea331c7090979ae85f7076a887944",
    PHASE7_EVIDENCE_TOOL: "77aa894be5d3e04534e521d159fa4743e759ef06",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_post_label_audit_sha256",
    "expected_post_label_audit_sha256",
    "phase7_evidence_status_sha256",
    "position_address",
    "opened_decision_id",
    "settlement_decision_id",
    "pio_database_path",
    "post_label_database_sha256",
    "evidence_database_sha256_before",
    "evidence_database_sha256_after",
    "phase6_promoted",
    "ledger_audit",
    "confirmed_receipts",
    "failed_receipts",
    "closed_positions",
    "open_positions",
    "distinct_closed_pools",
    "valued_closed_positions",
    "labeled_closed_positions",
    "phase7_promotion_ready",
    "phase7_reasons",
    "requires_additional_controlled_live_evidence",
    "exact_exit_position_valued_and_labeled",
    "phase7_evidence_recheck_ready",
    "requires_separate_phase7_promotion_action",
    "phase7_promotion_authorized",
    "phase7_promotion_persisted",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_repository_git_mutated",
    "production_pio_database_modified",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _git_blob_sha(path: Path) -> str:
    return _git_blob_sha_bytes(path.read_bytes())


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: str | Path, *, label: str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 7 EXIT evidence-recheck dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT evidence-recheck dependency mismatch: {relative}"
            )
    post_label = _load_module(
        source / POST_LABEL_AUDIT_TOOL,
        "phase7_exit_final_evidence_post_label",
    )
    evidence = _load_module(
        source / PHASE7_EVIDENCE_TOOL,
        "phase7_exit_final_evidence_status",
    )
    return post_label, evidence


def validate_exit_final_evidence_recheck(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT final evidence recheck must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"recheck_sha256"}:
        raise ValueError(
            "Phase 7 EXIT final evidence recheck schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT final evidence recheck format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT final evidence recheck type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "Phase 7 EXIT final evidence recheck lineage mismatch"
        )

    for field in (
        "saved_post_label_audit_sha256",
        "expected_post_label_audit_sha256",
        "phase7_evidence_status_sha256",
        "post_label_database_sha256",
        "evidence_database_sha256_before",
        "evidence_database_sha256_after",
        "recheck_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT final evidence recheck {field} is invalid"
            )

    if report["saved_post_label_audit_sha256"] != report[
        "expected_post_label_audit_sha256"
    ]:
        raise ValueError("post-label audit digest mismatch")
    if report["post_label_database_sha256"] != report[
        "evidence_database_sha256_before"
    ]:
        raise ValueError(
            "Phase 7 evidence recheck database differs from post-label audit"
        )
    if report["evidence_database_sha256_before"] != report[
        "evidence_database_sha256_after"
    ]:
        raise ValueError(
            "Phase 7 evidence recheck mutated production database"
        )

    for field in (
        "position_address",
        "opened_decision_id",
        "settlement_decision_id",
        "pio_database_path",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT final evidence recheck {field} is invalid"
            )

    if report.get("phase6_promoted") is not True:
        raise ValueError(
            "Phase 7 EXIT final evidence recheck requires Phase 6 promotion"
        )
    if not isinstance(report.get("ledger_audit"), dict):
        raise ValueError("Phase 7 EXIT evidence ledger audit is invalid")
    for field in (
        "confirmed_receipts",
        "failed_receipts",
        "closed_positions",
        "open_positions",
        "distinct_closed_pools",
        "valued_closed_positions",
        "labeled_closed_positions",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT final evidence recheck {field} is invalid"
            )

    if not isinstance(report.get("phase7_promotion_ready"), bool):
        raise ValueError("Phase 7 promotion-ready flag is invalid")
    reasons = report.get("phase7_reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("Phase 7 evidence reasons are invalid")
    if report["phase7_promotion_ready"] is not (len(reasons) == 0):
        raise ValueError(
            "Phase 7 promotion-ready/reasons binding mismatch"
        )
    if report["requires_additional_controlled_live_evidence"] is not (
        not report["phase7_promotion_ready"]
    ):
        raise ValueError(
            "Phase 7 additional-evidence flag mismatch"
        )

    if report["closed_positions"] <= 0:
        raise ValueError(
            "Phase 7 EXIT evidence recheck requires at least one closed position"
        )
    if report["valued_closed_positions"] > report["closed_positions"]:
        raise ValueError("valued closed-position count exceeds closed count")
    if report["labeled_closed_positions"] > report["closed_positions"]:
        raise ValueError("labeled closed-position count exceeds closed count")

    for field in (
        "exact_exit_position_valued_and_labeled",
        "phase7_evidence_recheck_ready",
        "requires_separate_phase7_promotion_action",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT final evidence recheck requires {field}=true"
            )

    for field in (
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT final evidence recheck requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["recheck_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError(
            "Phase 7 EXIT final evidence recheck digest mismatch"
        )


def build_exit_final_evidence_recheck(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_post_label_audit_path: str | Path,
    expected_post_label_audit_sha256: str,
    phase6_post_promotion_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    post_label_module, evidence_module = _load_reviewed(source)

    post_label = _load_json(
        saved_post_label_audit_path,
        label="saved Phase 7 EXIT learning-label post-audit",
    )
    post_label_module.validate_exit_learning_label_post_audit(post_label)
    if (
        not _is_hex_digest(expected_post_label_audit_sha256, 64)
        or post_label["audit_sha256"]
        != expected_post_label_audit_sha256
    ):
        raise ValueError("saved post-label audit digest mismatch")

    for field in (
        "target_state_matches_apply",
        "valuation_matches_apply",
        "learning_label_matches_apply",
        "learning_label_post_audit_ready",
        "phase7_evidence_status_recheck_required",
        "requires_separate_phase7_promotion_action",
    ):
        if post_label.get(field) is not True:
            raise ValueError(
                f"post-label audit lost required {field}"
            )
    if post_label.get("outcome_label_status") != "VALUED":
        raise ValueError("post-label audit outcome is not VALUED")
    if post_label.get("production_pio_database_modified") is not False:
        raise ValueError("post-label audit mutated production database")

    evidence = evidence_module.build_phase7_evidence_status(
        repository=repository,
        source_tree=source,
        phase6_post_promotion_audit_path=phase6_post_promotion_audit_path,
    )
    evidence_module.validate_phase7_evidence_status(evidence)

    if evidence["pio_database_path"] != post_label["pio_database_path"]:
        raise ValueError(
            "Phase 7 evidence database path differs from post-label audit"
        )
    if evidence["pio_database_sha256_before"] != post_label[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "Phase 7 evidence database differs from post-label audit"
        )
    if evidence.get("phase7_evidence_status_ready") is not True:
        raise ValueError("Phase 7 evidence status is not ready")
    if evidence.get("requires_separate_phase7_promotion_action") is not True:
        raise ValueError(
            "Phase 7 evidence status lost separate promotion boundary"
        )

    for field in (
        "phase7_promotion_persisted",
        "phase7_promotion_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if evidence.get(field) is not False:
            raise ValueError(
                f"Phase 7 evidence status unexpectedly sets {field}"
            )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "saved_post_label_audit_sha256": post_label["audit_sha256"],
        "expected_post_label_audit_sha256": (
            expected_post_label_audit_sha256
        ),
        "phase7_evidence_status_sha256": evidence[
            "phase7_evidence_status_sha256"
        ],
        "position_address": post_label["position_address"],
        "opened_decision_id": post_label["opened_decision_id"],
        "settlement_decision_id": post_label["settlement_decision_id"],
        "pio_database_path": post_label["pio_database_path"],
        "post_label_database_sha256": post_label[
            "pio_database_sha256_after"
        ],
        "evidence_database_sha256_before": evidence[
            "pio_database_sha256_before"
        ],
        "evidence_database_sha256_after": evidence[
            "pio_database_sha256_after"
        ],
        "phase6_promoted": evidence["phase6_promoted"],
        "ledger_audit": evidence["ledger_audit"],
        "confirmed_receipts": evidence["confirmed_receipts"],
        "failed_receipts": evidence["failed_receipts"],
        "closed_positions": evidence["closed_positions"],
        "open_positions": evidence["open_positions"],
        "distinct_closed_pools": evidence["distinct_closed_pools"],
        "valued_closed_positions": evidence["valued_closed_positions"],
        "labeled_closed_positions": evidence["labeled_closed_positions"],
        "phase7_promotion_ready": evidence["phase7_promotion_ready"],
        "phase7_reasons": evidence["phase7_reasons"],
        "requires_additional_controlled_live_evidence": evidence[
            "requires_additional_controlled_live_evidence"
        ],
        "exact_exit_position_valued_and_labeled": True,
        "phase7_evidence_recheck_ready": True,
        "requires_separate_phase7_promotion_action": True,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "recheck_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_final_evidence_recheck(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-evaluate reviewed Phase 7 controlled-LIVE evidence after the "
            "exact EXIT lifecycle is closed, valued and labeled. This recheck "
            "is snapshot-only and never authorizes or persists Phase 7 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-post-label-audit", required=True)
    parser.add_argument("--expected-post-label-audit-sha256", required=True)
    parser.add_argument("--phase6-post-promotion-audit", required=True)
    args = parser.parse_args()

    report = build_exit_final_evidence_recheck(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_post_label_audit_path=args.saved_post_label_audit,
        expected_post_label_audit_sha256=(
            args.expected_post_label_audit_sha256
        ),
        phase6_post_promotion_audit_path=args.phase6_post_promotion_audit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
