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
    "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_OPERATOR_ARCHIVE_SURFACE_INTEGRITY_V1"
)
ARCHIVE_MANIFEST = Path(
    "deploy/manifests/phase8-recursive-reentry-checkpoint-v25-operator-archive-surface.json"
)
ARCHIVE_MANIFEST_BLOB = "e640057a707dcf82107b07cf1186c5f93d5d8cbf"

EXPECTED_ARCHIVE_ROLES = (
    "operator-session-archive",
    "signing-material-archive",
    "operator-archive-runbook",
)
EXPECTED_SAFETY_BOUNDARY = {
    "authorization_currently_reusable": False,
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

ENTRY_FIELDS = (
    "role",
    "path",
    "git_blob",
    "kind",
    "read_only",
    "historical_only",
    "authorizes_next_action",
    "source_verified",
)
REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_archive_manifest_blob",
    "archive_manifest_sha256",
    "source_tree",
    "base_operator_surface_manifest_path",
    "base_operator_surface_manifest_git_blob",
    "base_operator_surface_checker_path",
    "base_operator_surface_checker_git_blob",
    "base_surface_integrity_sha256",
    "base_operator_surface_verified",
    "archive_entries",
    "archive_surface_verified",
    "source_tree_stable_during_check",
    "archive_layer_read_only",
    "archive_layer_historical_only",
    "archive_layer_is_not_execution_sequence",
    "archive_layer_authorizes_no_next_action",
    "raw_signing_materials_must_bind_to_sealed_session",
    "real_v25_evidence_required",
    "authorization_currently_reusable",
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


def _safe_relative_path(raw: Any, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{label} is invalid")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} is unsafe")
    return path


def _regular_file(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = path.resolve(strict=True)
    st = os.lstat(resolved)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _validate_archive_manifest(value: dict[str, Any]) -> None:
    if value.get("format_version") != 1:
        raise ValueError("checkpoint v25 archive surface manifest format mismatch")
    if value.get("artifact_type") != (
        "PHASE8_RECURSIVE_REENTRY_CHECKPOINT_V25_"
        "OPERATOR_ARCHIVE_SURFACE_MANIFEST_V1"
    ):
        raise ValueError("checkpoint v25 archive surface manifest type mismatch")
    if value.get("checkpoint_version") != 25:
        raise ValueError("checkpoint v25 archive surface manifest version mismatch")

    base = value.get("base_operator_surface")
    if not isinstance(base, dict):
        raise ValueError("checkpoint v25 archive base surface binding missing")
    for field in ("manifest_path", "checker_path"):
        _safe_relative_path(
            base.get(field),
            label=f"checkpoint v25 archive base {field}",
        )
    for field in ("manifest_git_blob", "checker_git_blob"):
        if not _is_hex_digest(base.get(field), 40):
            raise ValueError(
                f"checkpoint v25 archive base {field} invalid"
            )
    if base.get("must_verify_before_archive") is not True:
        raise ValueError("checkpoint v25 archive base surface must verify first")

    entries = value.get("archive_entries")
    if not isinstance(entries, list) or len(entries) != 3:
        raise ValueError("checkpoint v25 archive entries invalid")
    if [item.get("role") for item in entries] != list(EXPECTED_ARCHIVE_ROLES):
        raise ValueError("checkpoint v25 archive role order mismatch")
    seen: set[str] = set()
    for entry in entries:
        relative = _safe_relative_path(
            entry.get("path"),
            label="checkpoint v25 archive entry path",
        )
        normalized = relative.as_posix()
        if normalized in seen:
            raise ValueError("checkpoint v25 archive entry path duplicated")
        seen.add(normalized)
        if not _is_hex_digest(entry.get("git_blob"), 40):
            raise ValueError("checkpoint v25 archive entry blob invalid")
        if entry.get("kind") not in {"tool", "documentation"}:
            raise ValueError("checkpoint v25 archive entry kind invalid")
        if entry.get("read_only") is not True:
            raise ValueError("checkpoint v25 archive entry must be read-only")
        if entry.get("historical_only") is not True:
            raise ValueError("checkpoint v25 archive entry must be historical")
        if entry.get("authorizes_next_action") is not False:
            raise ValueError("checkpoint v25 archive entry cannot authorize action")

    if value.get("invariants") != {
        "base_operator_surface_must_remain_sealed": True,
        "archive_layer_is_not_execution_sequence": True,
        "archive_layer_is_historical_only": True,
        "archive_layer_authorizes_no_next_action": True,
        "raw_signing_materials_must_bind_to_sealed_session": True,
        "real_v25_evidence_required": True,
    }:
        raise ValueError("checkpoint v25 archive invariants mismatch")
    if value.get("safety_boundary") != EXPECTED_SAFETY_BOUNDARY:
        raise ValueError("checkpoint v25 archive safety boundary mismatch")


def _load_archive_manifest(source: Path) -> tuple[dict[str, Any], str]:
    path = _regular_file(
        source / ARCHIVE_MANIFEST,
        label="checkpoint v25 archive surface manifest",
    )
    payload = path.read_bytes()
    if _git_blob_sha_bytes(payload) != ARCHIVE_MANIFEST_BLOB:
        raise ValueError("checkpoint v25 archive surface manifest blob mismatch")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("checkpoint v25 archive surface manifest must be an object")
    _validate_archive_manifest(value)
    return value, _sha256_bytes(payload)


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


def _verify_base_surface(
    source: Path,
    manifest: dict[str, Any],
) -> tuple[str, str, str]:
    base = manifest["base_operator_surface"]
    manifest_blob = _verify_bound_file(
        source,
        raw_path=base["manifest_path"],
        expected_blob=base["manifest_git_blob"],
        label="checkpoint v25 base operator surface manifest",
    )
    checker_blob = _verify_bound_file(
        source,
        raw_path=base["checker_path"],
        expected_blob=base["checker_git_blob"],
        label="checkpoint v25 base operator surface checker",
    )
    checker = _load_module(
        source / Path(base["checker_path"]),
        "phase8_v25_archive_surface_base_checker",
    )
    report = (
        checker.build_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
            source_tree=source
        )
    )
    checker.validate_phase8_recursive_reentry_checkpoint_v25_operator_surface_integrity(
        report
    )
    if report.get("support_surface_verified") is not True:
        raise ValueError("checkpoint v25 base operator surface is not verified")
    if report.get("core_execution_manifest_verified") is not True:
        raise ValueError("checkpoint v25 base core manifest is not verified")
    if report.get("next_action_authorized") is not False:
        raise ValueError("checkpoint v25 base operator surface authorizes action")
    return manifest_blob, checker_blob, report["surface_integrity_sha256"]


def _verify_archive_entries(
    source: Path,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in manifest["archive_entries"]:
        actual = _verify_bound_file(
            source,
            raw_path=entry["path"],
            expected_blob=entry["git_blob"],
            label=f"checkpoint v25 archive {entry['role']}",
        )
        result.append(
            {
                "role": entry["role"],
                "path": entry["path"],
                "git_blob": actual,
                "kind": entry["kind"],
                "read_only": entry["read_only"],
                "historical_only": entry["historical_only"],
                "authorizes_next_action": entry["authorizes_next_action"],
                "source_verified": True,
            }
        )
    return result


def validate_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("checkpoint v25 archive surface report must be an object")
    if set(report) != set(REPORT_FIELDS) | {"archive_surface_sha256"}:
        raise ValueError("checkpoint v25 archive surface report schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint v25 archive surface format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected checkpoint v25 archive surface type")
    if report.get("reviewed_archive_manifest_blob") != ARCHIVE_MANIFEST_BLOB:
        raise ValueError("checkpoint v25 archive surface manifest lineage mismatch")

    for field in (
        "archive_manifest_sha256",
        "base_surface_integrity_sha256",
        "archive_surface_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"checkpoint v25 archive surface {field} invalid")
    for field in (
        "base_operator_surface_manifest_git_blob",
        "base_operator_surface_checker_git_blob",
    ):
        if not _is_hex_digest(report.get(field), 40):
            raise ValueError(f"checkpoint v25 archive surface {field} invalid")
    for field in (
        "source_tree",
        "base_operator_surface_manifest_path",
        "base_operator_surface_checker_path",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"checkpoint v25 archive surface {field} invalid")

    entries = report.get("archive_entries")
    if not isinstance(entries, list) or len(entries) != 3:
        raise ValueError("checkpoint v25 archive surface entries invalid")
    if [item.get("role") for item in entries] != list(EXPECTED_ARCHIVE_ROLES):
        raise ValueError("checkpoint v25 archive surface role mismatch")
    for entry in entries:
        if set(entry) != set(ENTRY_FIELDS):
            raise ValueError("checkpoint v25 archive surface entry schema mismatch")
        if not _is_hex_digest(entry.get("git_blob"), 40):
            raise ValueError("checkpoint v25 archive surface entry blob invalid")
        for field in ("read_only", "historical_only", "source_verified"):
            if entry.get(field) is not True:
                raise ValueError(
                    f"checkpoint v25 archive surface entry requires {field}=true"
                )
        if entry.get("authorizes_next_action") is not False:
            raise ValueError("checkpoint v25 archive surface entry authorizes action")

    for field in (
        "base_operator_surface_verified",
        "archive_surface_verified",
        "source_tree_stable_during_check",
        "archive_layer_read_only",
        "archive_layer_historical_only",
        "archive_layer_is_not_execution_sequence",
        "archive_layer_authorizes_no_next_action",
        "raw_signing_materials_must_bind_to_sealed_session",
        "real_v25_evidence_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"checkpoint v25 archive surface requires {field}=true"
            )

    for field in (
        "authorization_currently_reusable",
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
                f"checkpoint v25 archive surface requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    if report["archive_surface_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError("checkpoint v25 archive surface digest mismatch")


def build_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
    *,
    source_tree: str | Path,
) -> dict[str, Any]:
    source = _safe_source_tree(source_tree)
    manifest, manifest_sha = _load_archive_manifest(source)

    base_manifest_before, base_checker_before, base_digest_before = (
        _verify_base_surface(source, manifest)
    )
    entries_before = _verify_archive_entries(source, manifest)

    base_manifest_after, base_checker_after, base_digest_after = (
        _verify_base_surface(source, manifest)
    )
    entries_after = _verify_archive_entries(source, manifest)

    if (
        base_manifest_after != base_manifest_before
        or base_checker_after != base_checker_before
        or base_digest_after != base_digest_before
        or entries_after != entries_before
    ):
        raise ValueError(
            "checkpoint v25 archival source surface changed during integrity check"
        )

    base = manifest["base_operator_surface"]
    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_archive_manifest_blob": ARCHIVE_MANIFEST_BLOB,
        "archive_manifest_sha256": manifest_sha,
        "source_tree": str(source),
        "base_operator_surface_manifest_path": base["manifest_path"],
        "base_operator_surface_manifest_git_blob": base_manifest_before,
        "base_operator_surface_checker_path": base["checker_path"],
        "base_operator_surface_checker_git_blob": base_checker_before,
        "base_surface_integrity_sha256": base_digest_before,
        "base_operator_surface_verified": True,
        "archive_entries": entries_before,
        "archive_surface_verified": True,
        "source_tree_stable_during_check": True,
        "archive_layer_read_only": True,
        "archive_layer_historical_only": True,
        "archive_layer_is_not_execution_sequence": True,
        "archive_layer_authorizes_no_next_action": True,
        "raw_signing_materials_must_bind_to_sealed_session": True,
        "real_v25_evidence_required": True,
        "authorization_currently_reusable": False,
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
        "archive_surface_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
        report
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the terminal checkpoint v25 archival source surface: the "
            "already-sealed operator surface plus the session archive, raw "
            "signing-material archive, and archival runbook. This is source-"
            "integrity validation only and authorizes no next action."
        )
    )
    parser.add_argument("--source-tree", required=True)
    args = parser.parse_args()

    report = (
        build_phase8_recursive_reentry_checkpoint_v25_operator_archive_surface(
            source_tree=args.source_tree
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
