from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_EVIDENCE_HANDOFF_V1"
)

BUNDLE_TOOL = Path(
    "deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle.py"
)
POST_AUDIT_TOOL = Path(
    "deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_post_audit.py"
)
REVIEWED_SOURCE_BLOBS = {
    BUNDLE_TOOL: "99be522c3a9c9e71b8d6657b6a23133b1a4bef0f",
    POST_AUDIT_TOOL: "829806d8529d30edc06a7ca444ce2ffdbdd8f4ce",
}

STATE_CONTINUE = "CONTINUE"
STATE_TERMINAL = "TERMINAL"
STATE_RECOVERY = "RECOVERY"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_evidence_bundle_sha256",
    "source_post_audit_sha256",
    "source_execution_receipt_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "bundle_database_sha256",
    "bundle_wal_sha256",
    "bundle_shm_sha256",
    "pair_lineage_sha256",
    "pair_id",
    "previous_pair_id",
    "expected_run_id",
    "continuation_route",
    "handoff_state",
    "bundle_post_audit_binding_verified",
    "bundle_route_binding_verified",
    "pair_lineage_verified",
    "current_database_matches_bundle",
    "database_stable_during_handoff",
    "evidence_handoff_ready",
    "continuation_handoff_ready",
    "terminal_handoff_ready",
    "recovery_handoff_ready",
    "handoff_read_only",
    "requires_separate_next_action_authorization",
    "requires_fresh_operator_preflight_for_future_sequence",
    "future_checkpoint_refresh_authorized",
    "paper_supervisor_tick_authorized",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "live_submit_authorized",
    "transaction_submission_authorized",
    "new_live_capital_authorized",
    "continuous_promotion_authorized",
    "phase8_promotion_authorized",
    "production_file_modified",
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


def _is_hex_digest(value: Any, length: int = 64) -> bool:
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
                f"reviewed checkpoint v25 handoff dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"reviewed checkpoint v25 handoff dependency mismatch: {relative}"
            )
    return (
        _load_module(
            source / BUNDLE_TOOL,
            "phase8_recursive_reentry_checkpoint_v25_handoff_bundle",
        ),
        _load_module(
            source / POST_AUDIT_TOOL,
            "phase8_recursive_reentry_checkpoint_v25_handoff_post_audit",
        ),
    )


def _load_json(path: str | Path, *, label: str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"unsafe Pio database state file: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _resolve_production_database(repository: str | Path) -> tuple[Path, Path]:
    production = Path(repository).expanduser()
    if production.is_symlink():
        raise ValueError("production repository must not be a symlink")
    production = production.resolve(strict=True)
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    candidate = production / "data" / "pio.db"
    try:
        st = os.lstat(candidate)
    except FileNotFoundError as exc:
        raise ValueError("production Pio database is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"unsafe Pio database state file: {candidate}")
    database = candidate.resolve(strict=True)
    return production, database


def _pair_lineage(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_pair_entry_post_audit_sha256": audit[
            "source_pair_entry_post_audit_sha256"
        ],
        "pair_entry_request_sha256": audit[
            "pair_entry_request_sha256"
        ],
        "pair_entry_input_verification_sha256": audit[
            "pair_entry_input_verification_sha256"
        ],
        "source_final_evaluation_sha256": audit[
            "source_final_evaluation_sha256"
        ],
        "active_cycle_id": audit["active_cycle_id"],
        "incumbent_model_id": audit["incumbent_model_id"],
        "challenger_model_id": audit["challenger_model_id"],
        "account_id": audit["account_id"],
        "previous_pair_id": audit["previous_pair_id"],
        "pair_id": audit["pair_id"],
        "pool_address": audit["pool_address"],
        "entry_observed_at": audit["entry_observed_at"],
        "incumbent_position_id": audit["incumbent_position_id"],
        "challenger_position_id": audit["challenger_position_id"],
    }


def _route_state(audit: dict[str, Any]) -> str:
    flags = (
        audit.get("next_evidence_tick_review_ready") is True,
        audit.get("terminal_pair_evaluation_ready") is True,
        audit.get("tick_recovery_review_ready") is True,
    )
    if sum(bool(value) for value in flags) != 1:
        raise ValueError("checkpoint v25 handoff post-audit route is ambiguous")
    if flags[0]:
        return STATE_CONTINUE
    if flags[1]:
        return STATE_TERMINAL
    return STATE_RECOVERY


def _validate_bundle_and_audit_bindings(
    bundle: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    for field in (
        "database_matches_final_audit",
        "lineage_verified",
        "all_native_validators_passed",
        "bundle_read_only",
        "requires_separate_next_action_authorization",
    ):
        if bundle.get(field) is not True:
            raise ValueError(
                f"checkpoint v25 handoff requires bundle {field}=true"
            )
    for field in (
        "paper_supervisor_tick_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if bundle.get(field) is not False:
            raise ValueError(
                f"checkpoint v25 handoff refuses bundle {field}=true"
            )

    if bundle["post_audit_sha256"] != audit["post_audit_sha256"]:
        raise ValueError("checkpoint v25 handoff bundle/post-audit digest mismatch")
    if audit.get("post_tick_audit_ready") is not True:
        raise ValueError("checkpoint v25 handoff post-audit is not ready")
    if audit.get("separate_next_action_authorization_required") is not True:
        raise ValueError("checkpoint v25 handoff lost authorization boundary")
    if audit["pair_id"] == audit["previous_pair_id"]:
        raise ValueError("checkpoint v25 handoff pair id was not advanced")

    binding_fields = (
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
        "incumbent_position_id",
        "challenger_position_id",
        "requested_position_ids",
        "evidence_cycle_id",
        "expected_run_id",
        "target_chain_observed_at",
        "execution_receipt_sha256",
        "receipt_tick_status",
        "receipt_pair_tick_complete",
        "receipt_pair_tick_partial_failure",
        "pair_both_open",
        "pair_any_closed",
        "next_debt_type",
        "next_scope",
        "continuation_route",
        "next_evidence_tick_review_ready",
        "tick_recovery_review_ready",
        "terminal_pair_evaluation_ready",
    )
    for field in binding_fields:
        if bundle.get(field) != audit.get(field):
            raise ValueError(
                f"checkpoint v25 handoff bundle/audit {field} mismatch"
            )

    for bundle_field, audit_field in (
        ("current_database_sha256", "audit_database_sha256_after"),
        ("current_wal_sha256", "audit_wal_sha256_after"),
        ("current_shm_sha256", "audit_shm_sha256_after"),
    ):
        if bundle.get(bundle_field) != audit.get(audit_field):
            raise ValueError(
                "checkpoint v25 handoff final database binding mismatch: "
                f"{bundle_field}"
            )

    lineage = _pair_lineage(audit)
    if audit["pair_lineage_sha256"] != _sha256_bytes(
        _canonical_bytes(lineage)
    ):
        raise ValueError("checkpoint v25 handoff pair lineage drifted")

    return _route_state(audit)


def validate_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("checkpoint v25 evidence handoff must be an object")
    if set(report) != set(REPORT_FIELDS) | {"handoff_sha256"}:
        raise ValueError("checkpoint v25 evidence handoff schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint v25 evidence handoff format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected checkpoint v25 evidence handoff type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("checkpoint v25 evidence handoff source lineage mismatch")

    for field in (
        "source_evidence_bundle_sha256",
        "source_post_audit_sha256",
        "source_execution_receipt_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "bundle_database_sha256",
        "pair_lineage_sha256",
        "handoff_sha256",
    ):
        if not _is_hex_digest(report.get(field)):
            raise ValueError(f"checkpoint v25 evidence handoff {field} invalid")
    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
        "bundle_wal_sha256",
        "bundle_shm_sha256",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value):
            raise ValueError(f"checkpoint v25 evidence handoff {field} invalid")

    for field in ("production_repository", "pio_database_path"):
        value = report.get(field)
        if not isinstance(value, str) or not value.startswith("/"):
            raise ValueError(f"checkpoint v25 evidence handoff {field} invalid")
    for field in (
        "pair_id",
        "previous_pair_id",
        "expected_run_id",
        "continuation_route",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"checkpoint v25 evidence handoff {field} invalid")
    if report["pair_id"] == report["previous_pair_id"]:
        raise ValueError("checkpoint v25 evidence handoff pair id did not advance")

    if report.get("handoff_state") not in {
        STATE_CONTINUE,
        STATE_TERMINAL,
        STATE_RECOVERY,
    }:
        raise ValueError("checkpoint v25 evidence handoff state invalid")

    for field in (
        "bundle_post_audit_binding_verified",
        "bundle_route_binding_verified",
        "pair_lineage_verified",
        "current_database_matches_bundle",
        "database_stable_during_handoff",
        "evidence_handoff_ready",
        "handoff_read_only",
        "requires_separate_next_action_authorization",
        "requires_fresh_operator_preflight_for_future_sequence",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"checkpoint v25 evidence handoff requires {field}=true"
            )

    expected_routes = {
        "continuation_handoff_ready": report["handoff_state"] == STATE_CONTINUE,
        "terminal_handoff_ready": report["handoff_state"] == STATE_TERMINAL,
        "recovery_handoff_ready": report["handoff_state"] == STATE_RECOVERY,
    }
    for field, expected in expected_routes.items():
        if report.get(field) is not expected:
            raise ValueError(
                f"checkpoint v25 evidence handoff {field} mismatch"
            )

    for field in (
        "future_checkpoint_refresh_authorized",
        "paper_supervisor_tick_authorized",
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "continuous_promotion_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"checkpoint v25 evidence handoff requires {field}=false"
            )

    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError("checkpoint v25 evidence handoff database changed")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("checkpoint v25 evidence handoff WAL changed")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("checkpoint v25 evidence handoff SHM changed")
    if report["pio_database_sha256_before"] != report[
        "bundle_database_sha256"
    ]:
        raise ValueError("checkpoint v25 evidence handoff database mismatch")
    if report["pio_wal_sha256_before"] != report["bundle_wal_sha256"]:
        raise ValueError("checkpoint v25 evidence handoff WAL mismatch")
    if report["pio_shm_sha256_before"] != report["bundle_shm_sha256"]:
        raise ValueError("checkpoint v25 evidence handoff SHM mismatch")

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["handoff_sha256"] != _sha256_bytes(_canonical_bytes(identity)):
        raise ValueError("checkpoint v25 evidence handoff digest mismatch")


def build_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
    *,
    repository: str | Path,
    source_tree: str | Path,
    evidence_bundle_path: str | Path,
    post_audit_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    production, database = _resolve_production_database(repository)
    before = _database_state(database)

    bundle_module, audit_module = _load_reviewed(source)
    bundle = _load_json(
        evidence_bundle_path,
        label="checkpoint v25 continuation evidence bundle",
    )
    audit = _load_json(
        post_audit_path,
        label="checkpoint v25 continuation post-audit",
    )
    bundle_module.validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle(
        bundle
    )
    audit_module.validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_tick_post_audit(
        audit
    )
    state = _validate_bundle_and_audit_bindings(bundle, audit)

    if bundle["production_repository"] != str(production):
        raise ValueError("checkpoint v25 handoff production repository mismatch")
    if bundle["pio_database_path"] != str(database):
        raise ValueError("checkpoint v25 handoff database path mismatch")

    after = _database_state(database)
    if after != before:
        raise ValueError("production Pio database changed during v25 handoff")
    expected_state = {
        "database": bundle["current_database_sha256"],
        "wal": bundle["current_wal_sha256"],
        "shm": bundle["current_shm_sha256"],
    }
    if before != expected_state:
        raise ValueError(
            "production Pio database no longer matches sealed v25 evidence"
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
        "source_evidence_bundle_sha256": bundle["bundle_sha256"],
        "source_post_audit_sha256": audit["post_audit_sha256"],
        "source_execution_receipt_sha256": audit["execution_receipt_sha256"],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "bundle_database_sha256": bundle["current_database_sha256"],
        "bundle_wal_sha256": bundle["current_wal_sha256"],
        "bundle_shm_sha256": bundle["current_shm_sha256"],
        "pair_lineage_sha256": audit["pair_lineage_sha256"],
        "pair_id": audit["pair_id"],
        "previous_pair_id": audit["previous_pair_id"],
        "expected_run_id": audit["expected_run_id"],
        "continuation_route": audit["continuation_route"],
        "handoff_state": state,
        "bundle_post_audit_binding_verified": True,
        "bundle_route_binding_verified": True,
        "pair_lineage_verified": True,
        "current_database_matches_bundle": True,
        "database_stable_during_handoff": True,
        "evidence_handoff_ready": True,
        "continuation_handoff_ready": state == STATE_CONTINUE,
        "terminal_handoff_ready": state == STATE_TERMINAL,
        "recovery_handoff_ready": state == STATE_RECOVERY,
        "handoff_read_only": True,
        "requires_separate_next_action_authorization": True,
        "requires_fresh_operator_preflight_for_future_sequence": True,
        "future_checkpoint_refresh_authorized": False,
        "paper_supervisor_tick_authorized": False,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "live_submit_authorized": False,
        "transaction_submission_authorized": False,
        "new_live_capital_authorized": False,
        "continuous_promotion_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "handoff_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only handoff acceptance for a sealed checkpoint v25 evidence "
            "bundle and matching post-audit. It revalidates their reviewed "
            "lineage and proves the current production DB/WAL/SHM still match "
            "the sealed final state. It authorizes no future checkpoint, PAPER "
            "tick, live submission, or promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--evidence-bundle", required=True)
    parser.add_argument("--post-audit", required=True)
    args = parser.parse_args()

    report = build_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
        repository=args.repo,
        source_tree=args.source_tree,
        evidence_bundle_path=args.evidence_bundle,
        post_audit_path=args.post_audit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
