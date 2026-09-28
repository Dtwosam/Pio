from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE7_CONTROLLED_LIVE_EVIDENCE_PLAN_V1"

PHASE7_STATUS_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_evidence_status.py"
)
PHASE7_RUNBOOK = Path("docs/PHASE7_CONTROLLED_LIVE_RUNBOOK.md")
CONTROLLED_LIVE_MODULE = Path("rust-executor/src/controlled_live.rs")
CONTROLLED_LIVE_EXAMPLE = Path(
    "contracts/examples/controlled_live.example.json"
)

REVIEWED_SOURCE_BLOBS = {
    PHASE7_STATUS_TOOL: "77aa894be5d3e04534e521d159fa4743e759ef06",
    PHASE7_RUNBOOK: "929d6de8f5d3ffe23235e0e24ce3dc7141191f19",
    CONTROLLED_LIVE_MODULE: "5686a9578a3f6191d9fc1106cf75569c966dea69",
    CONTROLLED_LIVE_EXAMPLE: "3ae53b724d8d7460f4c1492f8cd82406fd2564d2",
}

REQUIRED_OPERATOR_INPUTS = (
    "production_controlled_live_config",
    "approved_pool_allowlist",
    "proposal_capital_quote",
    "daily_entry_capital_budget",
    "daily_entry_submission_budget",
    "daily_realized_loss_budget",
    "daily_drawdown_limit",
    "executor_wallet_identity",
)

PLAN_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "phase7_evidence_status_sha256",
    "phase6_post_promotion_audit_sha256",
    "phase7_criteria",
    "phase7_promotion_ready",
    "phase7_reasons",
    "current_evidence",
    "evidence_deficits",
    "ledger_clean",
    "historical_failed_receipts_blocked",
    "open_positions_require_exit_reconciliation",
    "ledger_reconciliation_required",
    "valuation_completion_required",
    "label_completion_required",
    "new_entry_evidence_candidate",
    "controlled_live_inputs_required",
    "required_operator_inputs",
    "example_config_disabled",
    "example_config_contains_placeholder",
    "example_config_production_ready",
    "smallest_practical_capital_required",
    "one_allowlisted_pool_initially_required",
    "exit_must_remain_available",
    "collection_plan_ready",
    "requires_separate_controlled_live_authorization",
    "requires_separate_transaction_authorization",
    "requires_separate_phase7_promotion_action",
    "phase7_promotion_persisted",
    "phase7_promotion_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "live_capital_authorized",
    "production_file_modified",
    "production_repository_git_mutated",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _git_blob_sha_bytes(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _git_blob_sha(path: Path) -> str:
    return _git_blob_sha_bytes(path.read_bytes())


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


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _load_status_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Phase 7 evidence-plan dependency missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 evidence-plan dependency mismatch: {relative}"
            )
    return _load_module(
        source / PHASE7_STATUS_TOOL,
        "phase7_controlled_live_evidence_plan_status",
    )


def _validate_example(source: Path) -> dict[str, Any]:
    example = _load_json(
        source / CONTROLLED_LIVE_EXAMPLE,
        label="reviewed controlled-LIVE example",
    )
    expected_fields = {
        "enabled",
        "allowed_pool_addresses",
        "max_open_positions",
        "max_rebalances_per_position",
        "max_capital_quote_per_entry",
        "max_daily_entry_capital_quote",
        "max_daily_entry_submissions",
        "max_daily_realized_loss_quote",
        "max_daily_drawdown_pct",
        "allow_rebalance",
        "allow_exit",
    }
    if set(example) != expected_fields:
        raise ValueError("reviewed controlled-LIVE example schema drifted")
    if example.get("enabled") is not False:
        raise ValueError("reviewed controlled-LIVE example must remain disabled")
    if example.get("allowed_pool_addresses") != [
        "REPLACE_WITH_APPROVED_POOL"
    ]:
        raise ValueError(
            "reviewed controlled-LIVE example must retain placeholder pool"
        )
    if example.get("max_open_positions") != 1:
        raise ValueError("reviewed controlled-LIVE example must cap one position")
    if example.get("allow_exit") is not True:
        raise ValueError("reviewed controlled-LIVE example must preserve EXIT")
    return example


def _nonnegative_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"Phase 7 evidence-plan {label} is invalid")
    return value


def _derive(status: dict[str, Any]) -> dict[str, Any]:
    criteria = status["phase7_criteria"]
    ledger = status["ledger_audit"]

    current = {
        "confirmed_receipts": _nonnegative_int(
            status.get("confirmed_receipts"),
            label="confirmed_receipts",
        ),
        "failed_receipts": _nonnegative_int(
            status.get("failed_receipts"),
            label="failed_receipts",
        ),
        "closed_positions": _nonnegative_int(
            status.get("closed_positions"),
            label="closed_positions",
        ),
        "open_positions": _nonnegative_int(
            status.get("open_positions"),
            label="open_positions",
        ),
        "distinct_closed_pools": _nonnegative_int(
            status.get("distinct_closed_pools"),
            label="distinct_closed_pools",
        ),
        "valued_closed_positions": _nonnegative_int(
            status.get("valued_closed_positions"),
            label="valued_closed_positions",
        ),
        "labeled_closed_positions": _nonnegative_int(
            status.get("labeled_closed_positions"),
            label="labeled_closed_positions",
        ),
    }

    deficits = {
        "confirmed_receipts": max(
            0,
            int(criteria["min_confirmed_receipts"])
            - current["confirmed_receipts"],
        ),
        "closed_positions": max(
            0,
            int(criteria["min_closed_positions"])
            - current["closed_positions"],
        ),
        "distinct_closed_pools": max(
            0,
            int(criteria["min_distinct_pools"])
            - current["distinct_closed_pools"],
        ),
        "failed_receipts_excess": max(
            0,
            current["failed_receipts"]
            - int(criteria["max_failed_receipts"]),
        ),
        "open_positions_excess": max(
            0,
            current["open_positions"]
            - int(criteria["max_open_positions_at_validation"]),
        ),
        "unvalued_closed_positions": max(
            0,
            current["closed_positions"]
            - current["valued_closed_positions"],
        ),
        "unlabeled_closed_positions": max(
            0,
            current["closed_positions"]
            - current["labeled_closed_positions"],
        ),
    }

    ledger_clean = ledger.get("clean") is True
    failed_blocked = deficits["failed_receipts_excess"] > 0
    open_blocked = deficits["open_positions_excess"] > 0
    valuation_required = deficits["unvalued_closed_positions"] > 0
    label_required = deficits["unlabeled_closed_positions"] > 0
    collection_debt = bool(
        deficits["confirmed_receipts"]
        or deficits["closed_positions"]
        or deficits["distinct_closed_pools"]
    )

    new_entry_candidate = bool(
        not status["phase7_promotion_ready"]
        and status["phase6_promoted"]
        and ledger_clean
        and not failed_blocked
        and not open_blocked
        and not valuation_required
        and not label_required
        and collection_debt
    )

    return {
        "current": current,
        "deficits": deficits,
        "ledger_clean": ledger_clean,
        "historical_failed_receipts_blocked": failed_blocked,
        "open_positions_require_exit_reconciliation": open_blocked,
        "ledger_reconciliation_required": not ledger_clean,
        "valuation_completion_required": valuation_required,
        "label_completion_required": label_required,
        "new_entry_evidence_candidate": new_entry_candidate,
    }


def validate_phase7_evidence_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("Phase 7 evidence plan must be a JSON object")
    if set(plan) != set(PLAN_FIELDS) | {"plan_sha256"}:
        raise ValueError("Phase 7 evidence plan schema mismatch")
    if plan.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 7 evidence plan format")
    if plan.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 7 evidence plan type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if plan.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 7 evidence plan lineage mismatch")

    for field in (
        "phase7_evidence_status_sha256",
        "phase6_post_promotion_audit_sha256",
        "plan_sha256",
    ):
        if not _is_hex_digest(plan.get(field), 64):
            raise ValueError(f"Phase 7 evidence plan {field} is invalid")

    if not isinstance(plan.get("phase7_promotion_ready"), bool):
        raise ValueError("Phase 7 evidence plan promotion-ready flag invalid")
    reasons = plan.get("phase7_reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("Phase 7 evidence plan reasons are invalid")
    if plan["phase7_promotion_ready"] is not (len(reasons) == 0):
        raise ValueError("Phase 7 evidence plan promotion/reasons mismatch")

    current = plan.get("current_evidence")
    deficits = plan.get("evidence_deficits")
    if not isinstance(current, dict) or not isinstance(deficits, dict):
        raise ValueError("Phase 7 evidence plan evidence blocks invalid")
    for field in (
        "confirmed_receipts",
        "failed_receipts",
        "closed_positions",
        "open_positions",
        "distinct_closed_pools",
        "valued_closed_positions",
        "labeled_closed_positions",
    ):
        _nonnegative_int(current.get(field), label=field)
    for field in (
        "confirmed_receipts",
        "closed_positions",
        "distinct_closed_pools",
        "failed_receipts_excess",
        "open_positions_excess",
        "unvalued_closed_positions",
        "unlabeled_closed_positions",
    ):
        _nonnegative_int(deficits.get(field), label=f"deficit.{field}")

    for field in (
        "ledger_clean",
        "historical_failed_receipts_blocked",
        "open_positions_require_exit_reconciliation",
        "ledger_reconciliation_required",
        "valuation_completion_required",
        "label_completion_required",
        "new_entry_evidence_candidate",
        "controlled_live_inputs_required",
        "example_config_disabled",
        "example_config_contains_placeholder",
        "example_config_production_ready",
        "smallest_practical_capital_required",
        "one_allowlisted_pool_initially_required",
        "exit_must_remain_available",
        "collection_plan_ready",
        "requires_separate_controlled_live_authorization",
        "requires_separate_transaction_authorization",
        "requires_separate_phase7_promotion_action",
    ):
        if not isinstance(plan.get(field), bool):
            raise ValueError(f"Phase 7 evidence plan {field} must be boolean")

    if plan.get("required_operator_inputs") != list(REQUIRED_OPERATOR_INPUTS):
        raise ValueError("Phase 7 evidence plan operator-input list mismatch")
    if plan.get("example_config_disabled") is not True:
        raise ValueError("Phase 7 example config must remain disabled")
    if plan.get("example_config_contains_placeholder") is not True:
        raise ValueError("Phase 7 example config must retain placeholder")
    if plan.get("example_config_production_ready") is not False:
        raise ValueError("Phase 7 example config cannot be production-ready")
    for field in (
        "smallest_practical_capital_required",
        "one_allowlisted_pool_initially_required",
        "exit_must_remain_available",
        "collection_plan_ready",
        "requires_separate_controlled_live_authorization",
        "requires_separate_transaction_authorization",
        "requires_separate_phase7_promotion_action",
    ):
        if plan.get(field) is not True:
            raise ValueError(f"Phase 7 evidence plan requires {field}=true")

    if plan["controlled_live_inputs_required"] is not plan[
        "new_entry_evidence_candidate"
    ]:
        raise ValueError("Phase 7 evidence plan input requirement mismatch")

    for field in (
        "phase7_promotion_persisted",
        "phase7_promotion_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
    ):
        if plan.get(field) is not False:
            raise ValueError(f"Phase 7 evidence plan requires {field}=false")

    identity = {field: plan[field] for field in PLAN_FIELDS}
    expected = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if plan["plan_sha256"] != expected:
        raise ValueError("Phase 7 evidence plan digest mismatch")


def build_phase7_evidence_plan(
    *,
    source_tree: str | Path,
    phase7_evidence_status_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    status_module = _load_status_module(source)
    _validate_example(source)

    status = _load_json(
        phase7_evidence_status_path,
        label="Phase 7 evidence status",
    )
    status_module.validate_phase7_evidence_status(status)

    if status.get("phase7_evidence_status_ready") is not True:
        raise ValueError("Phase 7 evidence status is not ready")
    if status.get("phase6_promoted") is not True:
        raise ValueError("Phase 7 evidence lost persisted Phase 6 promotion")
    for field in (
        "phase7_promotion_persisted",
        "phase7_promotion_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "live_capital_authorized",
    ):
        if status.get(field) is not False:
            raise ValueError(f"Phase 7 evidence unexpectedly authorizes {field}")

    derived = _derive(status)

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
        "phase7_evidence_status_sha256": status[
            "phase7_evidence_status_sha256"
        ],
        "phase6_post_promotion_audit_sha256": status[
            "phase6_post_promotion_audit_sha256"
        ],
        "phase7_criteria": status["phase7_criteria"],
        "phase7_promotion_ready": bool(status["phase7_promotion_ready"]),
        "phase7_reasons": list(status["phase7_reasons"]),
        "current_evidence": derived["current"],
        "evidence_deficits": derived["deficits"],
        "ledger_clean": derived["ledger_clean"],
        "historical_failed_receipts_blocked": derived[
            "historical_failed_receipts_blocked"
        ],
        "open_positions_require_exit_reconciliation": derived[
            "open_positions_require_exit_reconciliation"
        ],
        "ledger_reconciliation_required": derived[
            "ledger_reconciliation_required"
        ],
        "valuation_completion_required": derived[
            "valuation_completion_required"
        ],
        "label_completion_required": derived[
            "label_completion_required"
        ],
        "new_entry_evidence_candidate": derived[
            "new_entry_evidence_candidate"
        ],
        "controlled_live_inputs_required": derived[
            "new_entry_evidence_candidate"
        ],
        "required_operator_inputs": list(REQUIRED_OPERATOR_INPUTS),
        "example_config_disabled": True,
        "example_config_contains_placeholder": True,
        "example_config_production_ready": False,
        "smallest_practical_capital_required": True,
        "one_allowlisted_pool_initially_required": True,
        "exit_must_remain_available": True,
        "collection_plan_ready": True,
        "requires_separate_controlled_live_authorization": True,
        "requires_separate_transaction_authorization": True,
        "requires_separate_phase7_promotion_action": True,
        "phase7_promotion_persisted": False,
        "phase7_promotion_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "live_capital_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
    }
    plan = {
        **identity,
        "plan_sha256": hashlib.sha256(
            _canonical_bytes(identity)
        ).hexdigest(),
    }
    validate_phase7_evidence_plan(plan)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Turn a reviewed Phase 7 controlled-LIVE evidence status into a "
            "non-authorizing evidence plan. The plan identifies reconciliation "
            "or evidence debt and never invents pool, capital, budget, wallet, "
            "signing, submission, or live-capital inputs."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase7-evidence-status", required=True)
    args = parser.parse_args()

    plan = build_phase7_evidence_plan(
        source_tree=args.source_tree,
        phase7_evidence_status_path=args.phase7_evidence_status,
    )
    print(json.dumps(plan, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
