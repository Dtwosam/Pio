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
ARTIFACT_TYPE = "PHASE8_OFFLINE_EVIDENCE_REPLAY_PLAN_V1"

HANDOFF_TOOL = Path(
    "deploy/tools/build_phase8_post_phase7_operator_handoff.py"
)
EVIDENCE_RUN = Path(
    "python-learner/src/meteora_learner/phase8_evidence_run.py"
)
EVIDENCE_STEP = Path(
    "python-learner/src/meteora_learner/phase8_evidence_step.py"
)
OFFLINE_RETRAINING = Path(
    "python-learner/src/meteora_learner/phase8_offline_retraining.py"
)
RETRAIN_INPUTS = Path(
    "python-learner/src/meteora_learner/phase8_retrain_inputs.py"
)
EVIDENCE_PLAN = Path(
    "python-learner/src/meteora_learner/phase8_evidence_plan.py"
)
EVIDENCE_STATUS = Path(
    "python-learner/src/meteora_learner/phase8_evidence_status.py"
)
STORAGE_MODULE = Path(
    "python-learner/src/meteora_learner/storage.py"
)

REVIEWED_SOURCE_BLOBS = {
    HANDOFF_TOOL: "60d7bee1b76739b8540a8a345c77dc30eea28a78",
    EVIDENCE_RUN: "74b7fc83b52729ede36c401267f27deb430966d7",
    EVIDENCE_STEP: "887924565f43854141300bfea3a437fd40eeb354",
    OFFLINE_RETRAINING: "ac025dc201b191460c178e631b700bd2ca34ea7b",
    RETRAIN_INPUTS: "9059b9aa190cd9598fabbb657c183215c22aa794",
    EVIDENCE_PLAN: "f8c162a61e516e61bcc4bf8554cc22a870a9f3d1",
    EVIDENCE_STATUS: "ea2bcf69c1c269d54ca439e56eb6eaf57d36a2b5",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

PRIVATE_ROOT_TOKEN = "<PRIVATE_PHASE8_REPLAY_ROOT>"

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_phase8_handoff_sha256",
    "expected_phase8_handoff_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "production_database_unchanged",
    "phase7_promotion_confirmed",
    "phase8_handoff_ready",
    "phase8_research_only",
    "phase8_policy_actionable",
    "phase8_execution_wired",
    "max_steps",
    "private_database_sha256_before",
    "private_database_sha256_after",
    "private_database_changed",
    "private_artifacts",
    "private_artifact_count",
    "replay_report",
    "replay_status",
    "steps_attempted",
    "steps_progressed",
    "terminal_debt_type",
    "terminal_scope",
    "promotion_ready_after_replay",
    "phase8_persisted_current_after_replay",
    "replay_plan_ready",
    "replay_is_advisory_only",
    "separate_phase8_mutation_action_required",
    "paper_trading_authorized",
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


def _load_reviewed(source: Path) -> tuple[Any, dict[str, Any]]:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 8 replay dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 8 replay dependency mismatch: {relative}"
            )

    handoff_module = _load_module(
        source / HANDOFF_TOOL,
        "phase8_replay_handoff",
    )

    python_src = source / "python-learner" / "src"
    if str(python_src) not in sys.path:
        sys.path.insert(0, str(python_src))

    from meteora_learner.phase8_evidence_run import (
        run_phase8_evidence_until_blocked,
    )
    from meteora_learner.storage import Storage

    return handoff_module, {
        "Storage": Storage,
        "run_phase8_evidence_until_blocked": (
            run_phase8_evidence_until_blocked
        ),
    }


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
        raise ValueError("Pio database disappeared before Phase 8 replay")
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
            "production Pio database changed during Phase 8 replay snapshot"
        )


def _record(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_record"):
        result = value.to_record()
    else:
        result = asdict(value)
    if not isinstance(result, dict):
        raise ValueError("Phase 8 replay result did not produce an object")
    return result


def _normalize_private_paths(value: Any, root: Path) -> Any:
    prefix = str(root.resolve())
    if isinstance(value, dict):
        return {
            str(key): _normalize_private_paths(item, root)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _normalize_private_paths(item, root)
            for item in value
        ]
    if isinstance(value, str) and value.startswith(prefix):
        return PRIVATE_ROOT_TOKEN + value[len(prefix):]
    return value


def _private_artifacts(root: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    excluded = {"pio.db", "pio.db-wal", "pio.db-shm"}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.as_posix() in excluded:
            continue
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(
                f"Phase 8 replay produced unsafe symlink: {relative}"
            )
        if stat.S_ISDIR(st.st_mode):
            continue
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(
                f"Phase 8 replay produced unsafe file type: {relative}"
            )
        payload = path.read_bytes()
        artifacts.append(
            {
                "path": relative.as_posix(),
                "size_bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )
    return artifacts


def validate_phase8_offline_replay_plan(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 8 replay plan must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"replay_plan_sha256"}:
        raise ValueError("Phase 8 replay plan schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 8 replay plan format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 8 replay plan type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 8 replay plan lineage mismatch")

    for field in (
        "saved_phase8_handoff_sha256",
        "expected_phase8_handoff_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "private_database_sha256_before",
        "private_database_sha256_after",
        "replay_plan_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 8 replay plan {field} is invalid")
    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 8 replay plan {field} is invalid")

    if report["saved_phase8_handoff_sha256"] != report[
        "expected_phase8_handoff_sha256"
    ]:
        raise ValueError("Phase 8 replay handoff digest mismatch")
    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError("production Pio database changed during replay")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("production Pio WAL changed during replay")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("production Pio SHM changed during replay")

    for field in (
        "production_database_unchanged",
        "phase7_promotion_confirmed",
        "phase8_handoff_ready",
        "phase8_research_only",
        "replay_plan_ready",
        "replay_is_advisory_only",
        "separate_phase8_mutation_action_required",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 8 replay plan requires {field}=true"
            )

    for field in (
        "phase8_policy_actionable",
        "phase8_execution_wired",
        "paper_trading_authorized",
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
                f"Phase 8 replay plan requires {field}=false"
            )

    max_steps = report.get("max_steps")
    if (
        not isinstance(max_steps, int)
        or isinstance(max_steps, bool)
        or max_steps < 1
        or max_steps > 8
    ):
        raise ValueError("Phase 8 replay max_steps is invalid")

    for field in ("steps_attempted", "steps_progressed"):
        value = report.get(field)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > max_steps
        ):
            raise ValueError(f"Phase 8 replay plan {field} is invalid")
    if report["steps_progressed"] > report["steps_attempted"]:
        raise ValueError("Phase 8 replay progressed step count is invalid")

    if not isinstance(report.get("replay_report"), dict):
        raise ValueError("Phase 8 replay report is invalid")
    if report["replay_report"].get("research_only") is not True:
        raise ValueError("Phase 8 replay must remain research-only")
    if report["replay_report"].get("offline_only") is not True:
        raise ValueError("Phase 8 replay must remain offline-only")
    if report["replay_report"].get("policy_actionable") is not False:
        raise ValueError("Phase 8 replay must not be policy-actionable")
    if report["replay_report"].get("execution_wired") is not False:
        raise ValueError("Phase 8 replay must remain execution-unwired")

    bindings = {
        "replay_status": report["replay_report"].get("status"),
        "steps_attempted": report["replay_report"].get("steps_attempted"),
        "steps_progressed": report["replay_report"].get("steps_progressed"),
        "terminal_debt_type": report["replay_report"].get(
            "terminal_debt_type"
        ),
        "terminal_scope": report["replay_report"].get("terminal_scope"),
        "promotion_ready_after_replay": bool(
            report["replay_report"].get("promotion_ready")
        ),
        "phase8_persisted_current_after_replay": bool(
            report["replay_report"].get("persisted_phase8_current")
        ),
        "private_artifact_count": len(report.get("private_artifacts") or []),
    }
    for field, expected in bindings.items():
        if report.get(field) != expected:
            raise ValueError(
                f"Phase 8 replay plan {field} binding mismatch"
            )

    artifacts = report.get("private_artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Phase 8 replay private artifacts are invalid")
    for item in artifacts:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "size_bytes", "sha256"}
            or not isinstance(item["path"], str)
            or not item["path"]
            or item["path"].startswith("/")
            or ".." in Path(item["path"]).parts
            or not isinstance(item["size_bytes"], int)
            or item["size_bytes"] < 0
            or not _is_hex_digest(item["sha256"], 64)
        ):
            raise ValueError("Phase 8 replay private artifact entry is invalid")

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["replay_plan_sha256"] != expected:
        raise ValueError("Phase 8 replay plan digest mismatch")


def build_phase8_offline_replay_plan(
    *,
    repository: str | Path,
    source_tree: str | Path,
    phase8_handoff_path: str | Path,
    expected_phase8_handoff_sha256: str,
    max_steps: int = 4,
) -> dict[str, Any]:
    if (
        not isinstance(max_steps, int)
        or isinstance(max_steps, bool)
        or max_steps < 1
        or max_steps > 8
    ):
        raise ValueError("max_steps must be an integer in 1..8")

    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    handoff_module, runtime = _load_reviewed(source)
    handoff = _load_json(
        phase8_handoff_path,
        label="Phase 8 post-Phase 7 operator handoff",
    )
    handoff_module.validate_phase8_post_phase7_handoff(handoff)
    if (
        not _is_hex_digest(expected_phase8_handoff_sha256, 64)
        or handoff["handoff_sha256"] != expected_phase8_handoff_sha256
    ):
        raise ValueError("saved Phase 8 handoff digest mismatch")

    for field in (
        "phase7_promotion_confirmed",
        "phase7_promotion_persisted",
        "phase8_handoff_ready",
        "phase8_research_only",
        "phase8_read_only",
    ):
        if handoff.get(field) is not True:
            raise ValueError(
                f"Phase 8 replay requires handoff {field}=true"
            )
    for field in (
        "phase8_policy_actionable",
        "phase8_execution_wired",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_authorized",
        "transaction_submission_authorized",
        "new_live_capital_authorized",
        "phase8_policy_action_authorized",
        "phase8_execution_authorized",
        "phase8_promotion_authorized",
        "production_pio_database_modified",
    ):
        if handoff.get(field) is not False:
            raise ValueError(
                f"Phase 8 replay refuses handoff {field}=true"
            )

    database = (production / "data" / "pio.db").resolve(strict=True)
    if Path(handoff["pio_database_path"]).resolve() != database:
        raise ValueError("Phase 8 replay database binding mismatch")

    before = _database_state(database)
    if before["database"] != handoff["pio_database_sha256_after"]:
        raise ValueError("Pio database changed after Phase 8 handoff")
    if before["wal"] != handoff["pio_wal_sha256_after"]:
        raise ValueError("Pio WAL changed after Phase 8 handoff")
    if before["shm"] != handoff["pio_shm_sha256_after"]:
        raise ValueError("Pio SHM changed after Phase 8 handoff")

    with tempfile.TemporaryDirectory(
        prefix="pio-phase8-offline-replay-"
    ) as tmp:
        private_root = Path(tmp)
        private_db = private_root / "pio.db"
        _snapshot_sqlite(database, private_db)
        private_before = _sha256_bytes(private_db.read_bytes())

        storage = runtime["Storage"](private_db)
        replay = runtime["run_phase8_evidence_until_blocked"](
            storage,
            max_steps=max_steps,
        )
        replay_record = _normalize_private_paths(
            _record(replay),
            private_root,
        )
        private_after = _sha256_bytes(private_db.read_bytes())
        artifacts = _private_artifacts(private_root)

    after = _database_state(database)
    if after != before:
        raise ValueError(
            "production Pio database changed during Phase 8 replay"
        )

    for field, expected in (
        ("research_only", True),
        ("offline_only", True),
        ("policy_actionable", False),
        ("execution_wired", False),
    ):
        if replay_record.get(field) is not expected:
            raise ValueError(
                f"Phase 8 replay safety boundary changed: {field}"
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
        "saved_phase8_handoff_sha256": handoff["handoff_sha256"],
        "expected_phase8_handoff_sha256": expected_phase8_handoff_sha256,
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before["database"],
        "pio_database_sha256_after": after["database"],
        "pio_wal_sha256_before": before["wal"],
        "pio_wal_sha256_after": after["wal"],
        "pio_shm_sha256_before": before["shm"],
        "pio_shm_sha256_after": after["shm"],
        "production_database_unchanged": True,
        "phase7_promotion_confirmed": True,
        "phase8_handoff_ready": True,
        "phase8_research_only": True,
        "phase8_policy_actionable": False,
        "phase8_execution_wired": False,
        "max_steps": max_steps,
        "private_database_sha256_before": private_before,
        "private_database_sha256_after": private_after,
        "private_database_changed": private_before != private_after,
        "private_artifacts": artifacts,
        "private_artifact_count": len(artifacts),
        "replay_report": replay_record,
        "replay_status": replay_record.get("status"),
        "steps_attempted": replay_record.get("steps_attempted"),
        "steps_progressed": replay_record.get("steps_progressed"),
        "terminal_debt_type": replay_record.get("terminal_debt_type"),
        "terminal_scope": replay_record.get("terminal_scope"),
        "promotion_ready_after_replay": bool(
            replay_record.get("promotion_ready")
        ),
        "phase8_persisted_current_after_replay": bool(
            replay_record.get("persisted_phase8_current")
        ),
        "replay_plan_ready": True,
        "replay_is_advisory_only": True,
        "separate_phase8_mutation_action_required": True,
        "paper_trading_authorized": False,
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
        "replay_plan_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_offline_replay_plan(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the existing bounded Phase 8 offline evidence runner only on "
            "a private Pio database snapshot. The result is advisory and does "
            "not authorize PAPER, live execution, production mutation, or "
            "Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--phase8-handoff", required=True)
    parser.add_argument("--expected-phase8-handoff-sha256", required=True)
    parser.add_argument("--max-steps", type=int, default=4)
    args = parser.parse_args()

    report = build_phase8_offline_replay_plan(
        repository=args.repo,
        source_tree=args.source_tree,
        phase8_handoff_path=args.phase8_handoff,
        expected_phase8_handoff_sha256=(
            args.expected_phase8_handoff_sha256
        ),
        max_steps=args.max_steps,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
