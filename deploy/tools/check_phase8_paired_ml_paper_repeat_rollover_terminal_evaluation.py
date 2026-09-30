from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_TERMINAL_EVALUATION_V1"

POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_paired_ml_paper_repeat_rollover_continuation_evidence_tick_post_audit.py"
)
CONTINUOUS_PROMOTION_MODULE = Path(
    "python-learner/src/meteora_learner/continuous_promotion.py"
)
REVIEWED_SOURCE_BLOBS = {
    POST_AUDIT_TOOL: "14ebf0d181dd50ac1b598208fd3e268a75d8b181",
    CONTINUOUS_PROMOTION_MODULE: "6ae4acd70be4e03e7e2b2f5c410e37098b4b8bc6",
}

DEBT_PAIR_SETTLEMENT = "PAPER_PAIR_SETTLEMENT_REQUIRED"
DEBT_MORE_EVIDENCE = "PAPER_CHALLENGER_EVIDENCE_REQUIRED"
DEBT_PROMOTION_REVIEW = "PAPER_CHALLENGER_PROMOTION_REVIEW_REQUIRED"
DEBT_VALIDATION_REVIEW = "PAPER_CHALLENGER_VALIDATION_REVIEW_REQUIRED"

ROUTE_PAIR_SETTLEMENT = (
    "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_TERMINAL_SETTLEMENT_REVIEW"
)
ROUTE_NEXT_PAIR = "PHASE8_PAIRED_ML_PAPER_REPEAT_ROLLOVER_NEXT_PAIR_REVIEW"
ROUTE_PROMOTION = "PHASE8_CONTINUOUS_CHALLENGER_PROMOTION_REVIEW"
ROUTE_VALIDATION = "PHASE8_CONTINUOUS_CHALLENGER_VALIDATION_REVIEW"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_terminal_post_audit_sha256",
    "source_rollover_post_audit_sha256",
    "rollover_entry_request_sha256",
    "rollover_entry_input_verification_sha256",
    "source_final_evaluation_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "active_cycle_id",
    "incumbent_model_id",
    "challenger_model_id",
    "account_id",
    "previous_pair_id",
    "pair_id",
    "pool_address",
    "entry_observed_at",
    "terminal_tick_observed_at",
    "incumbent_position_id",
    "challenger_position_id",
    "incumbent_position_status",
    "challenger_position_status",
    "pair_closed_count",
    "pair_both_closed",
    "pair_has_open_remainder",
    "continuous_validation",
    "continuous_validation_sha256",
    "incumbent_closed_trades",
    "challenger_closed_trades",
    "required_incumbent_closed_trades",
    "required_challenger_closed_trades",
    "incumbent_closed_trades_remaining",
    "challenger_closed_trades_remaining",
    "closed_trade_floor_met",
    "continuous_validation_qualified",
    "continuous_validation_reasons",
    "next_debt_type",
    "next_scope",
    "continuation_route",
    "pair_settlement_review_ready",
    "next_pair_review_ready",
    "promotion_review_ready",
    "validation_review_ready",
    "separate_next_action_authorization_required",
    "read_only",
    "paper_pair_settlement_authorized",
    "new_pair_entry_authorized",
    "continuous_promotion_authorized",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "phase8_promotion_authorized",
    "production_source_file_modified",
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


def _load_reviewed(source: Path) -> tuple[Any, Any]:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"paired PAPER terminal evaluation dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"paired PAPER terminal evaluation dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / POST_AUDIT_TOOL,
            "phase8_rollover_paired_paper_terminal_post_audit",
        ),
        _load_module(
            source / CONTINUOUS_PROMOTION_MODULE,
            "meteora_learner.phase8_paired_paper_terminal_promotion",
        ),
    )


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


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    elif is_dataclass(value):
        result = asdict(value)
    elif isinstance(value, dict):
        result = value
    else:
        raise ValueError("paired PAPER terminal validation is not recordable")
    if not isinstance(result, dict):
        raise ValueError("paired PAPER terminal validation record is invalid")
    return result


def validate_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("paired PAPER terminal evaluation must be an object")
    if set(report) != set(REPORT_FIELDS) | {"evaluation_sha256"}:
        raise ValueError("paired PAPER terminal evaluation schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported paired PAPER terminal evaluation format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected paired PAPER terminal evaluation type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("paired PAPER terminal evaluation lineage mismatch")

    for field in (
        "source_terminal_post_audit_sha256",
        "source_rollover_post_audit_sha256",
        "rollover_entry_request_sha256",
        "rollover_entry_input_verification_sha256",
        "source_final_evaluation_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "continuous_validation_sha256",
        "evaluation_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"paired PAPER terminal evaluation {field} is invalid"
            )
    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"paired PAPER terminal evaluation {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "active_cycle_id",
        "incumbent_model_id",
        "challenger_model_id",
        "account_id",
        "previous_pair_id",
        "pair_id",
        "pool_address",
        "entry_observed_at",
        "terminal_tick_observed_at",
        "incumbent_position_id",
        "challenger_position_id",
        "incumbent_position_status",
        "challenger_position_status",
        "next_debt_type",
        "next_scope",
        "continuation_route",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"paired PAPER terminal evaluation {field} is invalid"
            )

    if not isinstance(report.get("continuous_validation"), dict):
        raise ValueError(
            "paired PAPER terminal evaluation validation record is invalid"
        )
    if not isinstance(report.get("continuous_validation_reasons"), list):
        raise ValueError(
            "paired PAPER terminal evaluation reasons are invalid"
        )

    if report["pair_closed_count"] not in {1, 2}:
        raise ValueError(
            "paired PAPER terminal evaluation requires one or two closed positions"
        )
    if report["next_scope"] != report["challenger_model_id"]:
        raise ValueError("paired PAPER terminal evaluation next scope mismatch")
    if report["incumbent_closed_trades_remaining"] != max(
        0,
        report["required_incumbent_closed_trades"]
        - report["incumbent_closed_trades"],
    ):
        raise ValueError(
            "paired PAPER terminal evaluation incumbent remaining mismatch"
        )
    if report["challenger_closed_trades_remaining"] != max(
        0,
        report["required_challenger_closed_trades"]
        - report["challenger_closed_trades"],
    ):
        raise ValueError(
            "paired PAPER terminal evaluation challenger remaining mismatch"
        )
    expected_floor = (
        report["incumbent_closed_trades"]
        >= report["required_incumbent_closed_trades"]
        and report["challenger_closed_trades"]
        >= report["required_challenger_closed_trades"]
    )
    if report["closed_trade_floor_met"] is not expected_floor:
        raise ValueError(
            "paired PAPER terminal evaluation closed-trade floor mismatch"
        )

    if report["pair_has_open_remainder"]:
        if report["pair_both_closed"] is not False:
            raise ValueError(
                "paired PAPER terminal evaluation pair settlement state mismatch"
            )
        if report["pair_closed_count"] != 1:
            raise ValueError(
                "paired PAPER terminal evaluation open remainder requires one closed position"
            )
        if report["next_debt_type"] != DEBT_PAIR_SETTLEMENT:
            raise ValueError(
                "paired PAPER terminal evaluation settlement debt mismatch"
            )
        if report["continuation_route"] != ROUTE_PAIR_SETTLEMENT:
            raise ValueError(
                "paired PAPER terminal evaluation settlement route mismatch"
            )
        if report["pair_settlement_review_ready"] is not True:
            raise ValueError(
                "paired PAPER terminal evaluation settlement review not ready"
            )
        for field in (
            "next_pair_review_ready",
            "promotion_review_ready",
            "validation_review_ready",
        ):
            if report[field] is not False:
                raise ValueError(
                    f"paired PAPER terminal evaluation requires {field}=false"
                )
    else:
        if report["pair_both_closed"] is not True:
            raise ValueError(
                "paired PAPER terminal evaluation requires fully closed pair"
            )
        if report["pair_closed_count"] != 2:
            raise ValueError(
                "paired PAPER terminal evaluation closed pair count mismatch"
            )
        if not report["closed_trade_floor_met"]:
            if report["next_debt_type"] != DEBT_MORE_EVIDENCE:
                raise ValueError(
                    "paired PAPER terminal evaluation more-evidence debt mismatch"
                )
            if report["continuation_route"] != ROUTE_NEXT_PAIR:
                raise ValueError(
                    "paired PAPER terminal evaluation next-pair route mismatch"
                )
            if report["next_pair_review_ready"] is not True:
                raise ValueError(
                    "paired PAPER terminal evaluation next-pair review not ready"
                )
            if (
                report["promotion_review_ready"]
                or report["validation_review_ready"]
                or report["pair_settlement_review_ready"]
            ):
                raise ValueError(
                    "paired PAPER terminal evaluation mixed next-pair route"
                )
        elif report["continuous_validation_qualified"]:
            if report["next_debt_type"] != DEBT_PROMOTION_REVIEW:
                raise ValueError(
                    "paired PAPER terminal evaluation promotion debt mismatch"
                )
            if report["continuation_route"] != ROUTE_PROMOTION:
                raise ValueError(
                    "paired PAPER terminal evaluation promotion route mismatch"
                )
            if report["promotion_review_ready"] is not True:
                raise ValueError(
                    "paired PAPER terminal evaluation promotion review not ready"
                )
            if (
                report["next_pair_review_ready"]
                or report["validation_review_ready"]
                or report["pair_settlement_review_ready"]
            ):
                raise ValueError(
                    "paired PAPER terminal evaluation mixed promotion route"
                )
        else:
            if report["next_debt_type"] != DEBT_VALIDATION_REVIEW:
                raise ValueError(
                    "paired PAPER terminal evaluation validation debt mismatch"
                )
            if report["continuation_route"] != ROUTE_VALIDATION:
                raise ValueError(
                    "paired PAPER terminal evaluation validation route mismatch"
                )
            if report["validation_review_ready"] is not True:
                raise ValueError(
                    "paired PAPER terminal evaluation validation review not ready"
                )
            if (
                report["next_pair_review_ready"]
                or report["promotion_review_ready"]
                or report["pair_settlement_review_ready"]
            ):
                raise ValueError(
                    "paired PAPER terminal evaluation mixed validation route"
                )

    for field in (
        "separate_next_action_authorization_required",
        "read_only",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"paired PAPER terminal evaluation requires {field}=true"
            )

    for field in (
        "paper_pair_settlement_authorized",
        "new_pair_entry_authorized",
        "continuous_promotion_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "phase8_promotion_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"paired PAPER terminal evaluation requires {field}=false"
            )

    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError("paired PAPER terminal evaluation modified database")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("paired PAPER terminal evaluation modified WAL")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("paired PAPER terminal evaluation modified SHM")

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["evaluation_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("paired PAPER terminal evaluation digest mismatch")


def build_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation(
    *,
    repository: str | Path,
    source_tree: str | Path,
    terminal_post_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    audit_module, promotion_module = _load_reviewed(source)
    audit = _load_json(
        terminal_post_audit_path,
        label="repeat rollover paired PAPER terminal post-tick audit",
    )
    audit_module.validate_phase8_paired_ml_paper_repeat_rollover_continuation_evidence_tick_post_audit(
        audit
    )
    if audit.get("terminal_pair_evaluation_ready") is not True:
        raise ValueError("paired PAPER post-tick audit is not terminal-ready")
    if audit.get("tick_recovery_review_ready") is not False:
        raise ValueError("paired PAPER terminal evaluation cannot consume recovery audit")
    if audit.get("pair_any_closed") is not True:
        raise ValueError("paired PAPER terminal evaluation requires a closed position")
    if audit.get("continuation_route") != audit_module.ROUTE_TERMINAL:
        raise ValueError("paired PAPER post-tick audit terminal route mismatch")
    if audit["pair_id"] == audit["previous_pair_id"]:
        raise ValueError(
            "repeat rollover paired PAPER terminal pair id was not advanced"
        )

    executor, _ = audit_module._load_reviewed(source)
    readiness = executor._load_readiness(source)
    supervision_module, _, _, _, _ = readiness._load_reviewed(source)
    database = supervision_module._database_path(production)
    if Path(str(audit["pio_database_path"])).resolve() != database:
        raise ValueError("paired PAPER terminal evaluation database binding mismatch")
    before = supervision_module._database_state(database)
    expected = {
        "database": audit["audit_database_sha256_after"],
        "wal": audit["audit_wal_sha256_after"],
        "shm": audit["audit_shm_sha256_after"],
    }
    if before != expected:
        raise ValueError(
            "Pio database changed after paired PAPER terminal post-audit"
        )

    positions = audit["fresh_positions"]
    incumbent_position = positions.get(audit["incumbent_position_id"])
    challenger_position = positions.get(audit["challenger_position_id"])
    if not isinstance(incumbent_position, dict) or not isinstance(
        challenger_position, dict
    ):
        raise ValueError("paired PAPER terminal positions are incomplete")
    incumbent_status = str(incumbent_position.get("status"))
    challenger_status = str(challenger_position.get("status"))
    if incumbent_status not in {"OPEN", "CLOSED"}:
        raise ValueError("paired PAPER incumbent terminal status is invalid")
    if challenger_status not in {"OPEN", "CLOSED"}:
        raise ValueError("paired PAPER challenger terminal status is invalid")
    closed_count = int(incumbent_status == "CLOSED") + int(
        challenger_status == "CLOSED"
    )
    if closed_count == 0:
        raise ValueError("paired PAPER terminal evaluation has no closed side")

    criteria = promotion_module.ContinuousChampionCriteria()
    validation = promotion_module.evaluate_continuous_champion(
        promotion_module.Storage(database),
        cycle_id=audit["active_cycle_id"],
        account_id=audit["account_id"],
        criteria=criteria,
    )
    validation_record = _record(validation)
    if validation.policy_actionable is not False:
        raise ValueError(
            "paired PAPER terminal validation became policy-actionable"
        )
    if validation.incumbent_model_id != audit["incumbent_model_id"]:
        raise ValueError("paired PAPER terminal incumbent model changed")
    if validation.challenger_model_id != audit["challenger_model_id"]:
        raise ValueError("paired PAPER terminal challenger model changed")

    incumbent_closed = int(validation.incumbent.closed_trades)
    challenger_closed = int(validation.challenger.closed_trades)
    required_incumbent = int(criteria.min_incumbent_closed_trades)
    required_challenger = int(criteria.min_challenger_closed_trades)
    floor_met = (
        incumbent_closed >= required_incumbent
        and challenger_closed >= required_challenger
    )
    both_closed = closed_count == 2
    open_remainder = closed_count == 1

    if open_remainder:
        next_debt = DEBT_PAIR_SETTLEMENT
        route = ROUTE_PAIR_SETTLEMENT
        settlement_ready = True
        next_pair_ready = False
        promotion_ready = False
        validation_ready = False
    elif not floor_met:
        next_debt = DEBT_MORE_EVIDENCE
        route = ROUTE_NEXT_PAIR
        settlement_ready = False
        next_pair_ready = True
        promotion_ready = False
        validation_ready = False
    elif validation.qualified:
        next_debt = DEBT_PROMOTION_REVIEW
        route = ROUTE_PROMOTION
        settlement_ready = False
        next_pair_ready = False
        promotion_ready = True
        validation_ready = False
    else:
        next_debt = DEBT_VALIDATION_REVIEW
        route = ROUTE_VALIDATION
        settlement_ready = False
        next_pair_ready = False
        promotion_ready = False
        validation_ready = True

    after = supervision_module._database_state(database)
    if after != before:
        raise ValueError(
            "paired PAPER terminal evaluation changed production database"
        )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(), key=lambda item: str(item[0])
            )
        },
        "source_terminal_post_audit_sha256": audit["post_audit_sha256"],
        "source_rollover_post_audit_sha256": audit[
            "source_rollover_post_audit_sha256"
        ],
        "rollover_entry_request_sha256": audit[
            "rollover_entry_request_sha256"
        ],
        "rollover_entry_input_verification_sha256": audit[
            "rollover_entry_input_verification_sha256"
        ],
        "source_final_evaluation_sha256": audit[
            "source_final_evaluation_sha256"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "active_cycle_id": audit["active_cycle_id"],
        "incumbent_model_id": audit["incumbent_model_id"],
        "challenger_model_id": audit["challenger_model_id"],
        "account_id": audit["account_id"],
        "previous_pair_id": audit["previous_pair_id"],
        "pair_id": audit["pair_id"],
        "pool_address": audit["pool_address"],
        "entry_observed_at": audit["entry_observed_at"],
        "terminal_tick_observed_at": audit["target_chain_observed_at"],
        "incumbent_position_id": audit["incumbent_position_id"],
        "challenger_position_id": audit["challenger_position_id"],
        "incumbent_position_status": incumbent_status,
        "challenger_position_status": challenger_status,
        "pair_closed_count": closed_count,
        "pair_both_closed": both_closed,
        "pair_has_open_remainder": open_remainder,
        "continuous_validation": validation_record,
        "continuous_validation_sha256": _sha256_bytes(
            _canonical_bytes(validation_record)
        ),
        "incumbent_closed_trades": incumbent_closed,
        "challenger_closed_trades": challenger_closed,
        "required_incumbent_closed_trades": required_incumbent,
        "required_challenger_closed_trades": required_challenger,
        "incumbent_closed_trades_remaining": max(
            0, required_incumbent - incumbent_closed
        ),
        "challenger_closed_trades_remaining": max(
            0, required_challenger - challenger_closed
        ),
        "closed_trade_floor_met": floor_met,
        "continuous_validation_qualified": bool(validation.qualified),
        "continuous_validation_reasons": list(validation.reasons),
        "next_debt_type": next_debt,
        "next_scope": audit["challenger_model_id"],
        "continuation_route": route,
        "pair_settlement_review_ready": settlement_ready,
        "next_pair_review_ready": next_pair_ready,
        "promotion_review_ready": promotion_ready,
        "validation_review_ready": validation_ready,
        "separate_next_action_authorization_required": True,
        "read_only": True,
        "paper_pair_settlement_authorized": False,
        "new_pair_entry_authorized": False,
        "continuous_promotion_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_promotion_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "evaluation_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a terminal repeat rollover paired Phase 8 PAPER result without mutating "
            "production. If only one side is closed, route to pair settlement. "
            "If both sides are closed, evaluate the existing continuous "
            "20-vs-20 PAPER qualification criteria and route to another fair "
            "pair, promotion review, or validation review. No promotion occurs."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--terminal-post-audit", required=True)
    args = parser.parse_args()

    report = build_phase8_paired_ml_paper_repeat_rollover_terminal_evaluation(
        repository=args.repo,
        source_tree=args.source_tree,
        terminal_post_audit_path=args.terminal_post_audit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
