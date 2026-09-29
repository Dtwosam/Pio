from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE7_CONTROLLED_LIVE_EXIT_AUTHORIZATION_READINESS_V1"
)

SIGNED_DECISION_TOOL = Path(
    "deploy/tools/build_phase7_controlled_live_exit_signed_decision.py"
)
LIFECYCLE_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_open_position_lifecycle.py"
)
REVIEWED_SOURCE_BLOBS = {
    SIGNED_DECISION_TOOL: "322b137cceb478aed60073005ca28c623bc5f29e",
    LIFECYCLE_TOOL: "5149cc8707479a19b8331a8a13b14344fdad9b84",
}

MAX_CLOCK_SKEW_SECONDS = 30

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_decision_verification_sha256",
    "expected_decision_verification_sha256",
    "original_lifecycle_status_sha256",
    "saved_fresh_lifecycle_status_sha256",
    "expected_fresh_lifecycle_status_sha256",
    "opened_decision_id",
    "opening_signature",
    "pool_address",
    "position_address",
    "executor_wallet_pubkey",
    "rpc_endpoint_sha256",
    "executor_binary_sha256",
    "original_position_snapshot_sha256",
    "fresh_position_snapshot_sha256",
    "original_capture_slot_start",
    "original_capture_slot_end",
    "fresh_capture_slot_start",
    "fresh_capture_slot_end",
    "decision_record_id",
    "decider_principal",
    "decision_issued_at",
    "decision_expires_at",
    "readiness_checked_at",
    "decision_signature_verified",
    "decision_not_expired",
    "fresh_lifecycle_valid",
    "fresh_snapshot_is_newer",
    "position_identity_matches",
    "position_account_present",
    "position_closed_proven",
    "position_still_open",
    "exit_decision_present",
    "human_exit_decision_verified",
    "exit_authorization_readiness_ready",
    "explicit_human_exit_authorization_required",
    "separate_exit_transaction_required",
    "post_exit_confirmation_required",
    "post_exit_closure_proof_required",
    "post_exit_state_reconciliation_required",
    "exit_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_entry_authorized",
    "new_live_capital_authorized",
    "phase7_promotion_authorized",
    "phase7_promotion_persisted",
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
                f"Phase 7 EXIT readiness dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 7 EXIT readiness dependency mismatch: {relative}"
            )
    decision = _load_module(
        source / SIGNED_DECISION_TOOL,
        "phase7_exit_authorization_readiness_decision",
    )
    lifecycle = _load_module(
        source / LIFECYCLE_TOOL,
        "phase7_exit_authorization_readiness_lifecycle",
    )
    return decision, lifecycle


def _canonical_utc(raw: Any, *, label: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError(f"{label} must use UTC Z form")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            f"{label} must use YYYY-MM-DDTHH:MM:SSZ"
        ) from exc


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_exit_authorization_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(
            "Phase 7 EXIT authorization readiness must be a JSON object"
        )
    if set(report) != set(REPORT_FIELDS) | {"readiness_sha256"}:
        raise ValueError(
            "Phase 7 EXIT authorization readiness schema mismatch"
        )
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 7 EXIT authorization readiness format"
        )
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 7 EXIT authorization readiness type"
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
            "Phase 7 EXIT authorization readiness lineage mismatch"
        )

    for field in (
        "saved_decision_verification_sha256",
        "expected_decision_verification_sha256",
        "original_lifecycle_status_sha256",
        "saved_fresh_lifecycle_status_sha256",
        "expected_fresh_lifecycle_status_sha256",
        "rpc_endpoint_sha256",
        "executor_binary_sha256",
        "original_position_snapshot_sha256",
        "fresh_position_snapshot_sha256",
        "readiness_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 7 EXIT authorization readiness {field} is invalid"
            )

    if (
        report["saved_decision_verification_sha256"]
        != report["expected_decision_verification_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT authorization readiness decision digest mismatch"
        )
    if (
        report["saved_fresh_lifecycle_status_sha256"]
        != report["expected_fresh_lifecycle_status_sha256"]
    ):
        raise ValueError(
            "Phase 7 EXIT authorization readiness lifecycle digest mismatch"
        )

    for field in (
        "opened_decision_id",
        "opening_signature",
        "pool_address",
        "position_address",
        "executor_wallet_pubkey",
        "decision_record_id",
        "decider_principal",
        "decision_issued_at",
        "decision_expires_at",
        "readiness_checked_at",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 7 EXIT authorization readiness {field} is invalid"
            )

    issued = _canonical_utc(
        report["decision_issued_at"],
        label="Phase 7 EXIT decision issued_at",
    )
    expires = _canonical_utc(
        report["decision_expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    checked = _canonical_utc(
        report["readiness_checked_at"],
        label="Phase 7 EXIT readiness checked_at",
    )
    if expires <= issued:
        raise ValueError(
            "Phase 7 EXIT authorization readiness decision lifetime invalid"
        )
    if checked < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError(
            "Phase 7 EXIT authorization readiness check predates decision"
        )
    if checked > expires:
        raise ValueError(
            "Phase 7 EXIT authorization readiness decision has expired"
        )

    for field in (
        "original_capture_slot_start",
        "original_capture_slot_end",
        "fresh_capture_slot_start",
        "fresh_capture_slot_end",
    ):
        value = report.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 7 EXIT authorization readiness {field} is invalid"
            )
    if report["original_capture_slot_end"] < report["original_capture_slot_start"]:
        raise ValueError(
            "Phase 7 EXIT authorization readiness original slot range invalid"
        )
    if report["fresh_capture_slot_end"] < report["fresh_capture_slot_start"]:
        raise ValueError(
            "Phase 7 EXIT authorization readiness fresh slot range invalid"
        )
    if report["fresh_capture_slot_start"] <= report["original_capture_slot_end"]:
        raise ValueError(
            "Phase 7 EXIT authorization readiness snapshot is not newer"
        )

    for field in (
        "decision_signature_verified",
        "decision_not_expired",
        "fresh_lifecycle_valid",
        "fresh_snapshot_is_newer",
        "position_identity_matches",
        "position_account_present",
        "position_still_open",
        "exit_decision_present",
        "human_exit_decision_verified",
        "exit_authorization_readiness_ready",
        "explicit_human_exit_authorization_required",
        "separate_exit_transaction_required",
        "post_exit_confirmation_required",
        "post_exit_closure_proof_required",
        "post_exit_state_reconciliation_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 7 EXIT authorization readiness requires {field}=true"
            )

    if report.get("position_closed_proven") is not False:
        raise ValueError(
            "Phase 7 EXIT authorization readiness requires "
            "position_closed_proven=false"
        )

    for field in (
        "exit_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_entry_authorized",
        "new_live_capital_authorized",
        "phase7_promotion_authorized",
        "phase7_promotion_persisted",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 7 EXIT authorization readiness requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["readiness_sha256"] != expected:
        raise ValueError(
            "Phase 7 EXIT authorization readiness digest mismatch"
        )


def build_exit_authorization_readiness(
    *,
    source_tree: str | Path,
    saved_decision_verification_path: str | Path,
    expected_decision_verification_sha256: str,
    fresh_lifecycle_status_path: str | Path,
    expected_fresh_lifecycle_status_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    decision_module, lifecycle_module = _load_reviewed(source)

    decision = _load_json(
        saved_decision_verification_path,
        label="saved Phase 7 EXIT decision verification",
    )
    decision_module.validate_verification(decision)
    if not _is_hex_digest(expected_decision_verification_sha256, 64):
        raise ValueError(
            "expected Phase 7 EXIT decision verification digest is invalid"
        )
    if decision["verification_sha256"] != expected_decision_verification_sha256:
        raise ValueError(
            "saved Phase 7 EXIT decision verification digest mismatch"
        )
    if decision.get("human_exit_decision_verified") is not True:
        raise ValueError(
            "Phase 7 EXIT authorization readiness requires a verified decision"
        )
    if decision.get("exit_decision_present") is not True:
        raise ValueError(
            "Phase 7 EXIT authorization readiness requires EXIT decision present"
        )

    issued = _canonical_utc(
        decision["issued_at"],
        label="Phase 7 EXIT decision issued_at",
    )
    expires = _canonical_utc(
        decision["expires_at"],
        label="Phase 7 EXIT decision expires_at",
    )
    now_dt = (
        _canonical_utc(
            now,
            label="Phase 7 EXIT authorization readiness now",
        )
        if now is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    if now_dt < issued - timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ValueError("Phase 7 EXIT decision is not yet valid")
    if now_dt > expires:
        raise ValueError("Phase 7 EXIT decision has expired")

    fresh = _load_json(
        fresh_lifecycle_status_path,
        label="fresh Phase 7 open-position lifecycle status",
    )
    lifecycle_module.validate_open_position_lifecycle_status(fresh)
    if not _is_hex_digest(expected_fresh_lifecycle_status_sha256, 64):
        raise ValueError(
            "expected fresh Phase 7 lifecycle digest is invalid"
        )
    if fresh["lifecycle_status_sha256"] != expected_fresh_lifecycle_status_sha256:
        raise ValueError("fresh Phase 7 lifecycle digest mismatch")

    if fresh.get("lifecycle_route") != "OPEN_POSITION_OBSERVATION":
        raise ValueError(
            "fresh Phase 7 lifecycle is not open-position observation"
        )
    if fresh.get("position_account_present") is not True:
        raise ValueError(
            "fresh Phase 7 lifecycle no longer observes the position account"
        )
    if fresh.get("position_closed_proven") is not False:
        raise ValueError(
            "fresh Phase 7 lifecycle proves the position is closed"
        )
    if fresh.get("open_position_snapshot_ready") is not True:
        raise ValueError(
            "fresh Phase 7 lifecycle open-position snapshot is not ready"
        )

    bindings = (
        ("decision_id", "opened_decision_id"),
        ("signature", "opening_signature"),
        ("pool_address", "pool_address"),
        ("position_address", "position_address"),
        ("executor_wallet_pubkey", "executor_wallet_pubkey"),
        ("rpc_endpoint_sha256", "rpc_endpoint_sha256"),
        ("executor_binary_sha256", "executor_binary_sha256"),
    )
    for fresh_field, decision_field in bindings:
        if fresh.get(fresh_field) != decision.get(decision_field):
            raise ValueError(
                f"fresh Phase 7 lifecycle {fresh_field} differs from signed "
                "EXIT decision"
            )

    if fresh["capture_slot_start"] <= decision["capture_slot_end"]:
        raise ValueError(
            "fresh Phase 7 lifecycle snapshot is not newer than signed decision"
        )

    for artifact, label in (
        (decision, "signed EXIT decision verification"),
        (fresh, "fresh open-position lifecycle"),
    ):
        for field in (
            "exit_authorized",
            "controlled_live_authorized",
            "live_submit_authorized",
            "transaction_signing_authorized",
            "transaction_submission_authorized",
            "automatic_resubmission_authorized",
            "new_live_entry_authorized",
            "new_live_capital_authorized",
            "phase7_promotion_authorized",
            "phase7_promotion_persisted",
            "production_file_modified",
            "production_repository_git_mutated",
            "production_pio_database_modified",
        ):
            if artifact.get(field) is not False:
                raise ValueError(
                    f"{label} unexpectedly authorizes {field}"
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
        "saved_decision_verification_sha256": decision["verification_sha256"],
        "expected_decision_verification_sha256": (
            expected_decision_verification_sha256
        ),
        "original_lifecycle_status_sha256": decision[
            "lifecycle_status_sha256"
        ],
        "saved_fresh_lifecycle_status_sha256": fresh[
            "lifecycle_status_sha256"
        ],
        "expected_fresh_lifecycle_status_sha256": (
            expected_fresh_lifecycle_status_sha256
        ),
        "opened_decision_id": decision["opened_decision_id"],
        "opening_signature": decision["opening_signature"],
        "pool_address": decision["pool_address"],
        "position_address": decision["position_address"],
        "executor_wallet_pubkey": decision["executor_wallet_pubkey"],
        "rpc_endpoint_sha256": decision["rpc_endpoint_sha256"],
        "executor_binary_sha256": decision["executor_binary_sha256"],
        "original_position_snapshot_sha256": decision[
            "position_snapshot_sha256"
        ],
        "fresh_position_snapshot_sha256": fresh[
            "position_snapshot_sha256"
        ],
        "original_capture_slot_start": decision["capture_slot_start"],
        "original_capture_slot_end": decision["capture_slot_end"],
        "fresh_capture_slot_start": fresh["capture_slot_start"],
        "fresh_capture_slot_end": fresh["capture_slot_end"],
        "decision_record_id": decision["decision_record_id"],
        "decider_principal": decision["decider_principal"],
        "decision_issued_at": decision["issued_at"],
        "decision_expires_at": decision["expires_at"],
        "readiness_checked_at": _format_utc(now_dt),
        "decision_signature_verified": True,
        "decision_not_expired": True,
        "fresh_lifecycle_valid": True,
        "fresh_snapshot_is_newer": True,
        "position_identity_matches": True,
        "position_account_present": True,
        "position_closed_proven": False,
        "position_still_open": True,
        "exit_decision_present": True,
        "human_exit_decision_verified": True,
        "exit_authorization_readiness_ready": True,
        "explicit_human_exit_authorization_required": True,
        "separate_exit_transaction_required": True,
        "post_exit_confirmation_required": True,
        "post_exit_closure_proof_required": True,
        "post_exit_state_reconciliation_required": True,
        "exit_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_entry_authorized": False,
        "new_live_capital_authorized": False,
        "phase7_promotion_authorized": False,
        "phase7_promotion_persisted": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "readiness_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_exit_authorization_readiness(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Bind a verified short-lived Phase 7 EXIT decision to a strictly "
            "newer read-only lifecycle observation of the same still-open "
            "position. This readiness gate authorizes nothing and requires "
            "separate human EXIT authorization and transaction boundaries."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-decision-verification", required=True)
    parser.add_argument(
        "--expected-decision-verification-sha256",
        required=True,
    )
    parser.add_argument("--fresh-lifecycle-status", required=True)
    parser.add_argument(
        "--expected-fresh-lifecycle-status-sha256",
        required=True,
    )
    parser.add_argument("--now")
    args = parser.parse_args()

    report = build_exit_authorization_readiness(
        source_tree=args.source_tree,
        saved_decision_verification_path=(
            args.saved_decision_verification
        ),
        expected_decision_verification_sha256=(
            args.expected_decision_verification_sha256
        ),
        fresh_lifecycle_status_path=args.fresh_lifecycle_status,
        expected_fresh_lifecycle_status_sha256=(
            args.expected_fresh_lifecycle_status_sha256
        ),
        now=args.now,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
