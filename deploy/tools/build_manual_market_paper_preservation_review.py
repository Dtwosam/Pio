from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVATION_REVIEW_V1"

RESEARCH_VALIDATOR = Path(
    "deploy/tools/validate_manual_market_paper_research_store_portable_patch.py"
)
STATE_VALIDATOR = Path(
    "deploy/tools/validate_manual_market_paper_state_reader_portable_patch.py"
)

REVIEWED_SOURCE_BLOBS = {
    RESEARCH_VALIDATOR: "e255f163f8ca5da09a81acf3bb13c6d25120079c",
    STATE_VALIDATOR: "e2d1a46d70d36f8a6338635743d5600e47b35480",
}

EXPECTED_PRODUCTION_HEAD = "ebc0b3c8405da30d88a5ee156f31bf041ffb1ad8"
RECONCILIATION_EVIDENCE_SHA256 = (
    "5ff37e0f8ee17b1593ac96cc8a678f05b13234422929a207efb581324bc69d13"
)
STATE_READER_HUNK_EVIDENCE_SHA256 = (
    "805d5e89ecc9d14157a58e5fc40e1cf3b9632b6636fc61580f157890eb4736a7"
)

PRESERVATION_SPECS = (
    {
        "kind": "RESEARCH_STORE",
        "path": "python-learner/src/meteora_learner/research_store.py",
        "production_current_blob": "f9deb47c10c88a4e1e364dd12d6c7569c3826a98",
        "superseded_reviewed_target_blob": "c9b9de5838d95a86bddffa6166b4d7a62e91cf16",
        "preserved_candidate_blob": "31bd88e3d74490f5d0b617ff4b36383e7e12e18f",
    },
    {
        "kind": "STATE_READER",
        "path": "rust-executor/src/state_reader.rs",
        "production_current_blob": "d1267db6708b91bc8cacabffcd397a380866c79a",
        "superseded_reviewed_target_blob": "30d1435af1329bca07f73d6639539b43503e84e9",
        "preserved_candidate_blob": "f54a1021cf8f89d285bde957d1f72d81857ec2fa",
    },
)

AUTHORIZATION_FIELDS = (
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
    "production_current_blob",
    "superseded_reviewed_target_blob",
    "preserved_candidate_blob",
    "candidate_sha256",
    "candidate_size",
    "portable_patch_report_sha256",
    "portable_validation_report_sha256",
    "validation_source_head",
    "validation_ready",
    "validation_blocker",
    "production_file_modified",
    "requires_separate_mutation_authorization",
    "entry_ready",
)

REVIEW_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "production_head",
    "reconciliation_evidence_sha256",
    "state_reader_hunk_evidence_sha256",
    "entries",
    "entry_count",
    "validation_source_heads_match",
    "preservation_ready",
    "blockers",
    "proposed_rebase_operations",
    "requires_reviewed_source_rebase",
    "requires_manifest_rebase",
    "requires_new_readiness_cycle",
    "candidate_content_included",
    "production_file_modified",
    "source_tree_modified",
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


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


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
            raise ValueError(f"reviewed preservation artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed preservation artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed preservation artifact mismatch: {relative}")


def _load_reviewed_modules(source: Path) -> tuple[Any, Any]:
    _verify_reviewed_source(source)
    research = _load_module(
        source / RESEARCH_VALIDATOR,
        "manual_market_paper_preservation_review_research_validator",
    )
    state = _load_module(
        source / STATE_VALIDATOR,
        "manual_market_paper_preservation_review_state_validator",
    )
    return research, state


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _entry_from_validation(
    *,
    report: dict[str, Any],
    validator: Any,
    spec: dict[str, str],
) -> dict[str, Any]:
    validator.validate_portable_validation_report(report)

    if report.get("candidate_git_blob") != spec["preserved_candidate_blob"]:
        raise ValueError(
            f"{spec['kind']} validation candidate blob does not match preserved candidate"
        )

    for field in AUTHORIZATION_FIELDS:
        if report.get(field) is not False:
            raise ValueError(
                f"{spec['kind']} validation must keep {field}=false"
            )

    if report.get("production_file_modified") is not False:
        raise ValueError(
            f"{spec['kind']} validation must not claim production modification"
        )
    if report.get("requires_separate_mutation_authorization") is not True:
        raise ValueError(
            f"{spec['kind']} validation must require separate mutation authorization"
        )

    validation_ready = report.get("validation_ready") is True
    blocker = report.get("validation_blocker")
    entry_ready = bool(validation_ready and blocker is None)

    return {
        "kind": spec["kind"],
        "path": spec["path"],
        "production_current_blob": spec["production_current_blob"],
        "superseded_reviewed_target_blob": spec["superseded_reviewed_target_blob"],
        "preserved_candidate_blob": spec["preserved_candidate_blob"],
        "candidate_sha256": report["candidate_sha256"],
        "candidate_size": report["candidate_size"],
        "portable_patch_report_sha256": report["patch_report_sha256"],
        "portable_validation_report_sha256": report["report_sha256"],
        "validation_source_head": report["reviewed_source_head"],
        "validation_ready": validation_ready,
        "validation_blocker": blocker,
        "production_file_modified": False,
        "requires_separate_mutation_authorization": True,
        "entry_ready": entry_ready,
    }


def _rebase_operation(entry: dict[str, Any]) -> dict[str, Any]:
    operation = {
        "kind": entry["kind"],
        "operation": "REVIEWED_SOURCE_TARGET_SUBSTITUTION",
        "path": entry["path"],
        "expected_production_current_blob": entry["production_current_blob"],
        "superseded_reviewed_target_blob": entry[
            "superseded_reviewed_target_blob"
        ],
        "preserved_candidate_blob": entry["preserved_candidate_blob"],
        "portable_validation_report_sha256": entry[
            "portable_validation_report_sha256"
        ],
        "requires_candidate_content_materialization": True,
        "requires_manifest_base_rebase": True,
        "requires_manifest_target_rebase": True,
        "production_mutation": False,
    }
    return {
        **operation,
        "operation_sha256": hashlib.sha256(_canonical_bytes(operation)).hexdigest(),
    }


def validate_preservation_review(review: dict[str, Any]) -> None:
    if not isinstance(review, dict):
        raise ValueError("preservation review must be a JSON object")

    expected_keys = set(REVIEW_FIELDS) | {"review_sha256"}
    if set(review) != expected_keys:
        raise ValueError("preservation review fields do not match reviewed schema")
    if review.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preservation review format")
    if review.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preservation review artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if review.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("preservation review source lineage mismatch")
    if review.get("production_head") != EXPECTED_PRODUCTION_HEAD:
        raise ValueError("preservation review production HEAD mismatch")
    if (
        review.get("reconciliation_evidence_sha256")
        != RECONCILIATION_EVIDENCE_SHA256
    ):
        raise ValueError("preservation review reconciliation evidence mismatch")
    if (
        review.get("state_reader_hunk_evidence_sha256")
        != STATE_READER_HUNK_EVIDENCE_SHA256
    ):
        raise ValueError("preservation review hunk evidence mismatch")

    entries = review.get("entries")
    if not isinstance(entries, list) or len(entries) != len(PRESERVATION_SPECS):
        raise ValueError("preservation review entry set is invalid")
    if review.get("entry_count") != len(entries):
        raise ValueError("preservation review entry count mismatch")

    for spec, entry in zip(PRESERVATION_SPECS, entries, strict=True):
        if not isinstance(entry, dict) or set(entry) != set(ENTRY_FIELDS):
            raise ValueError("preservation review entry schema mismatch")
        for field in (
            "kind",
            "path",
            "production_current_blob",
            "superseded_reviewed_target_blob",
            "preserved_candidate_blob",
        ):
            if entry[field] != spec[field]:
                raise ValueError(
                    f"preservation review {spec['kind']} {field} mismatch"
                )

        for field, length in (
            ("production_current_blob", 40),
            ("superseded_reviewed_target_blob", 40),
            ("preserved_candidate_blob", 40),
            ("candidate_sha256", 64),
            ("portable_patch_report_sha256", 64),
            ("portable_validation_report_sha256", 64),
            ("validation_source_head", 40),
        ):
            if not _is_hex_digest(entry.get(field), length):
                raise ValueError(
                    f"preservation review {spec['kind']} {field} is invalid"
                )

        candidate_size = entry.get("candidate_size")
        if (
            not isinstance(candidate_size, int)
            or isinstance(candidate_size, bool)
            or candidate_size <= 0
        ):
            raise ValueError(
                f"preservation review {spec['kind']} candidate size is invalid"
            )
        if not isinstance(entry.get("validation_ready"), bool):
            raise ValueError("preservation review validation_ready must be boolean")
        if not isinstance(entry.get("entry_ready"), bool):
            raise ValueError("preservation review entry_ready must be boolean")
        blocker = entry.get("validation_blocker")
        if blocker is not None and not isinstance(blocker, str):
            raise ValueError("preservation review validation blocker is invalid")
        expected_entry_ready = bool(
            entry["validation_ready"] and blocker is None
        )
        if entry["entry_ready"] is not expected_entry_ready:
            raise ValueError("preservation review entry readiness mismatch")
        if entry.get("production_file_modified") is not False:
            raise ValueError("preservation review entry must not modify production")
        if entry.get("requires_separate_mutation_authorization") is not True:
            raise ValueError(
                "preservation review entry must require separate mutation authorization"
            )

    source_heads_match = len(
        {entry["validation_source_head"] for entry in entries}
    ) == 1
    if review.get("validation_source_heads_match") is not source_heads_match:
        raise ValueError("preservation review source-head match flag mismatch")

    blockers = review.get("blockers")
    if not isinstance(blockers, list) or any(
        not isinstance(item, str) or not item for item in blockers
    ):
        raise ValueError("preservation review blockers are invalid")

    expected_blockers: list[str] = []
    for entry in entries:
        if not entry["entry_ready"]:
            blocker = entry["validation_blocker"] or "VALIDATION_NOT_READY"
            expected_blockers.append(f"{entry['kind']}:{blocker}")
    if not source_heads_match:
        expected_blockers.append("VALIDATION_SOURCE_HEAD_MISMATCH")
    if blockers != expected_blockers:
        raise ValueError("preservation review blocker set mismatch")

    expected_ready = not expected_blockers
    if review.get("preservation_ready") is not expected_ready:
        raise ValueError("preservation review ready flag mismatch")

    operations = review.get("proposed_rebase_operations")
    expected_operations = [_rebase_operation(entry) for entry in entries]
    if operations != expected_operations:
        raise ValueError("preservation review rebase operations mismatch")

    for field in (
        "requires_reviewed_source_rebase",
        "requires_manifest_rebase",
        "requires_new_readiness_cycle",
        "requires_separate_mutation_authorization",
    ):
        if review.get(field) is not True:
            raise ValueError(f"preservation review requires {field}=true")
    for field in (
        "candidate_content_included",
        "production_file_modified",
        "source_tree_modified",
    ):
        if review.get(field) is not False:
            raise ValueError(f"preservation review requires {field}=false")
    for field in AUTHORIZATION_FIELDS:
        if review.get(field) is not False:
            raise ValueError(f"preservation review requires {field}=false")

    if not _is_hex_digest(review.get("review_sha256"), 64):
        raise ValueError("preservation review digest is invalid")
    identity = {field: review[field] for field in REVIEW_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if review["review_sha256"] != expected_digest:
        raise ValueError("preservation review digest mismatch")


def build_preservation_review(
    *,
    source_tree: str | Path,
    research_store_validation: str | Path,
    state_reader_validation: str | Path,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")

    research_module, state_module = _load_reviewed_modules(source)
    research_report = _load_json(research_store_validation)
    state_report = _load_json(state_reader_validation)

    entries = [
        _entry_from_validation(
            report=research_report,
            validator=research_module,
            spec=PRESERVATION_SPECS[0],
        ),
        _entry_from_validation(
            report=state_report,
            validator=state_module,
            spec=PRESERVATION_SPECS[1],
        ),
    ]

    source_heads_match = len(
        {entry["validation_source_head"] for entry in entries}
    ) == 1
    blockers: list[str] = []
    for entry in entries:
        if not entry["entry_ready"]:
            blocker = entry["validation_blocker"] or "VALIDATION_NOT_READY"
            blockers.append(f"{entry['kind']}:{blocker}")
    if not source_heads_match:
        blockers.append("VALIDATION_SOURCE_HEAD_MISMATCH")

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
        "reconciliation_evidence_sha256": RECONCILIATION_EVIDENCE_SHA256,
        "state_reader_hunk_evidence_sha256": STATE_READER_HUNK_EVIDENCE_SHA256,
        "entries": entries,
        "entry_count": len(entries),
        "validation_source_heads_match": source_heads_match,
        "preservation_ready": not blockers,
        "blockers": blockers,
        "proposed_rebase_operations": [
            _rebase_operation(entry) for entry in entries
        ],
        "requires_reviewed_source_rebase": True,
        "requires_manifest_rebase": True,
        "requires_new_readiness_cycle": True,
        "candidate_content_included": False,
        "production_file_modified": False,
        "source_tree_modified": False,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    review = {
        **identity,
        "review_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_preservation_review(review)
    return review


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Combine the sealed off-host validations for the preserved "
            "research_store.py and state_reader.rs candidates into one "
            "non-mutating review. The report proposes only the exact two-file "
            "reviewed-source/manifest rebase needed before a new readiness "
            "cycle. Candidate contents are not embedded and no production or "
            "source-tree file is changed."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--research-store-validation", required=True)
    parser.add_argument("--state-reader-validation", required=True)
    args = parser.parse_args()

    review = build_preservation_review(
        source_tree=args.source_tree,
        research_store_validation=args.research_store_validation,
        state_reader_validation=args.state_reader_validation,
    )
    print(json.dumps(review, indent=2, sort_keys=True))
    if not review["preservation_ready"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
