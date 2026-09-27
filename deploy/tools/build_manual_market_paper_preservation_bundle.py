from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVATION_BUNDLE_V1"

EXPECTED_PRODUCTION_HEAD = "ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8"

RESEARCH_STORE_PATH = "python-learner/src/meteora_learner/research_store.py"
STATE_READER_PATH = "rust-executor/src/state_reader.rs"

EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB = (
    "31bd88e3d74490f5d0b617ff4b36383e7e12e18f"
)
EXPECTED_STATE_READER_CANDIDATE_BLOB = (
    "f54a1021cf8f89d285bde957d1f72d81857ec2fa"
)

RESEARCH_CANDIDATE_TOOL = Path(
    "deploy/tools/build_manual_market_paper_research_store_candidate.py"
)
RESEARCH_PATCH_TOOL = Path(
    "deploy/tools/export_manual_market_paper_research_store_candidate_patch.py"
)
RESEARCH_VALIDATION_TOOL = Path(
    "deploy/tools/validate_manual_market_paper_research_store_portable_patch.py"
)
STATE_CANDIDATE_TOOL = Path(
    "deploy/tools/build_manual_market_paper_state_reader_candidate.py"
)
STATE_PATCH_TOOL = Path(
    "deploy/tools/export_manual_market_paper_state_reader_candidate_patch.py"
)
STATE_VALIDATION_TOOL = Path(
    "deploy/tools/validate_manual_market_paper_state_reader_portable_patch.py"
)

REVIEWED_SOURCE_BLOBS = {
    RESEARCH_CANDIDATE_TOOL: "f874b5d0aca746d0d0e66622b6aef7c197e7c3b1",
    RESEARCH_PATCH_TOOL: "1c70ecdd49005fa55bc381dc9c40e705e6c9b508",
    RESEARCH_VALIDATION_TOOL: "e255f163f8ca5da09a81acf3bb13c6d25120079c",
    STATE_CANDIDATE_TOOL: "1c97fc8db482de015b2463f6d2a4823e8003628e",
    STATE_PATCH_TOOL: "c3d18d14b0b8ba983c9b26f8d3f6ade7da88d852",
    STATE_VALIDATION_TOOL: "e2d1a46d70d36f8a6338635743d5600e47b35480",
}

IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "production_head",
    "files",
    "blockers",
    "all_candidates_validated",
    "bundle_ready_for_mutation_review",
    "production_file_modified",
    "requires_fresh_production_recheck",
    "requires_separate_mutation_authorization",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
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


def _verify_reviewed_source(source: Path) -> None:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if not path.is_file():
            raise ValueError(
                f"reviewed preservation artifact is missing: {relative}"
            )
        if path.is_symlink():
            raise ValueError(
                f"reviewed preservation artifact is a symlink: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"reviewed preservation artifact mismatch: {relative}"
            )


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _require_equal(
    *,
    left: Any,
    right: Any,
    description: str,
) -> None:
    if left != right:
        raise ValueError(f"preservation chain mismatch: {description}")


def _build_chain_entry(
    *,
    candidate_module: Any,
    patch_module: Any,
    validation_module: Any,
    candidate_report: dict[str, Any],
    patch_report: dict[str, Any],
    validation_report: dict[str, Any],
    expected_path: str,
    expected_candidate_blob: str,
) -> dict[str, Any]:
    candidate_module.validate_candidate_report(candidate_report)
    patch_module.validate_portable_patch_report(patch_report)
    validation_module.validate_portable_validation_report(validation_report)

    _require_equal(
        left=candidate_report.get("production_head"),
        right=EXPECTED_PRODUCTION_HEAD,
        description=f"{expected_path} production HEAD",
    )
    _require_equal(
        left=candidate_report.get("path"),
        right=expected_path,
        description=f"{expected_path} candidate path",
    )
    _require_equal(
        left=candidate_report.get("candidate_git_blob"),
        right=expected_candidate_blob,
        description=f"{expected_path} candidate blob",
    )

    _require_equal(
        left=patch_report.get("candidate_report_sha256"),
        right=candidate_report.get("report_sha256"),
        description=f"{expected_path} candidate -> patch report",
    )
    for field in ("candidate_git_blob", "candidate_sha256", "candidate_size"):
        _require_equal(
            left=patch_report.get(field),
            right=candidate_report.get(field),
            description=f"{expected_path} patch {field}",
        )
    _require_equal(
        left=patch_report.get("reviewed_target_blob"),
        right=candidate_report.get("target_blob"),
        description=f"{expected_path} reviewed target",
    )

    _require_equal(
        left=validation_report.get("patch_report_sha256"),
        right=patch_report.get("report_sha256"),
        description=f"{expected_path} patch -> validation report",
    )
    _require_equal(
        left=validation_report.get("patch_sha256"),
        right=patch_report.get("patch_sha256"),
        description=f"{expected_path} validation patch digest",
    )
    _require_equal(
        left=validation_report.get("patch_size"),
        right=patch_report.get("patch_size"),
        description=f"{expected_path} validation patch size",
    )
    for field in ("candidate_git_blob", "candidate_sha256", "candidate_size"):
        _require_equal(
            left=validation_report.get(field),
            right=patch_report.get(field),
            description=f"{expected_path} validation {field}",
        )

    return {
        "path": expected_path,
        "current_blob": candidate_report["current_blob"],
        "reviewed_target_blob": candidate_report["target_blob"],
        "preserved_candidate_blob": candidate_report["candidate_git_blob"],
        "candidate_sha256": candidate_report["candidate_sha256"],
        "candidate_size": candidate_report["candidate_size"],
        "candidate_report_sha256": candidate_report["report_sha256"],
        "patch_sha256": patch_report["patch_sha256"],
        "patch_size": patch_report["patch_size"],
        "patch_report_sha256": patch_report["report_sha256"],
        "validation_report_sha256": validation_report["report_sha256"],
        "validation_reviewed_source_head": validation_report[
            "reviewed_source_head"
        ],
        "validation_ready": validation_report["validation_ready"],
        "validation_blocker": validation_report["validation_blocker"],
    }


def validate_preservation_bundle(bundle: dict[str, Any]) -> None:
    if not isinstance(bundle, dict):
        raise ValueError("preservation bundle must be a JSON object")

    expected_keys = set(IDENTITY_FIELDS) | {"bundle_sha256"}
    if set(bundle) != expected_keys:
        raise ValueError("preservation bundle fields do not match reviewed schema")
    if bundle.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preservation bundle format")
    if bundle.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preservation bundle artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if bundle.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("preservation bundle source lineage mismatch")
    if bundle.get("production_head") != EXPECTED_PRODUCTION_HEAD:
        raise ValueError("preservation bundle production HEAD mismatch")

    files = bundle.get("files")
    if not isinstance(files, list) or len(files) != 2:
        raise ValueError("preservation bundle must contain exactly two files")

    expected = (
        (RESEARCH_STORE_PATH, EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB),
        (STATE_READER_PATH, EXPECTED_STATE_READER_CANDIDATE_BLOB),
    )
    if [item.get("path") for item in files] != [item[0] for item in expected]:
        raise ValueError("preservation bundle file order/scope mismatch")

    for item, (expected_path, expected_blob) in zip(
        files,
        expected,
        strict=True,
    ):
        if not isinstance(item, dict):
            raise ValueError("preservation bundle file entry must be an object")
        if set(item) != {
            "path",
            "current_blob",
            "reviewed_target_blob",
            "preserved_candidate_blob",
            "candidate_sha256",
            "candidate_size",
            "candidate_report_sha256",
            "patch_sha256",
            "patch_size",
            "patch_report_sha256",
            "validation_report_sha256",
            "validation_reviewed_source_head",
            "validation_ready",
            "validation_blocker",
        }:
            raise ValueError("preservation bundle file entry schema mismatch")

        if item["path"] != expected_path:
            raise ValueError("preservation bundle file path mismatch")
        if item["preserved_candidate_blob"] != expected_blob:
            raise ValueError(
                f"preservation bundle candidate blob mismatch: {expected_path}"
            )

        for field, length in (
            ("current_blob", 40),
            ("reviewed_target_blob", 40),
            ("preserved_candidate_blob", 40),
            ("candidate_sha256", 64),
            ("candidate_report_sha256", 64),
            ("patch_sha256", 64),
            ("patch_report_sha256", 64),
            ("validation_report_sha256", 64),
            ("validation_reviewed_source_head", 40),
        ):
            if not _is_hex_digest(item.get(field), length):
                raise ValueError(
                    f"preservation bundle {field} is invalid: {expected_path}"
                )

        for field in ("candidate_size", "patch_size"):
            value = item.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise ValueError(
                    f"preservation bundle {field} is invalid: {expected_path}"
                )

        if not isinstance(item.get("validation_ready"), bool):
            raise ValueError(
                f"preservation bundle validation flag is invalid: {expected_path}"
            )
        blocker = item.get("validation_blocker")
        if item["validation_ready"]:
            if blocker is not None:
                raise ValueError(
                    f"ready preservation entry has a blocker: {expected_path}"
                )
        elif not isinstance(blocker, str) or not blocker:
            raise ValueError(
                f"non-ready preservation entry requires a blocker: {expected_path}"
            )

    expected_blockers = [
        f"{item['path']}:{item['validation_blocker']}"
        for item in files
        if not item["validation_ready"]
    ]
    if bundle.get("blockers") != expected_blockers:
        raise ValueError("preservation bundle blockers mismatch")

    all_validated = all(item["validation_ready"] for item in files)
    if bundle.get("all_candidates_validated") is not all_validated:
        raise ValueError("preservation bundle aggregate validation mismatch")
    if bundle.get("bundle_ready_for_mutation_review") is not all_validated:
        raise ValueError("preservation bundle readiness mismatch")

    if bundle.get("production_file_modified") is not False:
        raise ValueError("preservation bundle must not modify production")
    if bundle.get("requires_fresh_production_recheck") is not True:
        raise ValueError(
            "preservation bundle must require a fresh production recheck"
        )
    if bundle.get("requires_separate_mutation_authorization") is not True:
        raise ValueError(
            "preservation bundle must require separate mutation authorization"
        )

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if bundle.get(field) is not False:
            raise ValueError(f"preservation bundle requires {field}=false")

    if not _is_hex_digest(bundle.get("bundle_sha256"), 64):
        raise ValueError("preservation bundle digest is invalid")
    identity = {field: bundle[field] for field in IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if bundle["bundle_sha256"] != expected_digest:
        raise ValueError("preservation bundle digest mismatch")


def build_preservation_bundle(
    *,
    source_tree: str | Path,
    research_candidate_report_path: str | Path,
    research_patch_report_path: str | Path,
    research_validation_report_path: str | Path,
    state_candidate_report_path: str | Path,
    state_patch_report_path: str | Path,
    state_validation_report_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")
    _verify_reviewed_source(source)

    research_candidate_module = _load_module(
        source / RESEARCH_CANDIDATE_TOOL,
        "manual_market_paper_bundle_research_candidate",
    )
    research_patch_module = _load_module(
        source / RESEARCH_PATCH_TOOL,
        "manual_market_paper_bundle_research_patch",
    )
    research_validation_module = _load_module(
        source / RESEARCH_VALIDATION_TOOL,
        "manual_market_paper_bundle_research_validation",
    )
    state_candidate_module = _load_module(
        source / STATE_CANDIDATE_TOOL,
        "manual_market_paper_bundle_state_candidate",
    )
    state_patch_module = _load_module(
        source / STATE_PATCH_TOOL,
        "manual_market_paper_bundle_state_patch",
    )
    state_validation_module = _load_module(
        source / STATE_VALIDATION_TOOL,
        "manual_market_paper_bundle_state_validation",
    )

    research_candidate = _load_json(research_candidate_report_path)
    research_patch = _load_json(research_patch_report_path)
    research_validation = _load_json(research_validation_report_path)
    state_candidate = _load_json(state_candidate_report_path)
    state_patch = _load_json(state_patch_report_path)
    state_validation = _load_json(state_validation_report_path)

    files = [
        _build_chain_entry(
            candidate_module=research_candidate_module,
            patch_module=research_patch_module,
            validation_module=research_validation_module,
            candidate_report=research_candidate,
            patch_report=research_patch,
            validation_report=research_validation,
            expected_path=RESEARCH_STORE_PATH,
            expected_candidate_blob=EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
        ),
        _build_chain_entry(
            candidate_module=state_candidate_module,
            patch_module=state_patch_module,
            validation_module=state_validation_module,
            candidate_report=state_candidate,
            patch_report=state_patch,
            validation_report=state_validation,
            expected_path=STATE_READER_PATH,
            expected_candidate_blob=EXPECTED_STATE_READER_CANDIDATE_BLOB,
        ),
    ]

    blockers = [
        f"{item['path']}:{item['validation_blocker']}"
        for item in files
        if not item["validation_ready"]
    ]
    all_validated = not blockers

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
        "production_head": EXPECTED_PRODUCTION_HEAD,
        "files": files,
        "blockers": blockers,
        "all_candidates_validated": all_validated,
        "bundle_ready_for_mutation_review": all_validated,
        "production_file_modified": False,
        "requires_fresh_production_recheck": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    bundle = {
        **identity,
        "bundle_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_preservation_bundle(bundle)
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Seal the two preserved manual market/PAPER candidates into one "
            "digest-only review bundle. The tool validates candidate -> patch -> "
            "off-host validation lineage for research_store.py and state_reader.rs. "
            "It has no production repository input and authorizes no mutation."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--research-candidate-report", required=True)
    parser.add_argument("--research-patch-report", required=True)
    parser.add_argument("--research-validation-report", required=True)
    parser.add_argument("--state-candidate-report", required=True)
    parser.add_argument("--state-patch-report", required=True)
    parser.add_argument("--state-validation-report", required=True)
    args = parser.parse_args()

    bundle = build_preservation_bundle(
        source_tree=args.source_tree,
        research_candidate_report_path=args.research_candidate_report,
        research_patch_report_path=args.research_patch_report,
        research_validation_report_path=args.research_validation_report,
        state_candidate_report_path=args.state_candidate_report,
        state_patch_report_path=args.state_patch_report,
        state_validation_report_path=args.state_validation_report,
    )
    print(json.dumps(bundle, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
