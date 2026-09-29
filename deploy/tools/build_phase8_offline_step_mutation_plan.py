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
ARTIFACT_TYPE = "PHASE8_OFFLINE_STEP_MUTATION_PLAN_V1"

READINESS_TOOL = Path("deploy/tools/check_phase8_offline_step_readiness.py")
EVIDENCE_STEP = Path(
    "python-learner/src/meteora_learner/phase8_evidence_step.py"
)
OFFLINE_RETRAINING = Path(
    "python-learner/src/meteora_learner/phase8_offline_retraining.py"
)
RETRAIN_INPUTS = Path(
    "python-learner/src/meteora_learner/phase8_retrain_inputs.py"
)

REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "569942f2ec53a28aedaf74265bc99de0ec1303df",
    EVIDENCE_STEP: "887924565f43854141300bfea3a437fd40eeb354",
    OFFLINE_RETRAINING: "ac025dc201b191460c178e631b700bd2ca34ea7b",
    RETRAIN_INPUTS: "9059b9aa190cd9598fabbb657c183215c22aa794",
}

DATASET_BUILD = "RETRAIN_DATASET_BUILD_READY"
OFFLINE_TRAIN = "RETRAIN_OFFLINE_TRAIN_READY"
OFFLINE_VALIDATE = "RETRAIN_OFFLINE_VALIDATION_READY"
ALLOWED_DEBT_TYPES = {DATASET_BUILD, OFFLINE_TRAIN, OFFLINE_VALIDATE}

REPORT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "offline_step_readiness_sha256",
    "request_sha256",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256",
    "debt_type",
    "scope",
    "allowed_artifact_root",
    "artifact_root_exists",
    "artifact_inventory_before",
    "artifact_inventory_after",
    "artifact_inventory_sha256",
    "artifact_file_count",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "allowed_mutation_targets",
    "mutation_scope_minimal",
    "production_state_unchanged_during_plan",
    "mutation_plan_ready",
    "requires_separate_offline_step_executor",
    "offline_step_executed",
    "paper_challenger_transition_authorized",
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
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_readiness_module(source: Path) -> Any:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 8 mutation-plan dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"Phase 8 mutation-plan dependency mismatch: {relative}"
            )
    return _load_module(
        source / READINESS_TOOL,
        "phase8_offline_step_mutation_plan_readiness",
    )


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"unsafe production state file: {path}")
    return _sha256_bytes(path.read_bytes())


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _inventory_tree(root: Path | None) -> tuple[bool, list[dict[str, Any]]]:
    if root is None:
        return False, []
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return False, []
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ValueError(f"Phase 8 mutation artifact root is unsafe: {root}")

    values: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        item = os.lstat(path)
        if stat.S_ISLNK(item.st_mode):
            raise ValueError(
                f"Phase 8 mutation artifact tree contains symlink: {rel}"
            )
        if stat.S_ISDIR(item.st_mode):
            continue
        if not stat.S_ISREG(item.st_mode):
            raise ValueError(
                f"Phase 8 mutation artifact tree contains unsafe file: {rel}"
            )
        payload = path.read_bytes()
        values.append(
            {
                "path": rel,
                "size_bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )
    return True, values


def _artifact_root(
    *,
    production: Path,
    debt_type: str,
    scope: str,
) -> Path | None:
    data = production / "data"
    if debt_type == DATASET_BUILD:
        return data / "phase8_retraining_datasets"
    if debt_type == OFFLINE_TRAIN:
        return data / "phase8_ml_artifacts" / scope
    if debt_type == OFFLINE_VALIDATE:
        return None
    raise ValueError("Phase 8 mutation-plan debt type is not allowed")


def validate_phase8_offline_step_mutation_plan(
    report: dict[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise ValueError("Phase 8 mutation plan must be a JSON object")
    if set(report) != set(REPORT_FIELDS) | {"mutation_plan_sha256"}:
        raise ValueError("Phase 8 mutation plan schema mismatch")
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported Phase 8 mutation plan format")
    if report.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected Phase 8 mutation plan type")

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if report.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError("Phase 8 mutation plan lineage mismatch")

    for field in (
        "offline_step_readiness_sha256",
        "request_sha256",
        "pio_database_sha256",
        "artifact_inventory_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "mutation_plan_sha256",
    ):
        if not _is_hex_digest(report.get(field), 64):
            raise ValueError(f"Phase 8 mutation plan {field} is invalid")
    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = report.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(f"Phase 8 mutation plan {field} is invalid")

    for field in (
        "production_repository",
        "pio_database_path",
        "debt_type",
        "scope",
    ):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"Phase 8 mutation plan {field} is invalid")
    if report["debt_type"] not in ALLOWED_DEBT_TYPES:
        raise ValueError("Phase 8 mutation plan debt type is not allowed")

    artifact_root = report.get("allowed_artifact_root")
    if report["debt_type"] == OFFLINE_VALIDATE:
        if artifact_root is not None or report["artifact_root_exists"] is not False:
            raise ValueError("Phase 8 validation step must be DB-only")
    else:
        if not isinstance(artifact_root, str) or not artifact_root.startswith("/"):
            raise ValueError("Phase 8 mutation artifact root is invalid")
        if not isinstance(report.get("artifact_root_exists"), bool):
            raise ValueError("Phase 8 artifact-root existence flag is invalid")

    for field in (
        "artifact_inventory_before",
        "artifact_inventory_after",
        "allowed_mutation_targets",
    ):
        if not isinstance(report.get(field), list):
            raise ValueError(f"Phase 8 mutation plan {field} is invalid")
    if report["artifact_inventory_before"] != report["artifact_inventory_after"]:
        raise ValueError("artifact tree changed during Phase 8 mutation planning")
    if _sha256_bytes(
        _canonical_bytes(report["artifact_inventory_before"])
    ) != report["artifact_inventory_sha256"]:
        raise ValueError("Phase 8 artifact inventory digest mismatch")
    if report.get("artifact_file_count") != len(
        report["artifact_inventory_before"]
    ):
        raise ValueError("Phase 8 artifact file count mismatch")

    if report["pio_database_sha256_before"] != report[
        "pio_database_sha256_after"
    ]:
        raise ValueError("Pio database changed during mutation planning")
    if report["pio_wal_sha256_before"] != report["pio_wal_sha256_after"]:
        raise ValueError("Pio WAL changed during mutation planning")
    if report["pio_shm_sha256_before"] != report["pio_shm_sha256_after"]:
        raise ValueError("Pio SHM changed during mutation planning")
    if report["pio_database_sha256"] != report["pio_database_sha256_before"]:
        raise ValueError("Phase 8 readiness/database binding mismatch")

    for field in (
        "mutation_scope_minimal",
        "production_state_unchanged_during_plan",
        "mutation_plan_ready",
        "requires_separate_offline_step_executor",
    ):
        if report.get(field) is not True:
            raise ValueError(
                f"Phase 8 mutation plan requires {field}=true"
            )

    for field in (
        "offline_step_executed",
        "paper_challenger_transition_authorized",
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
                f"Phase 8 mutation plan requires {field}=false"
            )

    identity = {field: report[field] for field in REPORT_FIELDS}
    expected = _sha256_bytes(_canonical_bytes(identity))
    if report["mutation_plan_sha256"] != expected:
        raise ValueError("Phase 8 mutation plan digest mismatch")


def build_phase8_offline_step_mutation_plan(
    *,
    repository: str | Path,
    source_tree: str | Path,
    readiness_path: str | Path,
) -> dict[str, Any]:
    production = Path(repository).resolve()
    source = Path(source_tree).resolve()
    if not production.is_dir():
        raise ValueError("production repository root is invalid")
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")

    readiness_module = _load_readiness_module(source)
    readiness = _load_json(
        readiness_path,
        label="Phase 8 offline-step readiness",
    )
    readiness_module.validate_phase8_offline_step_readiness(readiness)
    if readiness.get("offline_step_readiness_ready") is not True:
        raise ValueError("Phase 8 offline-step readiness is not ready")
    if readiness.get("requires_separate_offline_step_executor") is not True:
        raise ValueError("Phase 8 readiness does not require an executor")
    if readiness.get("offline_step_executed") is not False:
        raise ValueError("Phase 8 offline step is already executed")
    if Path(readiness["production_repository"]).resolve() != production:
        raise ValueError("Phase 8 mutation plan repository binding mismatch")

    database = (production / "data" / "pio.db").resolve(strict=True)
    if Path(readiness["pio_database_path"]).resolve() != database:
        raise ValueError("Phase 8 mutation plan database binding mismatch")

    data_dir = production / "data"
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise ValueError("Phase 8 production data directory is unsafe")

    debt_type = readiness["debt_type"]
    scope = readiness["scope"]
    root = _artifact_root(
        production=production,
        debt_type=debt_type,
        scope=scope,
    )
    if root is not None:
        resolved_parent = root.parent.resolve(strict=False)
        if production not in resolved_parent.parents and resolved_parent != production:
            raise ValueError("Phase 8 artifact root escapes production repository")

    before_db = _database_state(database)
    if before_db["database"] != readiness["pio_database_sha256"]:
        raise ValueError("Pio database changed after Phase 8 readiness")
    root_exists_before, inventory_before = _inventory_tree(root)

    after_db = _database_state(database)
    root_exists_after, inventory_after = _inventory_tree(root)
    if after_db != before_db:
        raise ValueError("Pio database changed during Phase 8 mutation planning")
    if (
        root_exists_after != root_exists_before
        or inventory_after != inventory_before
    ):
        raise ValueError("artifact tree changed during Phase 8 mutation planning")

    targets = [
        str(database),
        str(Path(str(database) + "-wal")),
        str(Path(str(database) + "-shm")),
    ]
    if root is not None:
        targets.append(str(root.resolve(strict=False)))

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
        "offline_step_readiness_sha256": readiness["readiness_sha256"],
        "request_sha256": readiness["request_sha256"],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256": readiness["pio_database_sha256"],
        "debt_type": debt_type,
        "scope": scope,
        "allowed_artifact_root": (
            str(root.resolve(strict=False)) if root is not None else None
        ),
        "artifact_root_exists": root_exists_before,
        "artifact_inventory_before": inventory_before,
        "artifact_inventory_after": inventory_after,
        "artifact_inventory_sha256": _sha256_bytes(
            _canonical_bytes(inventory_before)
        ),
        "artifact_file_count": len(inventory_before),
        "pio_database_sha256_before": before_db["database"],
        "pio_database_sha256_after": after_db["database"],
        "pio_wal_sha256_before": before_db["wal"],
        "pio_wal_sha256_after": after_db["wal"],
        "pio_shm_sha256_before": before_db["shm"],
        "pio_shm_sha256_after": after_db["shm"],
        "allowed_mutation_targets": targets,
        "mutation_scope_minimal": True,
        "production_state_unchanged_during_plan": True,
        "mutation_plan_ready": True,
        "requires_separate_offline_step_executor": True,
        "offline_step_executed": False,
        "paper_challenger_transition_authorized": False,
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
        "mutation_plan_sha256": _sha256_bytes(
            _canonical_bytes(identity)
        ),
    }
    validate_phase8_offline_step_mutation_plan(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only mutation-footprint plan for one authorized "
            "Phase 8 offline research step. The plan inventories the exact "
            "database and artifact roots the future executor may touch."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--readiness", required=True)
    args = parser.parse_args()

    report = build_phase8_offline_step_mutation_plan(
        repository=args.repo,
        source_tree=args.source_tree,
        readiness_path=args.readiness,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
