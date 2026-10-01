from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_OPERATOR_SURFACE_INTEGRITY_V1"
)
SURFACE_MANIFEST = Path(
    "deploy/manifests/phase8-recursive-reentry-checkpoint-v25-operator-surface.json"
)
SURFACE_MANIFEST_BLOB = "ff2b075dfab5cdf544b1e3cd5df26a2583e17695"

EXPECTED_SUPPORT_ROLES = (
    "operator-preflight",
    "artifact-status",
    "evidence-handoff",
    "operator-runbook",
)
EXPECTED_CORE_ROLES = (
    "bundled-canonical-checkpoint",
    "continuation-readiness",
    "tick-request",
    "detached-authorization",
    "execution-readiness",
    "one-shot-paper-executor",
    "post-execution-audit",
    "evidence-bundle",
)
EXPECTED_SAFETY_BOUNDARY = {
    "future_checkpoint_refresh_authorized": False,
    "automatic_paper_execution_authorized": False,
    "recurring_paper_collection_authorized": False,
    "scheduler_execution_authorized": False,
    "live_submit_authorized": False,
    "transaction_submission_authorized": False,
    "new_live_capital_authorized": False,
    "continuous_promotion_authorized": False,
    "phase8_promotion_authorized": False,
}
EXPECTED_CORE_SAFETY_BOUNDARY = {
    "automatic_paper_execution_authorized": False,
    "recurring_paper_collection_authorized": False,
    "scheduler_execution_authorized": False,
    "live_submit_authorized": False,
    "transaction_submission_authorized": False,
    "new_live_capital_authorized": False,
    "continuous_promotion_authorized": False,
    "phase8_promotion_authorized": False,
}

SUPPORT_REPORT_FIELDS = (
    "role",
    "path",
    "git_blob",
    "kind",
    "read_only",
    "may_read_production_database",
    "authorizes_next_action",
    "source_verified",
)
REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_surface_manifest_blob",
    "surface_manifest_sha256",
    "source_tree",
    "core_execution_manifest_path",
    "core_execution_manifest_git_blob",
    "core_execution_manifest_verified",
    "core_mutation_boundary_role",
    "support_entries",
    "support_surface_verified",
    "source_tree_stable_during_check",
    "surface_read_only",
    "surface_is_not_execution_sequence",
    "real_v25_evidence_required_for_future_checkpoint",
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


def _regular_file(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = path.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _safe_relative_path(raw: Any, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{label} is invalid")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} is unsafe")
    return path


def _load_json_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    resolved = _regular_file(path, label=label)
    payload = resolved.read_bytes()
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value, payload


def _validate_core_manifest(value: dict[str, Any]) -> None:
    if value.get("format_version") != 1:
        raise ValueError("checkpoint v25 core manifest format mismatch")
    if value.get("artifact_type") != (
        "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_CONTINUATION_"
        "OPERATOR_MANIFEST_V1"
    ):
        raise ValueError("checkpoint v25 core manifest type mismatch")
    if value.get("checkpoint_version") != 25:
        raise ValueError("checkpoint v25 core manifest version mismatch")
    steps = value.get("ordered_steps")
    if not isinstance(steps, list) or len(steps) != len(EXPECTED_CORE_ROLES):
        raise ValueError("checkpoint v25 core manifest steps invalid")
    if [item.get("order") for item in steps] != list(range(1, 9)):
        raise ValueError("checkpoint v25 core manifest step order mismatch")
    if [item.get("role") for item in steps] != list(EXPECTED_CORE_ROLES):
        raise ValueError("checkpoint v25 core manifest roles mismatch")

    mutating = [
        item for item in steps
        if item.get("paper_state_mutation") is True
    ]
    if len(mutating) != 1:
        raise ValueError("checkpoint v25 core mutation boundary ambiguous")
    if mutating[0].get("role") != "one-shot-paper-executor":
        raise ValueError("checkpoint v25 core mutation role mismatch")
    if mutating[0].get("maximum_tick_count") != 1:
        raise ValueError("checkpoint v25 core mutation tick bound mismatch")
    if value.get("safety_boundary") != EXPECTED_CORE_SAFETY_BOUNDARY:
        raise ValueError("checkpoint v25 core safety boundary mismatch")


def _validate_surface_manifest(value: dict[str, Any]) -> None:
    if value.get("format_version") != 1:
        raise ValueError("checkpoint v25 surface manifest format mismatch")
    if value.get("artifact_type") != (
        "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_"
        "OPERATOR_SURFACE_MANIFEST_V1"
    ):
        raise ValueError("checkpoint v25 surface manifest type mismatch")
    if value.get("checkpoint_version") != 25:
        raise ValueError("checkpoint v25 surface manifest version mismatch")

    core = value.get("core_execution_manifest")
    if not isinstance(core, dict):
        raise ValueError("checkpoint v25 surface core manifest binding missing")
    if core.get("role") != "core-execution-sequence":
        raise ValueError("checkpoint v25 surface core role mismatch")
    _safe_relative_path(
        core.get("path"),
        label="checkpoint v25 surface core manifest path",
    )
    if not _is_hex_digest(core.get("git_blob"), 40):
        raise ValueError("checkpoint v25 surface core manifest blob invalid")
    if core.get("mutation_boundary_owned_by_core_manifest") is not True:
        raise ValueError("checkpoint v25 surface lost core mutation boundary")

    entries = value.get("support_entries")
    if not isinstance(entries, list) or len(entries) != 4:
        raise ValueError("checkpoint v25 surface support entries invalid")
    if [entry.get("role") for entry in entries] != list(
        EXPECTED_SUPPORT_ROLES
    ):
        raise ValueError("checkpoint v25 surface support role mismatch")

    seen_paths: set[str] = set()
    for entry in entries:
        raw = entry.get("path")
        relative = _safe_relative_path(
            raw,
            label="checkpoint v25 surface support path",
        )
        if relative.as_posix() in seen_paths:
            raise ValueError("checkpoint v25 surface support path duplicated")
        seen_paths.add(relative.as_posix())
        if not _is_hex_digest(entry.get("git_blob"), 40):
            raise ValueError("checkpoint v25 surface support blob invalid")
        if entry.get("kind") not in {"tool", "documentation"}:
            raise ValueError("checkpoint v25 surface support kind invalid")
        if entry.get("read_only") is not True:
            raise ValueError("checkpoint v25 surface support must be read-only")
        if not isinstance(entry.get("may_read_production_database"), bool):
            raise ValueError(
                "checkpoint v25 surface production-read declaration invalid"
            )
        if entry.get("authorizes_next_action") is not False:
            raise ValueError(
                "checkpoint v25 surface support cannot authorize next action"
            )

    invariants = value.get("invariants")
    if invariants != {
        "core_execution_manifest_required": True,
        "support_entries_are_not_an_execution_sequence": True,
        "support_tools_are_read_only": True,
        "support_layer_authorizes_no_next_action": True,
        "real_v25_evidence_required_for_future_checkpoint": True,
    }:
        raise ValueError("checkpoint v25 surface invariants mismatch")
    if value.get("safety_boundary") != EXPECTED_SAFETY_BOUNDARY:
        raise ValueError("checkpoint v25 surface safety boundary mismatch")


def _verify_bound_file(
    source: Path,
    *,
    raw_path: str,
    expected_blob: str,
    label: str,
) -> str:
    relative = _safe_relative_path(raw_path, label=f"{label} path")
    path = _regular_file(source / relative, label=label)
    actual = _git_blob_sha(path)
    if actual != expected_blob:
        raise ValueError(f"{label} blob mismatch")
    return actual


def _load_surface_manifest(
    source: Path,
) -> tuple[dict[str, Any], str]:
    path = _regular_file(
        source / SURFACE_MANIFEST,
        label="checkpoint v25 operator surface manifest",
    )
    payload = path.read_bytes()
    if _git_blob_sha_bytes(payload) != SURFACE_MANIFEST_BLOB:
        raise ValueError("checkpoint v25 operator surface manifest blob mismatch")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(
            "checkpoint v25 operator surface manifest must be an object"
        )
    _validate_surface_manifest(value)
    return value, _sha256_bytes(payload)


def _verify_surface_files(
    source: Path,
    manifest: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    core = manifest["core_execution_manifest"]
    core_blob = _verify_bound_file(
        source,
        raw_path=core["path"],
        expected_blob=core["git_blob"],
        label="checkpoint v25 core execution manifest",
    )
    core_value, _ = _load_json_file(
        source / Path(core["path"]),
        label="checkpoint v25 core execution manifest",
    )
    _validate_core_manifest(core_value)

    support: list[dict[str, Any]] = []
    for entry in manifest["support_entries"]:
        actual = _verify_bound_file(
            source,
            raw_path=entry["path"],
            expected_blob=entry["git_blob"],
            label=f"checkpoint v25 support {entry['role']}",
        )
        support.append(
            {
                "role": entry["role"],
                "path": entry["path"],
                "git_blob": actual,
                "kind": entry["kind"],
                "read_only": entry["read_only"],
                "may_read_production_database": entry[
                    "may_read_production_database"
                ],
                "authorizes_next_action": entry[
                    "authorizes_next_action"
                ],
                "source_verified": True,
            }
        )
    return core_blob, support


def validate_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("checkpoint v25 operator surface report must be an object")
    if set(report) != set(REPORT_FIELDS) | {"surface_integrity_sha256"}:
        raise ValueError("checkpoint v25 operator surface report schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint v25 operator surface format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected checkpoint v25 operator surface type")
    if report.get("reviewed_surface_manifest_blob") != SURFACE_MANIFEST_BLOB:
        raise ValueError("checkpoint v25 operator surface manifest lineage mismatch")

    for field in ("surface_manifest_sha256", "surface_integrity_sha256"):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"checkpoint v25 operator surface {field} invalid")
    if not isinstance(report.get("source_tree"), str) or not report["source_tree"]:
        raise ValueError("checkpoint v25 operator surface source_tree invalid")
    if not isinstance(
        report.get("core_execution_manifest_path"),
        str,
    ) or not report["core_execution_manifest_path"]:
        raise ValueError("checkpoint v25 operator surface core path invalid")
    if not _is_hex_digest(
        report.get("core_execution_manifest_git_blob"),
        40,
    ):
        raise ValueError("checkpoint v25 operator surface core blob invalid")
    if report.get("core_mutation_boundary_role") != "one-shot-paper-executor":
        raise ValueError("checkpoint v25 operator surface mutation role mismatch")

    entries = report.get("support_entries")
    if not isinstance(entries, list) or len(entries) != 4:
        raise ValueError("checkpoint v25 operator surface support report invalid")
    if [entry.get("role") for entry in entries] != list(
        EXPECTED_SUPPORT_ROLES
    ):
        raise ValueError("checkpoint v25 operator surface support order mismatch")
    for entry in entries:
        if set(entry) != set(SUPPORT_REPORT_FIELDS):
            raise ValueError("checkpoint v25 operator surface support schema mismatch")
        if not _is_hex_digest(entry.get("git_blob"), 40):
            raise ValueError("checkpoint v25 operator surface support blob invalid")
        if entry.get("read_only") is not True:
            raise ValueError("checkpoint v25 operator surface support not read-only")
        if entry.get("authorizes_next_action") is not False:
            raise ValueError(
                "checkpoint v25 operator surface support authorizes next action"
            )
        if entry.get("source_verified") is not True:
            raise ValueError(
                "checkpoint v25 operator surface support source not verified"
            )

    for field in (
        "core_execution_manifest_verified",
        "support_surface_verified",
        "source_tree_stable_during_check",
        "surface_read_only",
        "surface_is_not_execution_sequence",
        "real_v25_evidence_required_for_future_checkpoint",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"checkpoint v25 operator surface requires {field}=true"
            )

    for field in (
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
                f"checkpoint v25 operator surface requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["surface_integrity_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("checkpoint v25 operator surface digest mismatch")


def build_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
    *,
    source_tree: str | Path,
) -> dict[str, Any]:
    source = _safe_source_tree(source_tree)
    manifest, manifest_sha = _load_surface_manifest(source)

    core_before, support_before = _verify_surface_files(source, manifest)
    core_after, support_after = _verify_surface_files(source, manifest)
    if core_after != core_before or support_after != support_before:
        raise ValueError(
            "checkpoint v25 operator surface changed during integrity check"
        )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_surface_manifest_blob": SURFACE_MANIFEST_BLOB,
        "surface_manifest_sha256": manifest_sha,
        "source_tree": str(source),
        "core_execution_manifest_path": manifest[
            "core_execution_manifest"
        ]["path"],
        "core_execution_manifest_git_blob": core_before,
        "core_execution_manifest_verified": True,
        "core_mutation_boundary_role": "one-shot-paper-executor",
        "support_entries": support_before,
        "support_surface_verified": True,
        "source_tree_stable_during_check": True,
        "surface_read_only": True,
        "surface_is_not_execution_sequence": True,
        "real_v25_evidence_required_for_future_checkpoint": True,
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
        "surface_integrity_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the complete checkpoint v25 operator-facing source surface: "
            "the pinned core execution manifest plus the read-only preflight, "
            "artifact-status, evidence-handoff and runbook files. This is a "
            "source-integrity check only and authorizes no next action."
        )
    )
    parser.add_argument("--source-tree", required=True)
    args = parser.parse_args()

    report = (
        build_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
            source_tree=args.source_tree
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
