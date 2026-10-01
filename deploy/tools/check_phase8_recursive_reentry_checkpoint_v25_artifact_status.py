from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_ARTIFACT_STATUS_V1"
)
MANIFEST = Path(
    "deploy/manifests/phase8-recursive-reentry-checkpoint-v25-continuation.json"
)
MANIFEST_BLOB = "bda473bcece0a90b281fafff2907ccadd67a79cf"

EXPECTED_ROLES = (
    "bundled-canonical-checkpoint",
    "continuation-readiness",
    "tick-request",
    "detached-authorization",
    "execution-readiness",
    "one-shot-paper-executor",
    "post-execution-audit",
    "evidence-bundle",
)
VALIDATORS = {
    "bundled-canonical-checkpoint": (
        "validate_phase8_recursive_reentry_continuation_checkpoint_v25"
    ),
    "continuation-readiness": (
        "validate_phase8_recursive_reentry_checkpoint_v25_"
        "continuation_supervision_readiness"
    ),
    "tick-request": (
        "validate_phase8_recursive_reentry_checkpoint_v25_"
        "continuation_evidence_tick_request"
    ),
    "detached-authorization": "validate_verification",
    "execution-readiness": (
        "validate_phase8_recursive_reentry_checkpoint_v25_"
        "continuation_evidence_tick_execution_readiness"
    ),
    "one-shot-paper-executor": (
        "validate_phase8_recursive_reentry_checkpoint_v25_"
        "continuation_evidence_tick_execution_receipt"
    ),
    "post-execution-audit": (
        "validate_phase8_recursive_reentry_checkpoint_v25_"
        "continuation_evidence_tick_post_audit"
    ),
    "evidence-bundle": (
        "validate_phase8_recursive_reentry_checkpoint_v25_"
        "continuation_evidence_bundle"
    ),
}
EXPECTED_SAFETY_BOUNDARY = {
    "automatic_paper_execution_authorized": False,
    "recurring_paper_collection_authorized": False,
    "scheduler_execution_authorized": False,
    "live_submit_authorized": False,
    "transaction_submission_authorized": False,
    "new_live_capital_authorized": False,
    "continuous_promotion_authorized": False,
    "phase8_promotion_authorized": False,
}

STAGE_FIELDS = (
    "order",
    "role",
    "tool",
    "tool_git_blob",
    "validator",
    "artifact_name",
    "artifact_path",
    "paper_state_mutation",
    "human_signature_required",
    "exists",
    "regular_file",
    "artifact_sha256",
    "json_object",
    "validation_status",
    "validation_error_category",
)
REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_manifest_blob",
    "manifest_sha256",
    "source_tree",
    "artifact_directory",
    "reviewed_tool_blobs",
    "stages",
    "valid_prefix_length",
    "artifact_chain_prefix_valid",
    "artifact_chain_complete",
    "invalid_artifact_present",
    "out_of_order_artifacts_present",
    "first_incomplete_order",
    "first_incomplete_role",
    "first_incomplete_artifact",
    "first_incomplete_status",
    "next_boundary",
    "status_only",
    "fresh_preflight_required_before_any_operator_sequence",
    "detached_signature_reverification_performed",
    "production_database_revalidation_performed",
    "next_action_authorized",
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


def _safe_directory(path: str | Path, *, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{label} must be a directory")
    return resolved


def _load_manifest(source: Path) -> tuple[dict[str, Any], str]:
    path = source / MANIFEST
    if path.is_symlink() or not path.is_file():
        raise ValueError("checkpoint v25 operator manifest is missing")
    payload = path.read_bytes()
    if _git_blob_sha_bytes(payload) != MANIFEST_BLOB:
        raise ValueError("checkpoint v25 operator manifest blob mismatch")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("checkpoint v25 operator manifest must be an object")
    _validate_manifest(value)
    return value, _sha256_bytes(payload)


def _validate_manifest(value: dict[str, Any]) -> None:
    if value.get("format_version") != 1:
        raise ValueError("checkpoint v25 operator manifest format mismatch")
    if value.get("artifact_type") != (
        "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_CONTINUATION_"
        "OPERATOR_MANIFEST_V1"
    ):
        raise ValueError("checkpoint v25 operator manifest type mismatch")
    if value.get("checkpoint_version") != 25:
        raise ValueError("checkpoint v25 operator manifest version mismatch")

    steps = value.get("ordered_steps")
    if not isinstance(steps, list) or len(steps) != len(EXPECTED_ROLES):
        raise ValueError("checkpoint v25 operator manifest steps are invalid")
    if [item.get("order") for item in steps] != list(range(1, 9)):
        raise ValueError("checkpoint v25 operator manifest step order mismatch")
    if [item.get("role") for item in steps] != list(EXPECTED_ROLES):
        raise ValueError("checkpoint v25 operator manifest roles mismatch")

    names: set[str] = set()
    mutating = []
    for item in steps:
        role = item.get("role")
        tool = item.get("tool")
        blob = item.get("git_blob")
        output = item.get("output_artifact")
        if role not in VALIDATORS:
            raise ValueError("checkpoint v25 operator manifest role is unsupported")
        if not isinstance(tool, str) or not tool:
            raise ValueError("checkpoint v25 operator manifest tool is invalid")
        tool_path = Path(tool)
        if tool_path.is_absolute() or ".." in tool_path.parts:
            raise ValueError("checkpoint v25 operator manifest tool path unsafe")
        if not _is_hex_digest(blob, 40):
            raise ValueError("checkpoint v25 operator manifest tool blob invalid")
        if not isinstance(output, str) or not output:
            raise ValueError("checkpoint v25 operator manifest output is invalid")
        output_path = Path(output)
        if (
            output_path.is_absolute()
            or len(output_path.parts) != 1
            or ".." in output_path.parts
        ):
            raise ValueError("checkpoint v25 operator manifest output path unsafe")
        if output in names:
            raise ValueError("checkpoint v25 operator manifest output is duplicated")
        names.add(output)
        if item.get("paper_state_mutation") is True:
            mutating.append(item)

    if len(mutating) != 1 or mutating[0].get("role") != "one-shot-paper-executor":
        raise ValueError("checkpoint v25 operator manifest mutation boundary mismatch")
    if mutating[0].get("maximum_tick_count") != 1:
        raise ValueError("checkpoint v25 operator manifest tick bound mismatch")

    signed = next(
        item for item in steps
        if item.get("role") == "detached-authorization"
    )
    if signed.get("human_signature_required") is not True:
        raise ValueError("checkpoint v25 operator manifest signature boundary mismatch")

    prior = value.get("prior_evidence_boundary")
    if prior != {
        "sealed_v24_evidence_bundle_required": True,
        "matching_v24_post_audit_required": True,
        "prior_bundle_digest_must_survive_all_v25_stages": True,
    }:
        raise ValueError("checkpoint v25 operator manifest prior boundary mismatch")

    execution = value.get("execution_boundary")
    if not isinstance(execution, dict):
        raise ValueError("checkpoint v25 operator manifest execution boundary missing")
    if execution.get("only_mutating_step") != "one-shot-paper-executor":
        raise ValueError("checkpoint v25 operator manifest mutating step mismatch")
    if execution.get("maximum_paper_ticks_per_authorization") != 1:
        raise ValueError("checkpoint v25 operator manifest authorization scope mismatch")
    if execution.get("prior_bundle_digest_must_match_every_stage") is not True:
        raise ValueError("checkpoint v25 operator manifest prior lineage mismatch")

    if value.get("safety_boundary") != EXPECTED_SAFETY_BOUNDARY:
        raise ValueError("checkpoint v25 operator manifest safety boundary mismatch")


def _verify_source_tools(
    source: Path,
    manifest: dict[str, Any],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for step in manifest["ordered_steps"]:
        relative = Path(step["tool"])
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"checkpoint v25 reviewed tool missing: {relative}")
        actual = _git_blob_sha(path)
        if actual != step["git_blob"]:
            raise ValueError(
                f"checkpoint v25 reviewed tool blob mismatch: {relative}"
            )
        result[relative.as_posix()] = actual
    return result


def _load_validator(
    source: Path,
    step: dict[str, Any],
) -> Callable[[dict[str, Any]], None]:
    role = step["role"]
    relative = Path(step["tool"])
    path = source / relative
    module_name = "phase8_v25_status_" + role.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed validator: {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    validator_name = VALIDATORS[role]
    validator = getattr(module, validator_name, None)
    if not callable(validator):
        raise ValueError(
            f"reviewed checkpoint v25 validator missing: {validator_name}"
        )
    return validator


def _inspect_stage(
    *,
    source: Path,
    artifact_dir: Path,
    step: dict[str, Any],
) -> dict[str, Any]:
    artifact = artifact_dir / step["output_artifact"]
    base = {
        "order": step["order"],
        "role": step["role"],
        "tool": step["tool"],
        "tool_git_blob": step["git_blob"],
        "validator": VALIDATORS[step["role"]],
        "artifact_name": step["output_artifact"],
        "artifact_path": str(artifact),
        "paper_state_mutation": step.get("paper_state_mutation") is True,
        "human_signature_required": (
            step.get("human_signature_required") is True
        ),
    }
    try:
        st = os.lstat(artifact)
    except FileNotFoundError:
        return {
            **base,
            "exists": False,
            "regular_file": False,
            "artifact_sha256": None,
            "json_object": False,
            "validation_status": "MISSING",
            "validation_error_category": "ARTIFACT_MISSING",
        }

    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return {
            **base,
            "exists": True,
            "regular_file": False,
            "artifact_sha256": None,
            "json_object": False,
            "validation_status": "UNSAFE",
            "validation_error_category": "UNSAFE_ARTIFACT",
        }

    payload = artifact.read_bytes()
    digest = _sha256_bytes(payload)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            **base,
            "exists": True,
            "regular_file": True,
            "artifact_sha256": digest,
            "json_object": False,
            "validation_status": "INVALID_JSON",
            "validation_error_category": "INVALID_JSON",
        }
    if not isinstance(value, dict):
        return {
            **base,
            "exists": True,
            "regular_file": True,
            "artifact_sha256": digest,
            "json_object": False,
            "validation_status": "INVALID",
            "validation_error_category": "VALIDATOR_REJECTED",
        }

    validator = _load_validator(source, step)
    try:
        validator(value)
    except Exception:
        return {
            **base,
            "exists": True,
            "regular_file": True,
            "artifact_sha256": digest,
            "json_object": True,
            "validation_status": "INVALID",
            "validation_error_category": "VALIDATOR_REJECTED",
        }
    return {
        **base,
        "exists": True,
        "regular_file": True,
        "artifact_sha256": digest,
        "json_object": True,
        "validation_status": "VALID",
        "validation_error_category": None,
    }


def _next_boundary(
    *,
    stage: dict[str, Any] | None,
    out_of_order: bool,
) -> str:
    if stage is None:
        return "EVIDENCE_CHAIN_COMPLETE"
    if stage["validation_status"] in {"UNSAFE", "INVALID_JSON", "INVALID"}:
        return "INVALID_ARTIFACT_REVIEW_REQUIRED"
    if out_of_order:
        return "ARTIFACT_ORDER_REVIEW_REQUIRED"
    role = stage["role"]
    if role == "detached-authorization":
        return "EXTERNAL_HUMAN_SIGNATURE_REQUIRED"
    if role == "one-shot-paper-executor":
        return "MUTATION_BOUNDARY_REVIEW_REQUIRED"
    if role in {"post-execution-audit", "evidence-bundle"}:
        return "READ_ONLY_POST_EXECUTION_ARTIFACT_REQUIRED"
    return "READ_ONLY_ARTIFACT_REQUIRED"


def validate_phase8_recursive_reentry_checkpoint_v25_artifact_status(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("checkpoint v25 artifact status must be an object")
    if set(report) != set(REPORT_FIELDS) | {"artifact_status_sha256"}:
        raise ValueError("checkpoint v25 artifact status schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint v25 artifact status format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected checkpoint v25 artifact status type")
    if report.get("reviewed_manifest_blob") != MANIFEST_BLOB:
        raise ValueError("checkpoint v25 artifact status manifest mismatch")
    for field in ("manifest_sha256", "artifact_status_sha256"):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"checkpoint v25 artifact status {field} invalid")
    for field in ("source_tree", "artifact_directory"):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"checkpoint v25 artifact status {field} invalid")

    blobs = report.get("reviewed_tool_blobs")
    if not isinstance(blobs, dict) or len(blobs) != 8:
        raise ValueError("checkpoint v25 artifact status tool blobs invalid")
    if any(not _is_hex_digest(value, 40) for value in blobs.values()):
        raise ValueError("checkpoint v25 artifact status tool blob invalid")

    stages = report.get("stages")
    if not isinstance(stages, list) or len(stages) != 8:
        raise ValueError("checkpoint v25 artifact status stages invalid")
    if [item.get("order") for item in stages] != list(range(1, 9)):
        raise ValueError("checkpoint v25 artifact status order mismatch")
    if [item.get("role") for item in stages] != list(EXPECTED_ROLES):
        raise ValueError("checkpoint v25 artifact status role mismatch")

    valid_prefix = 0
    seen_incomplete = False
    later_present = False
    invalid_present = False
    for item in stages:
        if set(item) != set(STAGE_FIELDS):
            raise ValueError("checkpoint v25 artifact status stage schema mismatch")
        status_value = item.get("validation_status")
        if status_value not in {
            "VALID",
            "MISSING",
            "UNSAFE",
            "INVALID_JSON",
            "INVALID",
        }:
            raise ValueError("checkpoint v25 artifact validation status invalid")
        if status_value == "VALID" and not seen_incomplete:
            valid_prefix += 1
        else:
            if status_value != "VALID":
                seen_incomplete = True
        if seen_incomplete and item.get("exists") is True and item["order"] > valid_prefix + 1:
            later_present = True
        if status_value in {"UNSAFE", "INVALID_JSON", "INVALID"}:
            invalid_present = True
        digest = item.get("artifact_sha256")
        if digest is not None and not _is_hex_digest(digest, 64):
            raise ValueError("checkpoint v25 artifact digest invalid")
        if item.get("validation_error_category") not in {
            None,
            "ARTIFACT_MISSING",
            "UNSAFE_ARTIFACT",
            "INVALID_JSON",
            "VALIDATOR_REJECTED",
        }:
            raise ValueError("checkpoint v25 artifact error category invalid")

    if report.get("valid_prefix_length") != valid_prefix:
        raise ValueError("checkpoint v25 artifact valid prefix mismatch")
    complete = valid_prefix == 8
    if report.get("artifact_chain_complete") is not complete:
        raise ValueError("checkpoint v25 artifact completion mismatch")
    if report.get("artifact_chain_prefix_valid") is not True:
        raise ValueError("checkpoint v25 artifact prefix flag must be true")
    if report.get("invalid_artifact_present") is not invalid_present:
        raise ValueError("checkpoint v25 invalid-artifact flag mismatch")
    if report.get("out_of_order_artifacts_present") is not later_present:
        raise ValueError("checkpoint v25 out-of-order flag mismatch")

    first = None if complete else stages[valid_prefix]
    expected_boundary = _next_boundary(
        stage=first,
        out_of_order=later_present,
    )
    if report.get("next_boundary") != expected_boundary:
        raise ValueError("checkpoint v25 artifact boundary mismatch")
    expected_first = (
        (None, None, None, None)
        if first is None
        else (
            first["order"],
            first["role"],
            first["artifact_name"],
            first["validation_status"],
        )
    )
    actual_first = (
        report.get("first_incomplete_order"),
        report.get("first_incomplete_role"),
        report.get("first_incomplete_artifact"),
        report.get("first_incomplete_status"),
    )
    if actual_first != expected_first:
        raise ValueError("checkpoint v25 first-incomplete binding mismatch")

    for field in (
        "status_only",
        "fresh_preflight_required_before_any_operator_sequence",
    ):
        if report.get(field) is not True:
            raise ValueError(f"checkpoint v25 artifact status requires {field}=true")

    for field in (
        "detached_signature_reverification_performed",
        "production_database_revalidation_performed",
        "next_action_authorized",
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
                f"checkpoint v25 artifact status requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["artifact_status_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("checkpoint v25 artifact status digest mismatch")


def build_phase8_recursive_reentry_checkpoint_v25_artifact_status(
    *,
    source_tree: str | Path,
    artifact_directory: str | Path,
) -> dict[str, Any]:
    source = _safe_directory(source_tree, label="reviewed source tree")
    artifact_dir = _safe_directory(
        artifact_directory,
        label="checkpoint v25 artifact directory",
    )
    manifest, manifest_sha = _load_manifest(source)
    tool_blobs = _verify_source_tools(source, manifest)

    stages = [
        _inspect_stage(
            source=source,
            artifact_dir=artifact_dir,
            step=step,
        )
        for step in manifest["ordered_steps"]
    ]

    valid_prefix = 0
    for stage in stages:
        if stage["validation_status"] != "VALID":
            break
        valid_prefix += 1

    first = None if valid_prefix == len(stages) else stages[valid_prefix]
    invalid_present = any(
        item["validation_status"] in {"UNSAFE", "INVALID_JSON", "INVALID"}
        for item in stages
    )
    out_of_order = (
        first is not None
        and any(item["exists"] for item in stages[first["order"] :])
    )
    boundary = _next_boundary(stage=first, out_of_order=out_of_order)

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_manifest_blob": MANIFEST_BLOB,
        "manifest_sha256": manifest_sha,
        "source_tree": str(source),
        "artifact_directory": str(artifact_dir),
        "reviewed_tool_blobs": tool_blobs,
        "stages": stages,
        "valid_prefix_length": valid_prefix,
        "artifact_chain_prefix_valid": True,
        "artifact_chain_complete": valid_prefix == len(stages),
        "invalid_artifact_present": invalid_present,
        "out_of_order_artifacts_present": out_of_order,
        "first_incomplete_order": None if first is None else first["order"],
        "first_incomplete_role": None if first is None else first["role"],
        "first_incomplete_artifact": (
            None if first is None else first["artifact_name"]
        ),
        "first_incomplete_status": (
            None if first is None else first["validation_status"]
        ),
        "next_boundary": boundary,
        "status_only": True,
        "fresh_preflight_required_before_any_operator_sequence": True,
        "detached_signature_reverification_performed": False,
        "production_database_revalidation_performed": False,
        "next_action_authorized": False,
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
        "artifact_status_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_recursive_reentry_checkpoint_v25_artifact_status(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only saved-artifact status for the checkpoint v25 operator "
            "sequence. It verifies the pinned manifest/tool blobs and applies "
            "native validators to artifacts already present in an operator "
            "directory. It does not reverify the detached SSH signature, "
            "revalidate the production database, execute a PAPER tick, or "
            "authorize any next action."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--artifact-dir", required=True)
    args = parser.parse_args()
    report = build_phase8_recursive_reentry_checkpoint_v25_artifact_status(
        source_tree=args.source_tree,
        artifact_directory=args.artifact_dir,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
