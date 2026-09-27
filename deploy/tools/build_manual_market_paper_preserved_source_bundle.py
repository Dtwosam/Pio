from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_SOURCE_BUNDLE_V1"

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESERVATION_REVIEW_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preservation_review.py"
)
RESEARCH_VALIDATOR = Path(
    "deploy/tools/validate_manual_market_paper_research_store_portable_patch.py"
)
STATE_VALIDATOR = Path(
    "deploy/tools/validate_manual_market_paper_state_reader_portable_patch.py"
)

REVIEWED_TOOL_BLOBS = {
    PRESERVATION_REVIEW_TOOL: "0d3193a89337c40e2118389694bab80372944ba2",
    RESEARCH_VALIDATOR: "e255f163f8ca5da09a81acf3bb13c6d25120079c",
    STATE_VALIDATOR: "e2d1a46d70d36f8a6338635743d5600e47b35480",
}

ENTRY_ORDER = ("RESEARCH_STORE", "STATE_READER")

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_tool_blobs",
    "preservation_review_sha256",
    "base_source_head",
    "validation_source_head",
    "base_source_matches_validation_head",
    "bundle_dir",
    "bundle_under_var_tmp",
    "entries",
    "entry_count",
    "all_patch_reports_match_preservation",
    "all_base_targets_match_reviewed_targets",
    "all_candidate_reconstructions_match",
    "bundle_ready",
    "candidate_content_in_report",
    "candidate_content_materialized_in_private_bundle",
    "production_file_modified",
    "production_repository_accessed",
    "requires_preserved_readiness_tooling",
    "requires_new_readiness_cycle",
    "requires_separate_mutation_authorization",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

ENTRY_FIELDS = (
    "kind",
    "path",
    "base_blob",
    "preserved_candidate_blob",
    "candidate_sha256",
    "candidate_size",
    "portable_patch_report_sha256",
    "portable_validation_report_sha256",
    "patch_sha256",
    "patch_size",
    "base_target_matches_reviewed_target",
    "patch_report_matches_preservation",
    "patch_bytes_match_report",
    "patch_apply_check_passed",
    "patch_applied",
    "observed_candidate_blob",
    "observed_candidate_sha256",
    "observed_candidate_size",
    "candidate_reconstruction_matches",
    "entry_ready",
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


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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


def _verify_tool_lineage() -> None:
    for relative, expected in REVIEWED_TOOL_BLOBS.items():
        path = REPO_ROOT / relative
        if not path.is_file():
            raise ValueError(f"reviewed bundle artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed bundle artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed bundle artifact mismatch: {relative}")


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _git_head(source: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(source),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError("base source tree does not expose a readable Git HEAD")
    head = proc.stdout.strip()
    if not _is_hex_digest(head, 40):
        raise ValueError("base source HEAD is invalid")
    return head


def _resolve_regular_file(raw: str | Path, *, label: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} is not a regular file")
    return resolved


def _safe_bundle_dir(raw: str | Path) -> Path:
    output = Path(raw).expanduser()
    if not output.is_absolute():
        raise ValueError("bundle output path must be absolute")
    resolved = output.resolve(strict=False)
    var_tmp = Path("/var/tmp").resolve()
    if resolved == var_tmp or var_tmp not in resolved.parents:
        raise ValueError("bundle output path must be under /var/tmp")
    if output.exists():
        raise ValueError("bundle output path already exists")
    parent = output.parent
    if parent.is_symlink():
        raise ValueError("bundle output parent must not be a symlink")
    if not parent.exists() or not parent.is_dir():
        raise ValueError("bundle output parent must already exist")
    return resolved


def _copy_base_source(source: Path, output: Path) -> None:
    shutil.copytree(
        source,
        output,
        symlinks=False,
        ignore=shutil.ignore_patterns(
            ".git",
            "target",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        ),
    )


def _apply_patch(
    *,
    workspace: Path,
    patch_file: Path,
) -> tuple[bool, bool]:
    check = subprocess.run(
        [
            "git",
            "apply",
            "--check",
            "--whitespace=error-all",
            str(patch_file),
        ],
        cwd=str(workspace),
        capture_output=True,
        check=False,
    )
    if check.returncode != 0:
        return False, False

    apply = subprocess.run(
        [
            "git",
            "apply",
            "--whitespace=error-all",
            str(patch_file),
        ],
        cwd=str(workspace),
        capture_output=True,
        check=False,
    )
    return True, apply.returncode == 0


def _validator_modules() -> tuple[Any, Any, Any]:
    _verify_tool_lineage()
    preservation = _load_module(
        REPO_ROOT / PRESERVATION_REVIEW_TOOL,
        "manual_market_paper_preserved_bundle_review",
    )
    research = _load_module(
        REPO_ROOT / RESEARCH_VALIDATOR,
        "manual_market_paper_preserved_bundle_research_validator",
    )
    state = _load_module(
        REPO_ROOT / STATE_VALIDATOR,
        "manual_market_paper_preserved_bundle_state_validator",
    )
    research._verify_reviewed_source(REPO_ROOT)
    state._verify_reviewed_source(REPO_ROOT)
    return preservation, research, state


def _exporter_module(validator: Any, name: str) -> Any:
    return _load_module(REPO_ROOT / validator.EXPORTER_TOOL, name)


def _prepare_entry(
    *,
    preservation_entry: dict[str, Any],
    validator: Any,
    patch_report_path: str | Path,
    patch_file_path: str | Path,
    base_source: Path,
    bundle: Path,
    module_name: str,
) -> dict[str, Any]:
    exporter = _exporter_module(validator, module_name)

    patch_report = _load_json(patch_report_path)
    exporter.validate_portable_patch_report(patch_report)

    if patch_report.get("report_sha256") != preservation_entry[
        "portable_patch_report_sha256"
    ]:
        raise ValueError(
            f"{preservation_entry['kind']} patch report does not match preservation review"
        )
    if patch_report.get("candidate_git_blob") != preservation_entry[
        "preserved_candidate_blob"
    ]:
        raise ValueError(
            f"{preservation_entry['kind']} patch candidate blob mismatch"
        )
    if patch_report.get("candidate_sha256") != preservation_entry[
        "candidate_sha256"
    ]:
        raise ValueError(
            f"{preservation_entry['kind']} patch candidate SHA-256 mismatch"
        )
    if patch_report.get("candidate_size") != preservation_entry[
        "candidate_size"
    ]:
        raise ValueError(
            f"{preservation_entry['kind']} patch candidate size mismatch"
        )

    patch_file = _resolve_regular_file(
        patch_file_path,
        label=f"{preservation_entry['kind']} portable patch",
    )
    patch = patch_file.read_bytes()
    patch_bytes_match = bool(
        _sha256_bytes(patch) == patch_report["patch_sha256"]
        and len(patch) == patch_report["patch_size"]
    )
    if not patch_bytes_match:
        raise ValueError(
            f"{preservation_entry['kind']} portable patch bytes do not match report"
        )

    relative = Path(preservation_entry["path"])
    base_target = base_source / relative
    if base_target.is_symlink() or not base_target.is_file():
        raise ValueError(
            f"{preservation_entry['kind']} base target is not a regular file"
        )
    observed_base_blob = _git_blob_sha(base_target)
    base_matches = (
        observed_base_blob
        == preservation_entry["superseded_reviewed_target_blob"]
        == patch_report["reviewed_target_blob"]
    )
    if not base_matches:
        raise ValueError(
            f"{preservation_entry['kind']} base target does not match reviewed target"
        )

    apply_check, applied = _apply_patch(
        workspace=bundle,
        patch_file=patch_file,
    )

    bundle_target = bundle / relative
    if applied and bundle_target.is_file() and not bundle_target.is_symlink():
        payload = bundle_target.read_bytes()
        observed_candidate_blob = _git_blob_sha_bytes(payload)
        observed_candidate_sha = _sha256_bytes(payload)
        observed_candidate_size = len(payload)
    else:
        observed_candidate_blob = None
        observed_candidate_sha = None
        observed_candidate_size = None

    candidate_matches = bool(
        applied
        and observed_candidate_blob
        == preservation_entry["preserved_candidate_blob"]
        and observed_candidate_sha == preservation_entry["candidate_sha256"]
        and observed_candidate_size == preservation_entry["candidate_size"]
    )

    return {
        "kind": preservation_entry["kind"],
        "path": preservation_entry["path"],
        "base_blob": observed_base_blob,
        "preserved_candidate_blob": preservation_entry[
            "preserved_candidate_blob"
        ],
        "candidate_sha256": preservation_entry["candidate_sha256"],
        "candidate_size": preservation_entry["candidate_size"],
        "portable_patch_report_sha256": patch_report["report_sha256"],
        "portable_validation_report_sha256": preservation_entry[
            "portable_validation_report_sha256"
        ],
        "patch_sha256": patch_report["patch_sha256"],
        "patch_size": patch_report["patch_size"],
        "base_target_matches_reviewed_target": base_matches,
        "patch_report_matches_preservation": True,
        "patch_bytes_match_report": patch_bytes_match,
        "patch_apply_check_passed": apply_check,
        "patch_applied": applied,
        "observed_candidate_blob": observed_candidate_blob,
        "observed_candidate_sha256": observed_candidate_sha,
        "observed_candidate_size": observed_candidate_size,
        "candidate_reconstruction_matches": candidate_matches,
        "entry_ready": bool(
            base_matches
            and patch_bytes_match
            and apply_check
            and applied
            and candidate_matches
        ),
    }


def validate_bundle_report(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("preserved-source bundle report must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"bundle_sha256"}:
        raise ValueError("preserved-source bundle fields do not match reviewed schema")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preserved-source bundle format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preserved-source bundle artifact type")

    expected_tool_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_TOOL_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_tool_blobs") != expected_tool_blobs:
        raise ValueError("preserved-source bundle tool lineage mismatch")

    for field, length in (
        ("preservation_review_sha256", 64),
        ("base_source_head", 40),
        ("validation_source_head", 40),
        ("bundle_sha256", 64),
    ):
        if not _is_hex_digest(report.get(field), length):
            raise ValueError(f"preserved-source bundle {field} is invalid")

    bundle_dir = report.get("bundle_dir")
    if not isinstance(bundle_dir, str) or not bundle_dir.startswith("/var/tmp/"):
        raise ValueError("preserved-source bundle path is invalid")
    if report.get("bundle_under_var_tmp") is not True:
        raise ValueError("preserved-source bundle scope is invalid")
    if report.get("base_source_matches_validation_head") is not (
        report["base_source_head"] == report["validation_source_head"]
    ):
        raise ValueError("preserved-source bundle source-head match flag mismatch")

    entries = report.get("entries")
    if not isinstance(entries, list) or len(entries) != len(ENTRY_ORDER):
        raise ValueError("preserved-source bundle entry set is invalid")
    if report.get("entry_count") != len(entries):
        raise ValueError("preserved-source bundle entry count mismatch")
    if [entry.get("kind") for entry in entries] != list(ENTRY_ORDER):
        raise ValueError("preserved-source bundle entry ordering mismatch")

    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != set(ENTRY_FIELDS):
            raise ValueError("preserved-source bundle entry schema mismatch")
        for field, length in (
            ("base_blob", 40),
            ("preserved_candidate_blob", 40),
            ("candidate_sha256", 64),
            ("portable_patch_report_sha256", 64),
            ("portable_validation_report_sha256", 64),
            ("patch_sha256", 64),
        ):
            if not _is_hex_digest(entry.get(field), length):
                raise ValueError(
                    f"preserved-source bundle {entry['kind']} {field} is invalid"
                )
        for field in ("candidate_size", "patch_size"):
            value = entry.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(
                    f"preserved-source bundle {entry['kind']} {field} is invalid"
                )
        for field in (
            "base_target_matches_reviewed_target",
            "patch_report_matches_preservation",
            "patch_bytes_match_report",
            "patch_apply_check_passed",
            "patch_applied",
            "candidate_reconstruction_matches",
            "entry_ready",
        ):
            if not isinstance(entry.get(field), bool):
                raise ValueError(
                    f"preserved-source bundle {entry['kind']} {field} must be boolean"
                )
        observed_blob = entry.get("observed_candidate_blob")
        observed_sha = entry.get("observed_candidate_sha256")
        observed_size = entry.get("observed_candidate_size")
        if entry["candidate_reconstruction_matches"]:
            if observed_blob != entry["preserved_candidate_blob"]:
                raise ValueError("preserved-source bundle observed candidate blob mismatch")
            if observed_sha != entry["candidate_sha256"]:
                raise ValueError("preserved-source bundle observed candidate SHA mismatch")
            if observed_size != entry["candidate_size"]:
                raise ValueError("preserved-source bundle observed candidate size mismatch")
        expected_ready = all(
            entry[field]
            for field in (
                "base_target_matches_reviewed_target",
                "patch_report_matches_preservation",
                "patch_bytes_match_report",
                "patch_apply_check_passed",
                "patch_applied",
                "candidate_reconstruction_matches",
            )
        )
        if entry["entry_ready"] is not expected_ready:
            raise ValueError("preserved-source bundle entry readiness mismatch")

    expected_all_patch_reports = all(
        entry["patch_report_matches_preservation"] for entry in entries
    )
    expected_all_base = all(
        entry["base_target_matches_reviewed_target"] for entry in entries
    )
    expected_all_candidates = all(
        entry["candidate_reconstruction_matches"] for entry in entries
    )
    if report.get("all_patch_reports_match_preservation") is not expected_all_patch_reports:
        raise ValueError("preserved-source bundle patch-report aggregate mismatch")
    if report.get("all_base_targets_match_reviewed_targets") is not expected_all_base:
        raise ValueError("preserved-source bundle base-target aggregate mismatch")
    if report.get("all_candidate_reconstructions_match") is not expected_all_candidates:
        raise ValueError("preserved-source bundle candidate aggregate mismatch")

    expected_ready = bool(
        report["base_source_matches_validation_head"]
        and all(entry["entry_ready"] for entry in entries)
    )
    if report.get("bundle_ready") is not expected_ready:
        raise ValueError("preserved-source bundle ready flag mismatch")

    if report.get("candidate_content_in_report") is not False:
        raise ValueError("preserved-source bundle must not embed candidate content in report")
    if report.get("candidate_content_materialized_in_private_bundle") is not True:
        raise ValueError("preserved-source bundle must record private candidate materialization")
    for field in (
        "production_file_modified",
        "production_repository_accessed",
    ):
        if report.get(field) is not False:
            raise ValueError(f"preserved-source bundle requires {field}=false")
    for field in (
        "requires_preserved_readiness_tooling",
        "requires_new_readiness_cycle",
        "requires_separate_mutation_authorization",
    ):
        if report.get(field) is not True:
            raise ValueError(f"preserved-source bundle requires {field}=true")
    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if report.get(field) is not False:
            raise ValueError(f"preserved-source bundle requires {field}=false")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if report["bundle_sha256"] != expected_digest:
        raise ValueError("preserved-source bundle digest mismatch")


def build_preserved_source_bundle(
    *,
    base_source_tree: str | Path,
    preservation_review_path: str | Path,
    research_store_patch_report: str | Path,
    research_store_patch: str | Path,
    state_reader_patch_report: str | Path,
    state_reader_patch: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    preservation_module, research_validator, state_validator = _validator_modules()

    preservation_review = _load_json(preservation_review_path)
    preservation_module.validate_preservation_review(preservation_review)
    if preservation_review.get("preservation_ready") is not True:
        raise ValueError("preservation review is not ready")

    base_source = Path(base_source_tree).resolve()
    if not base_source.is_dir():
        raise ValueError(f"base source tree is missing: {base_source}")
    base_head = _git_head(base_source)

    entries_by_kind = {
        entry["kind"]: entry
        for entry in preservation_review["entries"]
    }
    validation_heads = {
        entry["validation_source_head"]
        for entry in preservation_review["entries"]
    }
    if len(validation_heads) != 1:
        raise ValueError("preservation review validation source heads disagree")
    validation_head = next(iter(validation_heads))
    if base_head != validation_head:
        raise ValueError(
            "base source HEAD does not match portable validation source HEAD"
        )

    output = _safe_bundle_dir(output_dir)
    _copy_base_source(base_source, output)

    try:
        entries = [
            _prepare_entry(
                preservation_entry=entries_by_kind["RESEARCH_STORE"],
                validator=research_validator,
                patch_report_path=research_store_patch_report,
                patch_file_path=research_store_patch,
                base_source=base_source,
                bundle=output,
                module_name="manual_market_paper_preserved_bundle_research_exporter",
            ),
            _prepare_entry(
                preservation_entry=entries_by_kind["STATE_READER"],
                validator=state_validator,
                patch_report_path=state_reader_patch_report,
                patch_file_path=state_reader_patch,
                base_source=base_source,
                bundle=output,
                module_name="manual_market_paper_preserved_bundle_state_exporter",
            ),
        ]
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise

    all_patch_reports = all(
        entry["patch_report_matches_preservation"] for entry in entries
    )
    all_base_targets = all(
        entry["base_target_matches_reviewed_target"] for entry in entries
    )
    all_candidates = all(
        entry["candidate_reconstruction_matches"] for entry in entries
    )
    bundle_ready = bool(
        base_head == validation_head
        and all(entry["entry_ready"] for entry in entries)
    )

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "reviewed_tool_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_TOOL_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "preservation_review_sha256": preservation_review["review_sha256"],
        "base_source_head": base_head,
        "validation_source_head": validation_head,
        "base_source_matches_validation_head": base_head == validation_head,
        "bundle_dir": str(output),
        "bundle_under_var_tmp": True,
        "entries": entries,
        "entry_count": len(entries),
        "all_patch_reports_match_preservation": all_patch_reports,
        "all_base_targets_match_reviewed_targets": all_base_targets,
        "all_candidate_reconstructions_match": all_candidates,
        "bundle_ready": bundle_ready,
        "candidate_content_in_report": False,
        "candidate_content_materialized_in_private_bundle": True,
        "production_file_modified": False,
        "production_repository_accessed": False,
        "requires_preserved_readiness_tooling": True,
        "requires_new_readiness_cycle": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    report = {
        **identity,
        "bundle_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_bundle_report(report)
    if not bundle_ready:
        shutil.rmtree(output, ignore_errors=True)
        raise ValueError("preserved-source bundle failed closed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Construct a private preserved-source bundle under /var/tmp from the "
            "exact portable-validation source revision plus the two sealed private "
            "candidate patches. Candidate contents are materialized only in the "
            "private bundle; the report contains hashes/metadata only. This tool "
            "never reads or writes the production repository and does not authorize mutation."
        )
    )
    parser.add_argument("--base-source-tree", required=True)
    parser.add_argument("--preservation-review", required=True)
    parser.add_argument("--research-store-patch-report", required=True)
    parser.add_argument("--research-store-patch", required=True)
    parser.add_argument("--state-reader-patch-report", required=True)
    parser.add_argument("--state-reader-patch", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    report = build_preserved_source_bundle(
        base_source_tree=args.base_source_tree,
        preservation_review_path=args.preservation_review,
        research_store_patch_report=args.research_store_patch_report,
        research_store_patch=args.research_store_patch,
        state_reader_patch_report=args.state_reader_patch_report,
        state_reader_patch=args.state_reader_patch,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
