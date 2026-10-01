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
    "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_OPERATOR_SESSION_ARCHIVE_V1"
)

SURFACE_TOOL = Path(
    "deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_operator_surface.py"
)
PREFLIGHT_TOOL = Path(
    "deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_operator_preflight.py"
)
STATUS_TOOL = Path(
    "deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_artifact_status.py"
)
HANDOFF_TOOL = Path(
    "deploy/tools/check_phase8_recursive_reentry_checkpoint_v25_evidence_handoff.py"
)
BUNDLE_TOOL = Path(
    "deploy/tools/build_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle.py"
)

REVIEWED_SOURCE_BLOBS = {
    SURFACE_TOOL: "9ef36e3b4fd72580930d1deba6fcede5f3a4179c",
    PREFLIGHT_TOOL: "ec750da64adffec6f0b0813ecb450b22077db0ae",
    STATUS_TOOL: "1c0c3dc90dee361e9bceaecff0d8644259eefd25",
    HANDOFF_TOOL: "43ccdb2e6b701689c22d8b7fe026c686a6c3a52e",
    BUNDLE_TOOL: "99be522c3a9c9e71b8d6657b6a23133b1a4bef0f",
}

CORE_ROLE_TO_BUNDLE_NAME = {
    "bundled-canonical-checkpoint": "checkpoint",
    "continuation-readiness": "continuation_readiness",
    "tick-request": "request",
    "detached-authorization": "signed_authorization_verification",
    "execution-readiness": "execution_readiness",
    "one-shot-paper-executor": "execution_receipt",
    "post-execution-audit": "post_audit",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "source_tree",
    "production_repository",
    "pio_database_path",
    "surface_integrity_file_sha256",
    "preflight_file_sha256",
    "artifact_status_file_sha256",
    "evidence_bundle_file_sha256",
    "evidence_handoff_file_sha256",
    "surface_integrity_sha256",
    "preflight_sha256",
    "artifact_status_sha256",
    "evidence_bundle_sha256",
    "evidence_handoff_sha256",
    "core_execution_manifest_git_blob",
    "core_manifest_sha256",
    "core_tool_blobs",
    "core_artifact_file_sha256",
    "core_artifact_hashes_match_bundle",
    "core_artifact_chain_complete",
    "pair_id",
    "previous_pair_id",
    "expected_run_id",
    "continuation_route",
    "handoff_state",
    "bundle_database_sha256",
    "bundle_wal_sha256",
    "bundle_shm_sha256",
    "surface_integrity_verified",
    "preflight_verified",
    "artifact_status_verified",
    "evidence_bundle_verified",
    "evidence_handoff_verified",
    "session_lineage_verified",
    "archive_read_only",
    "historical_archive_only",
    "fresh_preflight_performed",
    "detached_signature_reverification_performed",
    "current_database_revalidation_performed",
    "next_action_authorized",
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


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _safe_source_tree(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("reviewed source tree must not be a symlink")
    source = candidate.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    return source


def _regular_json(
    path: str | Path,
    *,
    label: str,
) -> tuple[dict[str, Any], str]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    payload = resolved.read_bytes()
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value, _sha256_bytes(payload)


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_reviewed(source: Path) -> dict[str, Any]:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"checkpoint v25 session archive dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"checkpoint v25 session archive dependency mismatch: {relative}"
            )

    return {
        "surface": _load_module(
            source / SURFACE_TOOL,
            "phase8_v25_session_surface",
        ),
        "preflight": _load_module(
            source / PREFLIGHT_TOOL,
            "phase8_v25_session_preflight",
        ),
        "status": _load_module(
            source / STATUS_TOOL,
            "phase8_v25_session_status",
        ),
        "handoff": _load_module(
            source / HANDOFF_TOOL,
            "phase8_v25_session_handoff",
        ),
        "bundle": _load_module(
            source / BUNDLE_TOOL,
            "phase8_v25_session_bundle",
        ),
    }


def _require_false(value: dict[str, Any], *fields: str) -> None:
    for field in fields:
        if value.get(field) is not False:
            raise ValueError(
                f"checkpoint v25 session archive requires {field}=false"
            )


def _validate_input_reports(
    *,
    modules: dict[str, Any],
    surface: dict[str, Any],
    preflight: dict[str, Any],
    status: dict[str, Any],
    bundle: dict[str, Any],
    handoff: dict[str, Any],
) -> None:
    modules[
        "surface"
    ].validate_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
        surface
    )
    modules[
        "preflight"
    ].validate_phase8_recursive_reentry_checkpoint_v25_operator_preflight(
        preflight
    )
    modules[
        "status"
    ].validate_phase8_recursive_reentry_checkpoint_v25_artifact_status(
        status
    )
    modules[
        "bundle"
    ].validate_phase8_recursive_reentry_checkpoint_v25_continuation_evidence_bundle(
        bundle
    )
    modules[
        "handoff"
    ].validate_phase8_recursive_reentry_checkpoint_v25_evidence_handoff(
        handoff
    )


def _validate_cross_bindings(
    *,
    source: Path,
    surface: dict[str, Any],
    preflight: dict[str, Any],
    status: dict[str, Any],
    bundle: dict[str, Any],
    bundle_file_sha256: str,
    handoff: dict[str, Any],
) -> dict[str, str]:
    if Path(surface["source_tree"]).resolve() != source:
        raise ValueError("checkpoint v25 session surface source-tree mismatch")
    if Path(preflight["source_tree"]).resolve() != source:
        raise ValueError("checkpoint v25 session preflight source-tree mismatch")
    if Path(status["source_tree"]).resolve() != source:
        raise ValueError("checkpoint v25 session status source-tree mismatch")

    if surface.get("support_surface_verified") is not True:
        raise ValueError("checkpoint v25 session surface is not verified")
    if surface.get("core_execution_manifest_verified") is not True:
        raise ValueError("checkpoint v25 session core manifest is not verified")
    if surface.get("real_v25_evidence_required_for_future_checkpoint") is not True:
        raise ValueError("checkpoint v25 session lost evidence boundary")
    _require_false(
        surface,
        "next_action_authorized",
        "future_checkpoint_refresh_authorized",
        "paper_supervisor_tick_authorized",
        "live_submit_authorized",
        "phase8_promotion_authorized",
    )

    if preflight.get("preflight_ready") is not True:
        raise ValueError("checkpoint v25 session preflight is not ready")
    if preflight.get("manifest_valid") is not True:
        raise ValueError("checkpoint v25 session preflight manifest invalid")
    if preflight.get("reviewed_source_tools_verified") is not True:
        raise ValueError("checkpoint v25 session preflight tools not verified")
    _require_false(
        preflight,
        "paper_supervisor_tick_authorized",
        "paper_trading_authorized",
        "live_submit_authorized",
        "phase8_promotion_authorized",
    )

    if status.get("artifact_chain_complete") is not True:
        raise ValueError("checkpoint v25 session artifact chain is incomplete")
    if status.get("valid_prefix_length") != 8:
        raise ValueError("checkpoint v25 session artifact prefix is incomplete")
    if status.get("next_boundary") != "EVIDENCE_CHAIN_COMPLETE":
        raise ValueError("checkpoint v25 session artifact status is not complete")
    if status.get("invalid_artifact_present") is not False:
        raise ValueError("checkpoint v25 session contains invalid artifact")
    if status.get("out_of_order_artifacts_present") is not False:
        raise ValueError("checkpoint v25 session contains out-of-order artifact")
    _require_false(
        status,
        "next_action_authorized",
        "paper_supervisor_tick_authorized",
        "live_submit_authorized",
        "phase8_promotion_authorized",
    )

    core_blob = surface["core_execution_manifest_git_blob"]
    if preflight["reviewed_manifest_blob"] != core_blob:
        raise ValueError("checkpoint v25 session preflight/core manifest mismatch")
    if status["reviewed_manifest_blob"] != core_blob:
        raise ValueError("checkpoint v25 session status/core manifest mismatch")
    if preflight["manifest_sha256"] != status["manifest_sha256"]:
        raise ValueError("checkpoint v25 session core manifest digest mismatch")
    if preflight["reviewed_tool_blobs"] != status["reviewed_tool_blobs"]:
        raise ValueError("checkpoint v25 session reviewed tool set mismatch")

    stages = status["stages"]
    if len(stages) != 8:
        raise ValueError("checkpoint v25 session status stage count mismatch")
    by_role = {stage["role"]: stage for stage in stages}
    if len(by_role) != 8:
        raise ValueError("checkpoint v25 session duplicate status role")

    artifact_hashes = bundle["artifact_file_sha256"]
    for role, bundle_name in CORE_ROLE_TO_BUNDLE_NAME.items():
        stage = by_role.get(role)
        if stage is None or stage.get("validation_status") != "VALID":
            raise ValueError(
                f"checkpoint v25 session core stage invalid: {role}"
            )
        if stage.get("artifact_sha256") != artifact_hashes.get(bundle_name):
            raise ValueError(
                f"checkpoint v25 session core artifact hash mismatch: {role}"
            )

    bundle_stage = by_role.get("evidence-bundle")
    if bundle_stage is None or bundle_stage.get("validation_status") != "VALID":
        raise ValueError("checkpoint v25 session evidence-bundle stage invalid")
    if bundle_stage.get("artifact_sha256") != bundle_file_sha256:
        raise ValueError(
            "checkpoint v25 session evidence-bundle file hash mismatch"
        )

    for field in ("production_repository", "pio_database_path"):
        if preflight[field] != bundle[field] or bundle[field] != handoff[field]:
            raise ValueError(
                f"checkpoint v25 session {field} binding mismatch"
            )

    if handoff.get("evidence_handoff_ready") is not True:
        raise ValueError("checkpoint v25 session handoff is not ready")
    if handoff["source_evidence_bundle_sha256"] != bundle["bundle_sha256"]:
        raise ValueError("checkpoint v25 session bundle/handoff digest mismatch")
    if handoff["source_post_audit_sha256"] != bundle["post_audit_sha256"]:
        raise ValueError("checkpoint v25 session post-audit binding mismatch")
    if handoff["source_execution_receipt_sha256"] != bundle[
        "execution_receipt_sha256"
    ]:
        raise ValueError("checkpoint v25 session receipt binding mismatch")
    if handoff["pair_id"] != bundle["pair_id"]:
        raise ValueError("checkpoint v25 session pair binding mismatch")
    if handoff["previous_pair_id"] != bundle["previous_pair_id"]:
        raise ValueError("checkpoint v25 session previous-pair binding mismatch")
    if handoff["expected_run_id"] != bundle["expected_run_id"]:
        raise ValueError("checkpoint v25 session run binding mismatch")
    if handoff["continuation_route"] != bundle["continuation_route"]:
        raise ValueError("checkpoint v25 session route binding mismatch")
    if handoff["bundle_database_sha256"] != bundle["current_database_sha256"]:
        raise ValueError("checkpoint v25 session database binding mismatch")
    if handoff["bundle_wal_sha256"] != bundle["current_wal_sha256"]:
        raise ValueError("checkpoint v25 session WAL binding mismatch")
    if handoff["bundle_shm_sha256"] != bundle["current_shm_sha256"]:
        raise ValueError("checkpoint v25 session SHM binding mismatch")
    _require_false(
        handoff,
        "future_checkpoint_refresh_authorized",
        "paper_supervisor_tick_authorized",
        "live_submit_authorized",
        "phase8_promotion_authorized",
    )

    return dict(preflight["reviewed_tool_blobs"])


def validate_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("checkpoint v25 session archive must be an object")
    if set(report) != set(REPORT_FIELDS) | {"session_archive_sha256"}:
        raise ValueError("checkpoint v25 session archive schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint v25 session archive format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected checkpoint v25 session archive type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("checkpoint v25 session archive source lineage mismatch")

    for field in (
        "surface_integrity_file_sha256",
        "preflight_file_sha256",
        "artifact_status_file_sha256",
        "evidence_bundle_file_sha256",
        "evidence_handoff_file_sha256",
        "surface_integrity_sha256",
        "preflight_sha256",
        "artifact_status_sha256",
        "evidence_bundle_sha256",
        "evidence_handoff_sha256",
        "core_manifest_sha256",
        "bundle_database_sha256",
        "session_archive_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"checkpoint v25 session archive {field} invalid")
    for field in ("bundle_wal_sha256", "bundle_shm_sha256"):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"checkpoint v25 session archive {field} invalid")

    if not _is_hex_digest(report.get("core_execution_manifest_git_blob"), 40):
        raise ValueError("checkpoint v25 session archive core blob invalid")
    tool_blobs = report.get("core_tool_blobs")
    if not isinstance(tool_blobs, dict) or len(tool_blobs) != 8:
        raise ValueError("checkpoint v25 session archive core tool set invalid")
    if any(not _is_hex_digest(value, 40) for value in tool_blobs.values()):
        raise ValueError("checkpoint v25 session archive core tool blob invalid")
    artifact_hashes = report.get("core_artifact_file_sha256")
    if not isinstance(artifact_hashes, dict) or len(artifact_hashes) != 7:
        raise ValueError("checkpoint v25 session archive artifact hashes invalid")
    if any(not _is_hex_digest(value, 64) for value in artifact_hashes.values()):
        raise ValueError("checkpoint v25 session archive artifact hash invalid")

    for field in (
        "source_tree",
        "production_repository",
        "pio_database_path",
        "pair_id",
        "previous_pair_id",
        "expected_run_id",
        "continuation_route",
        "handoff_state",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"checkpoint v25 session archive {field} invalid")

    for field in (
        "core_artifact_hashes_match_bundle",
        "core_artifact_chain_complete",
        "surface_integrity_verified",
        "preflight_verified",
        "artifact_status_verified",
        "evidence_bundle_verified",
        "evidence_handoff_verified",
        "session_lineage_verified",
        "archive_read_only",
        "historical_archive_only",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"checkpoint v25 session archive requires {field}=true"
            )

    for field in (
        "fresh_preflight_performed",
        "detached_signature_reverification_performed",
        "current_database_revalidation_performed",
        "next_action_authorized",
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
                f"checkpoint v25 session archive requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["session_archive_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("checkpoint v25 session archive digest mismatch")


def build_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
    *,
    source_tree: str | Path,
    surface_integrity_path: str | Path,
    preflight_path: str | Path,
    artifact_status_path: str | Path,
    evidence_bundle_path: str | Path,
    evidence_handoff_path: str | Path,
) -> dict[str, Any]:
    source = _safe_source_tree(source_tree)
    modules = _load_reviewed(source)

    surface, surface_file_sha = _regular_json(
        surface_integrity_path,
        label="checkpoint v25 operator surface integrity",
    )
    preflight, preflight_file_sha = _regular_json(
        preflight_path,
        label="checkpoint v25 operator preflight",
    )
    status, status_file_sha = _regular_json(
        artifact_status_path,
        label="checkpoint v25 artifact status",
    )
    bundle, bundle_file_sha = _regular_json(
        evidence_bundle_path,
        label="checkpoint v25 evidence bundle",
    )
    handoff, handoff_file_sha = _regular_json(
        evidence_handoff_path,
        label="checkpoint v25 evidence handoff",
    )

    _validate_input_reports(
        modules=modules,
        surface=surface,
        preflight=preflight,
        status=status,
        bundle=bundle,
        handoff=handoff,
    )
    core_tool_blobs = _validate_cross_bindings(
        source=source,
        surface=surface,
        preflight=preflight,
        status=status,
        bundle=bundle,
        bundle_file_sha256=bundle_file_sha,
        handoff=handoff,
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
        "source_tree": str(source),
        "production_repository": bundle["production_repository"],
        "pio_database_path": bundle["pio_database_path"],
        "surface_integrity_file_sha256": surface_file_sha,
        "preflight_file_sha256": preflight_file_sha,
        "artifact_status_file_sha256": status_file_sha,
        "evidence_bundle_file_sha256": bundle_file_sha,
        "evidence_handoff_file_sha256": handoff_file_sha,
        "surface_integrity_sha256": surface["surface_integrity_sha256"],
        "preflight_sha256": preflight["preflight_sha256"],
        "artifact_status_sha256": status["artifact_status_sha256"],
        "evidence_bundle_sha256": bundle["bundle_sha256"],
        "evidence_handoff_sha256": handoff["handoff_sha256"],
        "core_execution_manifest_git_blob": surface[
            "core_execution_manifest_git_blob"
        ],
        "core_manifest_sha256": preflight["manifest_sha256"],
        "core_tool_blobs": core_tool_blobs,
        "core_artifact_file_sha256": dict(bundle["artifact_file_sha256"]),
        "core_artifact_hashes_match_bundle": True,
        "core_artifact_chain_complete": True,
        "pair_id": bundle["pair_id"],
        "previous_pair_id": bundle["previous_pair_id"],
        "expected_run_id": bundle["expected_run_id"],
        "continuation_route": bundle["continuation_route"],
        "handoff_state": handoff["handoff_state"],
        "bundle_database_sha256": bundle["current_database_sha256"],
        "bundle_wal_sha256": bundle["current_wal_sha256"],
        "bundle_shm_sha256": bundle["current_shm_sha256"],
        "surface_integrity_verified": True,
        "preflight_verified": True,
        "artifact_status_verified": True,
        "evidence_bundle_verified": True,
        "evidence_handoff_verified": True,
        "session_lineage_verified": True,
        "archive_read_only": True,
        "historical_archive_only": True,
        "fresh_preflight_performed": False,
        "detached_signature_reverification_performed": False,
        "current_database_revalidation_performed": False,
        "next_action_authorized": False,
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
        "session_archive_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Seal a historical checkpoint v25 operator session from already-"
            "produced, natively validated surface-integrity, preflight, final "
            "artifact-status, evidence-bundle and evidence-handoff reports. "
            "This archive does not perform a fresh preflight, reverify the "
            "detached signature, re-read production state, or authorize any "
            "next action."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--surface-integrity", required=True)
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--artifact-status", required=True)
    parser.add_argument("--evidence-bundle", required=True)
    parser.add_argument("--evidence-handoff", required=True)
    args = parser.parse_args()

    report = (
        build_phase8_recursive_reentry_checkpoint_v25_operator_session_archive(
            source_tree=args.source_tree,
            surface_integrity_path=args.surface_integrity,
            preflight_path=args.preflight,
            artifact_status_path=args.artifact_status,
            evidence_bundle_path=args.evidence_bundle,
            evidence_handoff_path=args.evidence_handoff,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
