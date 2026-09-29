from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "PHASE8_POST_PHASE7_OPERATOR_HANDOFF_V1"

POST_PHASE7_TOOL = Path(
    "deploy/tools/check_phase7_controlled_live_post_promotion.py"
)
PYTHON_OPERATOR_HANDOFF = Path(
    "python-learner/src/meteora_learner/phase8_operator_handoff.py"
)
PYTHON_EVIDENCE_PLAN = Path(
    "python-learner/src/meteora_learner/phase8_evidence_plan.py"
)
PYTHON_EVIDENCE_STATUS = Path(
    "python-learner/src/meteora_learner/phase8_evidence_status.py"
)
PYTHON_CONTINUOUS_LEARNING = Path(
    "python-learner/src/meteora_learner/continuous_learning.py"
)
PYTHON_VALIDATION = Path(
    "python-learner/src/meteora_learner/phase8_validation.py"
)
PYTHON_RETRAINING_CYCLE = Path(
    "python-learner/src/meteora_learner/retraining_cycle.py"
)
PYTHON_RETRAIN_INPUTS = Path(
    "python-learner/src/meteora_learner/phase8_retrain_inputs.py"
)
PYTHON_OFFLINE_RETRAINING = Path(
    "python-learner/src/meteora_learner/phase8_offline_retraining.py"
)
PYTHON_STORAGE = Path(
    "python-learner/src/meteora_learner/storage.py"
)

REVIEWED_SOURCE_BLOBS = {
    POST_PHASE7_TOOL: "9d2b223d0c81adc6f6e01ea091a672c38b284c02",
    PYTHON_OPERATOR_HANDOFF: "17334b7ac35ed62aaead66285b4206669c7413ad",
    PYTHON_EVIDENCE_PLAN: "f8c162a61e516e61bcc4bf8554cc22a870a9f3d1",
    PYTHON_EVIDENCE_STATUS: "ea2bcf69c1c269d54ca439e56eb6eaf57d36a2b5",
    PYTHON_CONTINUOUS_LEARNING: "dc65c48dd5964a68b30f6edea85b03664f840e4e",
    PYTHON_VALIDATION: "1f712d83f440385aa16756f8e2755ef99dd1ef86",
    PYTHON_RETRAINING_CYCLE: "a02bda4a6b7a995275b186c9d71d415537197ee5",
    PYTHON_RETRAIN_INPUTS: "9059b9aa190cd9598fabbb657c183215c22aa794",
    PYTHON_OFFLINE_RETRAINING: "ac025dc201b191460c178e631b700bd2ca34ea7b",
    PYTHON_STORAGE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_phase7_post_promotion_audit_sha256",
    "expected_phase7_post_promotion_audit_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "production_source_stable_during_handoff",
    "phase7_promotion_confirmed",
    "phase7_promotion_persisted",
    "phase8_evidence_status",
    "phase8_operator_handoff",
    "phase8_phase7_dependency_satisfied",
    "phase8_research_only",
    "phase8_read_only",
    "phase8_policy_actionable",
    "phase8_execution_wired",
    "phase8_status",
    "phase8_promotion_ready",
    "phase8_persisted_current",
    "phase8_automatic_action_available",
    "phase8_operator_action_required",
    "phase8_manual_input_required",
    "phase8_handoff_ready",
    "requires_separate_phase8_action",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_authorized",
    "transaction_submission_authorized",
    "automatic_resubmission_authorized",
    "new_live_capital_authorized",
    "phase8_policy_action_authorized",
    "phase8_execution_authorized",
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


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_reviewed(source: Path) -> tuple[Any, dict[str, Any]]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 8 post-Phase 7 handoff dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 8 post-Phase 7 handoff dependency mismatch: {relative}"
            )

    post = _load_module(
        source / POST_PHASE7_TOOL,
        "phase8_post_phase7_audit",
    )

    python_src = source / "python-learner" / "src"
    if str(python_src) not in sys.path:
        sys.path.insert(0, str(python_src))

    from meteora_learner.phase8_evidence_status import (
        evaluate_phase8_evidence_status,
    )
    from meteora_learner.phase8_operator_handoff import (
        build_phase8_operator_handoff,
    )
    from meteora_learner.storage import Storage

    return post, {
        "Storage": Storage,
        "evaluate_phase8_evidence_status": evaluate_phase8_evidence_status,
        "build_phase8_operator_handoff": build_phase8_operator_handoff,
    }


def _load_json(path: str | Path, *, label: str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = candidate.resolve(strict=True)
    st = resolved.stat()
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


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    before = _database_state(source)
    if before["database"] is None:
        raise ValueError("Pio database disappeared before Phase 8 handoff")
    uri = f"file:{source.as_posix()}?mode=ro"
    if before["wal"] is None:
        uri += "&immutable=1"
    src = sqlite3.connect(uri, uri=True)
    try:
        src.execute("PRAGMA query_only=ON")
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if _database_state(source) != before:
        raise ValueError(
            "production Pio database changed during Phase 8 handoff snapshot"
        )


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ValueError("Phase 8 handoff result did not produce an object")
    return result


def validate_phase8_post_phase7_handoff(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 8 post-Phase 7 handoff must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"handoff_sha256"}:
        raise ValueError("Phase 8 post-Phase 7 handoff schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 8 post-Phase 7 handoff format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 8 post-Phase 7 handoff type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 8 post-Phase 7 handoff lineage mismatch")

    for field in (
        "saved_phase7_post_promotion_audit_sha256",
        "expected_phase7_post_promotion_audit_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "handoff_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(
                f"Phase 8 post-Phase 7 handoff {field} is invalid"
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
                f"Phase 8 post-Phase 7 handoff {field} is invalid"
            )

    if report["saved_phase7_post_promotion_audit_sha256"] != report[
        "expected_phase7_post_promotion_audit_sha256"
    ]:
        raise ValueError("Phase 8 post-Phase 7 audit digest mismatch")
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError(
            "production Pio database changed during Phase 8 handoff"
        )
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError(
            "production Pio WAL changed during Phase 8 handoff"
        )
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError(
            "production Pio SHM changed during Phase 8 handoff"
        )

    for field in (
        "production_repository",
        "pio_database_path",
        "phase8_status",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(
                f"Phase 8 post-Phase 7 handoff {field} is invalid"
            )

    status = report.get("phase8_evidence_status")
    handoff = report.get("phase8_operator_handoff")
    if not isinstance(status, dict):
        raise ValueError("Phase 8 evidence status is invalid")
    if not isinstance(handoff, dict):
        raise ValueError("Phase 8 operator handoff is invalid")

    if status.get("phase7_promoted") is not True:
        raise ValueError("Phase 8 requires persisted Phase 7 promotion")
    if status.get("research_only") is not True:
        raise ValueError("Phase 8 evidence status must remain research-only")
    if status.get("policy_actionable") is not False:
        raise ValueError("Phase 8 evidence status must not be policy-actionable")
    if status.get("execution_wired") is not False:
        raise ValueError("Phase 8 evidence status must remain execution-unwired")

    if handoff.get("research_only") is not True:
        raise ValueError("Phase 8 operator handoff must remain research-only")
    if handoff.get("read_only") is not True:
        raise ValueError("Phase 8 operator handoff must remain read-only")
    if handoff.get("policy_actionable") is not False:
        raise ValueError("Phase 8 operator handoff must not be policy-actionable")
    if handoff.get("execution_wired") is not False:
        raise ValueError("Phase 8 operator handoff must remain execution-unwired")

    bindings = {
        "phase8_phase7_dependency_satisfied": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "phase8_status": handoff.get("status"),
        "phase8_promotion_ready": bool(handoff.get("promotion_ready")),
        "phase8_persisted_current": bool(
            handoff.get("persisted_phase8_current")
        ),
        "phase8_automatic_action_available": bool(
            handoff.get("automatic_action_available")
        ),
        "phase8_operator_action_required": bool(
            handoff.get("operator_action_required")
        ),
        "phase8_manual_input_required": bool(
            handoff.get("manual_input_required")
        ),
        "requires_separate_phase8_action": handoff.get("status") != "READY",
    }
    for field, expected in bindings.items():
        if report.get(field) != expected:
            raise ValueError(
                f"Phase 8 post-Phase 7 handoff {field} binding mismatch"
            )

    for field in (
        "production_source_stable_during_handoff",
        "phase7_promotion_confirmed",
        "phase7_promotion_persisted",
        "phase8_handoff_ready",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 8 post-Phase 7 handoff requires {field}=true"
            )

    for field in (
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "phase8_policy_action_authorized",
        "phase8_execution_authorized",
        "phase8_promotion_authorized",
        "production_file_modified",
        "production_repository_git_mutated",
        "production_pio_database_modified",
    ):
        if report.get(field) is not False:
            raise ValueError(
                f"Phase 8 post-Phase 7 handoff requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["handoff_sha256"] != expected:
        raise ValueError("Phase 8 post-Phase 7 handoff digest mismatch")


def build_phase8_post_phase7_handoff(
    *,
    repository: str | Path,
    source_tree: str | Path,
    phase7_post_promotion_audit_path: str | Path,
    expected_phase7_post_promotion_audit_sha256: str,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    post_module, runtime = _load_reviewed(source)
    post = _load_json(
        phase7_post_promotion_audit_path,
        label="Phase 7 post-promotion audit",
    )
    post_module.validate_post_promotion_audit(post)
    if (
        not _is_hex_digest(expected_phase7_post_promotion_audit_sha256, 64)
        or post["post_promotion_audit_sha256"]
        != expected_phase7_post_promotion_audit_sha256
    ):
        raise ValueError("saved Phase 7 post-promotion audit digest mismatch")

    for field in (
        "post_promotion_audit_ready",
        "phase7_promotion_confirmed",
        "phase7_promotion_persisted",
        "requires_separate_phase8_workflow",
        "source_database_unchanged",
        "promotion_snapshot_read_only",
    ):
        if post.get(field) is not True:
            raise ValueError(
                f"Phase 8 handoff requires Phase 7 audit {field}=true"
            )
    for field in (
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "automatic_resubmission_authorized",
        "new_live_capital_authorized",
        "production_pio_database_modified_by_audit",
    ):
        if post.get(field) is not False:
            raise ValueError(
                f"Phase 8 handoff refuses Phase 7 audit {field}=true"
            )

    database = (production / "data" / "pio.db").resolve(strict=True)
    if Path(post["pio_database_path"]).resolve() != database:
        raise ValueError("Phase 8 handoff database binding mismatch")

    before = _database_state(database)
    if before["database"] != post["pio_database_sha256_after"]:
        raise ValueError(
            "Pio database changed after Phase 7 post-promotion audit"
        )
    if before["wal"] != post["pio_wal_sha256_after"]:
        raise ValueError(
            "Pio WAL changed after Phase 7 post-promotion audit"
        )
    if before["shm"] != post["pio_shm_sha256_after"]:
        raise ValueError(
            "Pio SHM changed after Phase 7 post-promotion audit"
        )

    with tempfile.TemporaryDirectory(
        prefix="pio-phase8-post-phase7-handoff-"
    ) as tmp:
        private_db = Path(tmp) / "pio.db"
        _snapshot_sqlite(database, private_db)
        storage = runtime["Storage"](private_db)
        phase8_status = _record(
            runtime["evaluate_phase8_evidence_status"](storage)
        )
        phase8_handoff = _record(
            runtime["build_phase8_operator_handoff"](storage)
        )

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during Phase 8 handoff"
        )

    if phase8_status.get("phase7_promoted") is not True:
        raise ValueError(
            "private Phase 8 evaluation does not see Phase 7 promoted"
        )
    if (
        phase8_status.get("research_only") is not True
        or phase8_status.get("policy_actionable") is not False
        or phase8_status.get("execution_wired") is not False
    ):
        raise ValueError("Phase 8 evidence status safety boundary changed")
    if (
        phase8_handoff.get("research_only") is not True
        or phase8_handoff.get("read_only") is not True
        or phase8_handoff.get("policy_actionable") is not False
        or phase8_handoff.get("execution_wired") is not False
    ):
        raise ValueError("Phase 8 operator handoff safety boundary changed")

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
        "saved_phase7_post_promotion_audit_sha256": post[
            "post_promotion_audit_sha256"
        ],
        "expected_phase7_post_promotion_audit_sha256": (
            expected_phase7_post_promotion_audit_sha256
        ),
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "production_source_stable_during_handoff": True,
        "phase7_promotion_confirmed": True,
        "phase7_promotion_persisted": True,
        "phase8_evidence_status": phase8_status,
        "phase8_operator_handoff": phase8_handoff,
        "phase8_phase7_dependency_satisfied": True,
        "phase8_research_only": True,
        "phase8_read_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "phase8_status": str(phase8_handoff["status"]),
        "phase8_promotion_ready": bool(
            phase8_handoff["promotion_ready"]
        ),
        "phase8_persisted_current": bool(
            phase8_handoff["persisted_phase8_current"]
        ),
        "phase8_automatic_action_available": bool(
            phase8_handoff["automatic_action_available"]
        ),
        "phase8_operator_action_required": bool(
            phase8_handoff["operator_action_required"]
        ),
        "phase8_manual_input_required": bool(
            phase8_handoff["manual_input_required"]
        ),
        "phase8_handoff_ready": True,
        "requires_separate_phase8_action": (
            phase8_handoff["status"] != "READY"
        ),
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_authorized": False,
        "transaction_submission_authorized": False,
        "automatic_resubmission_authorized": False,
        "new_live_capital_authorized": False,
        "phase8_policy_action_authorized": False,
        "phase8_execution_authorized": False,
        "phase8_promotion_authorized": False,
        "production_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": False,
    }
    report = {
        **identity,
        "handoff_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_post_phase7_handoff(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only handoff from confirmed Phase 7 promotion into the "
            "existing Phase 8 research workflow. Phase 8 is evaluated only on "
            "a private Pio DB snapshot and remains research-only, read-only, "
            "policy-nonactionable, and execution-unwired."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase7-post-promotion-audit", required=True)
    parser.add_argument(
        "--expected-phase7-post-promotion-audit-sha256",
        required=True,
    )
    args = parser.parse_args()

    report = build_phase8_post_phase7_handoff(
        repository=args.repo,
        source_tree=args.source_tree,
        phase7_post_promotion_audit_path=(
            args.phase7_post_promotion_audit
        ),
        expected_phase7_post_promotion_audit_sha256=(
            args.expected_phase7_post_promotion_audit_sha256
        ),
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
