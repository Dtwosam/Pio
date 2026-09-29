from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = (
    "PHASE8_PAPER_CHALLENGER_TRANSITION_ONE_SHOT_EXECUTION_RECEIPT_V1"
)

READINESS_TOOL = Path(
    "deploy/tools/check_phase8_paper_challenger_transition_execution_readiness.py"
)
STORAGE_MODULE = Path("python-learner/src/meteora_learner/storage.py")
REVIEWED_SOURCE_BLOBS = {
    READINESS_TOOL: "b0bef3b4610fb5da21ef9b127892159f48947f43",
    STORAGE_MODULE: "39bcc99413df357b89d261e854861e9e4a3fff23",
}

LOCK_PATH = Path("/var/tmp/pio-phase8-paper-transition-one-shot.lock")

RECEIPT_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "saved_readiness_sha256",
    "fresh_readiness_sha256",
    "saved_readiness_stable_sha256",
    "fresh_readiness_stable_sha256",
    "transition_request_sha256",
    "signed_authorization_verification_sha256",
    "approval_payload_sha256",
    "approval_signature_sha256",
    "allowed_signers_sha256",
    "approver_principal",
    "approval_id",
    "authorization_expires_at",
    "production_repository",
    "pio_database_path",
    "pio_database_sha256_before",
    "pio_database_sha256_after",
    "pio_wal_sha256_before",
    "pio_wal_sha256_after",
    "pio_shm_sha256_before",
    "pio_shm_sha256_after",
    "research_artifacts_before",
    "research_artifacts_after",
    "research_artifacts_before_sha256",
    "research_artifacts_after_sha256",
    "model_id",
    "active_cycle_id",
    "model_status_before",
    "model_status_after",
    "cycle_status_before",
    "cycle_status_after",
    "cycle_active_key_before",
    "cycle_active_key_after",
    "offline_evidence_type",
    "offline_evidence_qualified",
    "model_history_count_before",
    "model_history_count_after",
    "cycle_history_count_before",
    "cycle_history_count_after",
    "model_history_latest",
    "cycle_history_latest",
    "transition_committed_at",
    "fresh_readiness_matches_saved",
    "human_paper_challenger_transition_authorization_verified",
    "exact_cycle_model_binding_verified",
    "offline_qualification_verified",
    "atomic_transition_guard_verified",
    "one_shot_only",
    "paper_challenger_transition_authorized",
    "paper_challenger_transition_executed",
    "model_transition_completed",
    "cycle_sync_completed",
    "transition_completed",
    "requires_post_transition_audit",
    "paper_evidence_collection_authorized",
    "paper_trading_authorized",
    "new_live_entry_authorized",
    "controlled_live_authorized",
    "live_submit_authorized",
    "transaction_signing_performed",
    "transaction_submission_performed",
    "automatic_resubmission_performed",
    "new_live_capital_used",
    "phase8_policy_action_authorized",
    "phase8_execution_authorized",
    "phase8_promotion_authorized",
    "production_source_file_modified",
    "production_repository_git_mutated",
    "production_pio_database_modified",
    "production_research_artifacts_modified",
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


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


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


def _load_readiness_module(source: Path) -> Any:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Phase 8 PAPER transition executor dependency missing: {relative}"
            )
        if _git_blob_sha(path) != expected:
            raise ValueError(
                f"Phase 8 PAPER transition executor dependency mismatch: {relative}"
            )
    return _load_module(
        source / READINESS_TOOL,
        "phase8_paper_transition_executor_readiness",
    )


def _regular_hash_or_none(path: Path) -> str | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(
            f"unsafe Phase 8 PAPER transition database state file: {path}"
        )
    return _sha256_path(path)


def _database_state(database: Path) -> dict[str, str | None]:
    return {
        "database": _regular_hash_or_none(database),
        "wal": _regular_hash_or_none(Path(str(database) + "-wal")),
        "shm": _regular_hash_or_none(Path(str(database) + "-shm")),
    }


def _production_database(production: Path) -> Path:
    data = production / "data"
    if data.is_symlink() or not data.is_dir():
        raise ValueError(
            "Phase 8 PAPER transition executor data directory is unsafe"
        )
    database = data / "pio.db"
    try:
        st = os.lstat(database)
    except FileNotFoundError as exc:
        raise ValueError(
            "Phase 8 PAPER transition executor Pio database is missing"
        ) from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(
            "Phase 8 PAPER transition executor Pio database is unsafe"
        )
    return database.resolve()


def _stable_readiness_projection(
    value: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key not in {
            "readiness_sha256",
            "fresh_post_audit_sha256",
        }
    }


def _stable_readiness_sha256(value: dict[str, Any]) -> str:
    return _sha256_bytes(
        _canonical_bytes(_stable_readiness_projection(value))
    )


def _research_artifacts(
    source: Path,
    readiness_module: Any,
    data_root: Path,
) -> list[dict[str, Any]]:
    audit_module, _, _ = readiness_module._load_reviewed(source)
    _, original_executor, _ = audit_module._load_reviewed(source)
    value = original_executor._research_artifacts(data_root)
    if not isinstance(value, list):
        raise ValueError(
            "Phase 8 PAPER transition artifact inventory is invalid"
        )
    return value


def _history_count(
    conn: sqlite3.Connection,
    *,
    table: str,
    key: str,
    value: str,
) -> int:
    if table not in {
        "phase8_model_status_history",
        "phase8_cycle_status_history",
    }:
        raise ValueError("unexpected Phase 8 transition history table")
    if key not in {"model_id", "cycle_id"}:
        raise ValueError("unexpected Phase 8 transition history key")
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {key} = ?",
        (value,),
    ).fetchone()
    return int(row[0])


def _latest_model_history(
    conn: sqlite3.Connection,
    model_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, changed_at, old_status, new_status
        FROM phase8_model_status_history
        WHERE model_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (model_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Phase 8 model transition history is missing")
    return {
        "id": int(row[0]),
        "changed_at": str(row[1]),
        "old_status": str(row[2]) if row[2] is not None else None,
        "new_status": str(row[3]),
    }


def _latest_cycle_history(
    conn: sqlite3.Connection,
    cycle_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, changed_at, old_status, new_status,
               old_challenger_model_id, new_challenger_model_id,
               old_active_key, new_active_key
        FROM phase8_cycle_status_history
        WHERE cycle_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (cycle_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Phase 8 cycle transition history is missing")
    return {
        "id": int(row[0]),
        "changed_at": str(row[1]),
        "old_status": str(row[2]) if row[2] is not None else None,
        "new_status": str(row[3]),
        "old_challenger_model_id": (
            str(row[4]) if row[4] is not None else None
        ),
        "new_challenger_model_id": (
            str(row[5]) if row[5] is not None else None
        ),
        "old_active_key": str(row[6]) if row[6] is not None else None,
        "new_active_key": str(row[7]) if row[7] is not None else None,
    }


def _atomic_transition(
    database: Path,
    *,
    model_id: str,
    cycle_id: str,
) -> dict[str, Any]:
    committed_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(database)
    try:
        conn.execute("BEGIN IMMEDIATE")

        model = conn.execute(
            """
            SELECT status
            FROM model_registry
            WHERE model_id = ?
            """,
            (model_id,),
        ).fetchone()
        if model is None or str(model[0]) != "OFFLINE_QUALIFIED":
            raise ValueError(
                "Phase 8 PAPER transition model is not OFFLINE_QUALIFIED"
            )

        evidence = conn.execute(
            """
            SELECT evidence_type, qualified
            FROM model_offline_evidence
            WHERE model_id = ?
            LIMIT 1
            """,
            (model_id,),
        ).fetchone()
        if evidence is None:
            raise ValueError(
                "Phase 8 PAPER transition offline evidence is missing"
            )
        evidence_type = str(evidence[0])
        evidence_qualified = bool(evidence[1])
        if (
            evidence_type != "OFFLINE_CHALLENGER_V1"
            or not evidence_qualified
        ):
            raise ValueError(
                "Phase 8 PAPER transition offline evidence is not qualified"
            )

        cycle = conn.execute(
            """
            SELECT status, active_key, challenger_model_id
            FROM continuous_learning_cycles
            WHERE cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
        if cycle is None:
            raise ValueError(
                "Phase 8 PAPER transition active cycle is missing"
            )
        cycle_status = str(cycle[0])
        active_key = str(cycle[1]) if cycle[1] is not None else None
        challenger = str(cycle[2]) if cycle[2] is not None else None
        if cycle_status != "OFFLINE_QUALIFIED":
            raise ValueError(
                "Phase 8 PAPER transition cycle is not OFFLINE_QUALIFIED"
            )
        if active_key != "ACTIVE":
            raise ValueError(
                "Phase 8 PAPER transition cycle is not active"
            )
        if challenger != model_id:
            raise ValueError(
                "Phase 8 PAPER transition cycle/model binding changed"
            )

        active = conn.execute(
            """
            SELECT cycle_id
            FROM continuous_learning_cycles
            WHERE active_key = 'ACTIVE'
            """
        ).fetchall()
        if len(active) != 1 or str(active[0][0]) != cycle_id:
            raise ValueError(
                "Phase 8 PAPER transition active-cycle uniqueness changed"
            )

        other_paper = conn.execute(
            """
            SELECT model_id
            FROM model_registry
            WHERE status = 'PAPER_CHALLENGER'
              AND model_id <> ?
            LIMIT 1
            """,
            (model_id,),
        ).fetchone()
        if other_paper is not None:
            raise ValueError(
                "another PAPER_CHALLENGER already exists: "
                f"{other_paper[0]}"
            )

        model_history_before = _history_count(
            conn,
            table="phase8_model_status_history",
            key="model_id",
            value=model_id,
        )
        cycle_history_before = _history_count(
            conn,
            table="phase8_cycle_status_history",
            key="cycle_id",
            value=cycle_id,
        )

        model_update = conn.execute(
            """
            UPDATE model_registry
            SET status = 'PAPER_CHALLENGER',
                updated_at = ?
            WHERE model_id = ?
              AND status = 'OFFLINE_QUALIFIED'
            """,
            (committed_at, model_id),
        )
        if model_update.rowcount != 1:
            raise ValueError(
                "Phase 8 PAPER transition model update lost its guard"
            )

        cycle_update = conn.execute(
            """
            UPDATE continuous_learning_cycles
            SET status = 'PAPER_CHALLENGER',
                updated_at = ?
            WHERE cycle_id = ?
              AND active_key = 'ACTIVE'
              AND status = 'OFFLINE_QUALIFIED'
              AND challenger_model_id = ?
            """,
            (committed_at, cycle_id, model_id),
        )
        if cycle_update.rowcount != 1:
            raise ValueError(
                "Phase 8 PAPER transition cycle sync lost its guard"
            )

        model_after = conn.execute(
            "SELECT status FROM model_registry WHERE model_id = ?",
            (model_id,),
        ).fetchone()
        cycle_after = conn.execute(
            """
            SELECT status, active_key, challenger_model_id
            FROM continuous_learning_cycles
            WHERE cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
        if model_after is None or str(model_after[0]) != "PAPER_CHALLENGER":
            raise ValueError(
                "Phase 8 PAPER transition model did not reach PAPER_CHALLENGER"
            )
        if (
            cycle_after is None
            or str(cycle_after[0]) != "PAPER_CHALLENGER"
            or str(cycle_after[1]) != "ACTIVE"
            or str(cycle_after[2]) != model_id
        ):
            raise ValueError(
                "Phase 8 PAPER transition cycle did not synchronize"
            )

        model_history_after = _history_count(
            conn,
            table="phase8_model_status_history",
            key="model_id",
            value=model_id,
        )
        cycle_history_after = _history_count(
            conn,
            table="phase8_cycle_status_history",
            key="cycle_id",
            value=cycle_id,
        )
        if model_history_after != model_history_before + 1:
            raise ValueError(
                "Phase 8 PAPER transition model history delta is invalid"
            )
        if cycle_history_after != cycle_history_before + 1:
            raise ValueError(
                "Phase 8 PAPER transition cycle history delta is invalid"
            )

        model_history_latest = _latest_model_history(conn, model_id)
        cycle_history_latest = _latest_cycle_history(conn, cycle_id)
        if (
            model_history_latest["old_status"] != "OFFLINE_QUALIFIED"
            or model_history_latest["new_status"] != "PAPER_CHALLENGER"
        ):
            raise ValueError(
                "Phase 8 PAPER transition model history binding is invalid"
            )
        if (
            cycle_history_latest["old_status"] != "OFFLINE_QUALIFIED"
            or cycle_history_latest["new_status"] != "PAPER_CHALLENGER"
            or cycle_history_latest["old_challenger_model_id"] != model_id
            or cycle_history_latest["new_challenger_model_id"] != model_id
            or cycle_history_latest["old_active_key"] != "ACTIVE"
            or cycle_history_latest["new_active_key"] != "ACTIVE"
        ):
            raise ValueError(
                "Phase 8 PAPER transition cycle history binding is invalid"
            )

        conn.commit()
        return {
            "model_status_before": "OFFLINE_QUALIFIED",
            "model_status_after": "PAPER_CHALLENGER",
            "cycle_status_before": "OFFLINE_QUALIFIED",
            "cycle_status_after": "PAPER_CHALLENGER",
            "cycle_active_key_before": "ACTIVE",
            "cycle_active_key_after": "ACTIVE",
            "offline_evidence_type": evidence_type,
            "offline_evidence_qualified": evidence_qualified,
            "model_history_count_before": model_history_before,
            "model_history_count_after": model_history_after,
            "cycle_history_count_before": cycle_history_before,
            "cycle_history_count_after": cycle_history_after,
            "model_history_latest": model_history_latest,
            "cycle_history_latest": cycle_history_latest,
            "transition_committed_at": committed_at,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_phase8_paper_challenger_transition_execution_receipt(
    receipt: dict[str, Any],
) -> None:
    if not isinstance(receipt, dict):
        raise ValueError(
            "Phase 8 PAPER transition execution receipt must be an object"
        )
    if set(receipt) != set(RECEIPT_FIELDS) | {"receipt_sha256"}:
        raise ValueError(
            "Phase 8 PAPER transition execution receipt schema mismatch"
        )
    if receipt.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            "unsupported Phase 8 PAPER transition execution receipt format"
        )
    if receipt.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(
            "unexpected Phase 8 PAPER transition execution receipt type"
        )

    expected_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if receipt.get("reviewed_source_blobs") != expected_blobs:
        raise ValueError(
            "Phase 8 PAPER transition execution receipt lineage mismatch"
        )

    for field in (
        "saved_readiness_sha256",
        "fresh_readiness_sha256",
        "saved_readiness_stable_sha256",
        "fresh_readiness_stable_sha256",
        "transition_request_sha256",
        "signed_authorization_verification_sha256",
        "approval_payload_sha256",
        "approval_signature_sha256",
        "allowed_signers_sha256",
        "pio_database_sha256_before",
        "pio_database_sha256_after",
        "research_artifacts_before_sha256",
        "research_artifacts_after_sha256",
        "receipt_sha256",
    ):
        if not _is_hex_digest(receipt.get(field), 64):
            raise ValueError(
                f"Phase 8 PAPER transition receipt {field} is invalid"
            )

    for field in (
        "pio_wal_sha256_before",
        "pio_wal_sha256_after",
        "pio_shm_sha256_before",
        "pio_shm_sha256_after",
    ):
        value = receipt.get(field)
        if value is not None and not _is_hex_digest(value, 64):
            raise ValueError(
                f"Phase 8 PAPER transition receipt {field} is invalid"
            )

    for field in (
        "production_repository",
        "pio_database_path",
        "model_id",
        "active_cycle_id",
        "model_status_before",
        "model_status_after",
        "cycle_status_before",
        "cycle_status_after",
        "cycle_active_key_before",
        "cycle_active_key_after",
        "offline_evidence_type",
        "approver_principal",
        "approval_id",
        "authorization_expires_at",
        "transition_committed_at",
    ):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(
                f"Phase 8 PAPER transition receipt {field} is invalid"
            )

    if receipt["model_status_before"] != "OFFLINE_QUALIFIED":
        raise ValueError(
            "Phase 8 PAPER transition receipt model-before mismatch"
        )
    if receipt["model_status_after"] != "PAPER_CHALLENGER":
        raise ValueError(
            "Phase 8 PAPER transition receipt model-after mismatch"
        )
    if receipt["cycle_status_before"] != "OFFLINE_QUALIFIED":
        raise ValueError(
            "Phase 8 PAPER transition receipt cycle-before mismatch"
        )
    if receipt["cycle_status_after"] != "PAPER_CHALLENGER":
        raise ValueError(
            "Phase 8 PAPER transition receipt cycle-after mismatch"
        )
    if (
        receipt["cycle_active_key_before"] != "ACTIVE"
        or receipt["cycle_active_key_after"] != "ACTIVE"
    ):
        raise ValueError(
            "Phase 8 PAPER transition receipt active-cycle binding mismatch"
        )
    if receipt["offline_evidence_type"] != "OFFLINE_CHALLENGER_V1":
        raise ValueError(
            "Phase 8 PAPER transition receipt offline evidence type mismatch"
        )

    for field in (
        "model_history_count_before",
        "model_history_count_after",
        "cycle_history_count_before",
        "cycle_history_count_after",
    ):
        value = receipt.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"Phase 8 PAPER transition receipt {field} is invalid"
            )
    if (
        receipt["model_history_count_after"]
        != receipt["model_history_count_before"] + 1
    ):
        raise ValueError(
            "Phase 8 PAPER transition model history delta mismatch"
        )
    if (
        receipt["cycle_history_count_after"]
        != receipt["cycle_history_count_before"] + 1
    ):
        raise ValueError(
            "Phase 8 PAPER transition cycle history delta mismatch"
        )

    for field in (
        "research_artifacts_before",
        "research_artifacts_after",
    ):
        if not isinstance(receipt.get(field), list):
            raise ValueError(
                f"Phase 8 PAPER transition receipt {field} is invalid"
            )
    if receipt["research_artifacts_before"] != receipt[
        "research_artifacts_after"
    ]:
        raise ValueError(
            "Phase 8 PAPER transition changed research artifacts"
        )
    if receipt["research_artifacts_before_sha256"] != _sha256_bytes(
        _canonical_bytes(receipt["research_artifacts_before"])
    ):
        raise ValueError(
            "Phase 8 PAPER transition before-artifact digest mismatch"
        )
    if receipt["research_artifacts_after_sha256"] != _sha256_bytes(
        _canonical_bytes(receipt["research_artifacts_after"])
    ):
        raise ValueError(
            "Phase 8 PAPER transition after-artifact digest mismatch"
        )

    for field in ("model_history_latest", "cycle_history_latest"):
        if not isinstance(receipt.get(field), dict):
            raise ValueError(
                f"Phase 8 PAPER transition receipt {field} is invalid"
            )

    for field in (
        "fresh_readiness_matches_saved",
        "human_paper_challenger_transition_authorization_verified",
        "exact_cycle_model_binding_verified",
        "offline_qualification_verified",
        "atomic_transition_guard_verified",
        "one_shot_only",
        "paper_challenger_transition_authorized",
        "paper_challenger_transition_executed",
        "model_transition_completed",
        "cycle_sync_completed",
        "transition_completed",
        "requires_post_transition_audit",
        "production_pio_database_modified",
    ):
        if receipt.get(field) is not True:
            raise ValueError(
                "Phase 8 PAPER transition receipt requires "
                f"{field}=true"
            )

    for field in (
        "paper_evidence_collection_authorized",
        "paper_trading_authorized",
        "new_live_entry_authorized",
        "controlled_live_authorized",
        "live_submit_authorized",
        "transaction_signing_performed",
        "transaction_submission_performed",
        "automatic_resubmission_performed",
        "new_live_capital_used",
        "phase8_policy_action_authorized",
        "phase8_execution_authorized",
        "phase8_promotion_authorized",
        "production_source_file_modified",
        "production_repository_git_mutated",
        "production_research_artifacts_modified",
    ):
        if receipt.get(field) is not False:
            raise ValueError(
                "Phase 8 PAPER transition receipt requires "
                f"{field}=false"
            )

    if (
        receipt["saved_readiness_stable_sha256"]
        != receipt["fresh_readiness_stable_sha256"]
    ):
        raise ValueError(
            "Phase 8 PAPER transition stable readiness mismatch"
        )

    database_modified = (
        receipt["pio_database_sha256_before"]
        != receipt["pio_database_sha256_after"]
        or receipt["pio_wal_sha256_before"]
        != receipt["pio_wal_sha256_after"]
        or receipt["pio_shm_sha256_before"]
        != receipt["pio_shm_sha256_after"]
    )
    if not database_modified:
        raise ValueError(
            "Phase 8 PAPER transition receipt shows no database mutation"
        )

    identity = {field: receipt[field] for field in RECEIPT_FIELDS}
    if receipt["receipt_sha256"] != _sha256_bytes(
        _canonical_bytes(identity)
    ):
        raise ValueError(
            "Phase 8 PAPER transition execution receipt digest mismatch"
        )


def execute_phase8_paper_challenger_transition_once(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_readiness_path: str | Path,
    saved_post_audit_path: str | Path,
    execution_receipt_path: str | Path,
    transition_request_path: str | Path,
    saved_signed_authorization_verification_path: str | Path,
    signed_payload_path: str | Path,
    signature_path: str | Path,
    allowed_signers_path: str | Path,
    expected_allowed_signers_sha256: str,
    now: str | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository root is invalid")

    readiness_module = _load_readiness_module(source)
    saved_readiness = _load_json(
        saved_readiness_path,
        label="saved Phase 8 PAPER transition execution readiness",
    )
    readiness_module.validate_phase8_paper_challenger_transition_execution_readiness(
        saved_readiness
    )

    if saved_readiness.get(
        "paper_transition_execution_readiness_ready"
    ) is not True:
        raise ValueError(
            "saved Phase 8 PAPER transition readiness is not ready"
        )
    if saved_readiness.get(
        "requires_immediate_one_shot_paper_transition_executor"
    ) is not True:
        raise ValueError(
            "saved Phase 8 PAPER transition readiness lacks executor boundary"
        )
    if saved_readiness.get("readiness_only") is not True:
        raise ValueError(
            "saved Phase 8 PAPER transition artifact is not readiness-only"
        )

    if Path(str(saved_readiness["production_repository"])).resolve() != production:
        raise ValueError(
            "Phase 8 PAPER transition readiness repository mismatch"
        )
    database = _production_database(production)
    if Path(str(saved_readiness["pio_database_path"])).resolve() != database:
        raise ValueError(
            "Phase 8 PAPER transition readiness database mismatch"
        )

    lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    lock_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(LOCK_PATH, lock_flags, 0o600)
    except OSError as exc:
        raise ValueError(
            "Phase 8 PAPER transition executor lock path is unsafe"
        ) from exc
    lock_stat = os.fstat(lock_fd)
    if not stat.S_ISREG(lock_stat.st_mode):
        os.close(lock_fd)
        raise ValueError(
            "Phase 8 PAPER transition executor lock must be a regular file"
        )

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "another Phase 8 PAPER transition executor is active"
            ) from exc

        fresh_readiness = (
            readiness_module.build_phase8_paper_challenger_transition_execution_readiness(
                repository=production,
                source_tree=source,
                saved_post_audit_path=saved_post_audit_path,
                execution_receipt_path=execution_receipt_path,
                transition_request_path=transition_request_path,
                saved_signed_authorization_verification_path=(
                    saved_signed_authorization_verification_path
                ),
                signed_payload_path=signed_payload_path,
                signature_path=signature_path,
                allowed_signers_path=allowed_signers_path,
                expected_allowed_signers_sha256=(
                    expected_allowed_signers_sha256
                ),
                now=now,
            )
        )
        readiness_module.validate_phase8_paper_challenger_transition_execution_readiness(
            fresh_readiness
        )

        saved_stable = _stable_readiness_sha256(saved_readiness)
        fresh_stable = _stable_readiness_sha256(fresh_readiness)
        if saved_stable != fresh_stable:
            raise ValueError(
                "fresh Phase 8 PAPER transition readiness stable state "
                "differs from saved readiness"
            )

        before_state = _database_state(database)
        expected_before = {
            "database": saved_readiness["pio_database_sha256"],
            "wal": saved_readiness["pio_wal_sha256"],
            "shm": saved_readiness["pio_shm_sha256"],
        }
        if before_state != expected_before:
            raise ValueError(
                "Pio database changed after PAPER transition readiness"
            )

        artifacts_before = _research_artifacts(
            source,
            readiness_module,
            database.parent,
        )
        artifacts_before_sha = _sha256_bytes(
            _canonical_bytes(artifacts_before)
        )
        if (
            artifacts_before_sha
            != saved_readiness["research_artifacts_sha256"]
            or len(artifacts_before)
            != saved_readiness["research_artifact_count"]
        ):
            raise ValueError(
                "Phase 8 research artifacts changed after transition readiness"
            )

        transition = _atomic_transition(
            database,
            model_id=saved_readiness["model_id"],
            cycle_id=saved_readiness["active_cycle_id"],
        )

        after_state = _database_state(database)
        artifacts_after = _research_artifacts(
            source,
            readiness_module,
            database.parent,
        )
        if artifacts_after != artifacts_before:
            raise ValueError(
                "Phase 8 PAPER transition unexpectedly changed "
                "research artifacts"
            )
        if after_state == before_state:
            raise ValueError(
                "Phase 8 PAPER transition did not mutate the Pio database"
            )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    artifacts_after_sha = _sha256_bytes(
        _canonical_bytes(artifacts_after)
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
        "saved_readiness_sha256": saved_readiness["readiness_sha256"],
        "fresh_readiness_sha256": fresh_readiness["readiness_sha256"],
        "saved_readiness_stable_sha256": saved_stable,
        "fresh_readiness_stable_sha256": fresh_stable,
        "transition_request_sha256": fresh_readiness[
            "transition_request_sha256"
        ],
        "signed_authorization_verification_sha256": fresh_readiness[
            "fresh_signed_authorization_verification_sha256"
        ],
        "approval_payload_sha256": fresh_readiness[
            "approval_payload_sha256"
        ],
        "approval_signature_sha256": fresh_readiness[
            "approval_signature_sha256"
        ],
        "allowed_signers_sha256": fresh_readiness[
            "allowed_signers_sha256"
        ],
        "approver_principal": fresh_readiness["approver_principal"],
        "approval_id": fresh_readiness["approval_id"],
        "authorization_expires_at": fresh_readiness[
            "authorization_expires_at"
        ],
        "production_repository": str(production),
        "pio_database_path": str(database),
        "pio_database_sha256_before": before_state["database"],
        "pio_database_sha256_after": after_state["database"],
        "pio_wal_sha256_before": before_state["wal"],
        "pio_wal_sha256_after": after_state["wal"],
        "pio_shm_sha256_before": before_state["shm"],
        "pio_shm_sha256_after": after_state["shm"],
        "research_artifacts_before": artifacts_before,
        "research_artifacts_after": artifacts_after,
        "research_artifacts_before_sha256": artifacts_before_sha,
        "research_artifacts_after_sha256": artifacts_after_sha,
        "model_id": fresh_readiness["model_id"],
        "active_cycle_id": fresh_readiness["active_cycle_id"],
        **transition,
        "fresh_readiness_matches_saved": True,
        "human_paper_challenger_transition_authorization_verified": True,
        "exact_cycle_model_binding_verified": True,
        "offline_qualification_verified": True,
        "atomic_transition_guard_verified": True,
        "one_shot_only": True,
        "paper_challenger_transition_authorized": True,
        "paper_challenger_transition_executed": True,
        "model_transition_completed": True,
        "cycle_sync_completed": True,
        "transition_completed": True,
        "requires_post_transition_audit": True,
        "paper_evidence_collection_authorized": False,
        "paper_trading_authorized": False,
        "new_live_entry_authorized": False,
        "controlled_live_authorized": False,
        "live_submit_authorized": False,
        "transaction_signing_performed": False,
        "transaction_submission_performed": False,
        "automatic_resubmission_performed": False,
        "new_live_capital_used": False,
        "phase8_policy_action_authorized": False,
        "phase8_execution_authorized": False,
        "phase8_promotion_authorized": False,
        "production_source_file_modified": False,
        "production_repository_git_mutated": False,
        "production_pio_database_modified": True,
        "production_research_artifacts_modified": False,
    }
    receipt = {
        **identity,
        "receipt_sha256": _sha256_bytes(_canonical_bytes(identity)),
    }
    validate_phase8_paper_challenger_transition_execution_receipt(
        receipt
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly one authorized Phase 8 OFFLINE_QUALIFIED to "
            "PAPER_CHALLENGER state transition. The model and active "
            "retraining cycle are updated atomically with exact guards and "
            "immutable history verification. This stage does not start PAPER "
            "evidence collection or paper trades, use live capital, submit "
            "transactions, or persist Phase 8 promotion."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--saved-readiness", required=True)
    parser.add_argument("--saved-post-audit", required=True)
    parser.add_argument("--execution-receipt", required=True)
    parser.add_argument("--transition-request", required=True)
    parser.add_argument("--saved-signed-verification", required=True)
    parser.add_argument("--signed-payload", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument(
        "--expected-allowed-signers-sha256",
        required=True,
    )
    parser.add_argument("--now")
    args = parser.parse_args()

    receipt = execute_phase8_paper_challenger_transition_once(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_readiness_path=args.saved_readiness,
        saved_post_audit_path=args.saved_post_audit,
        execution_receipt_path=args.execution_receipt,
        transition_request_path=args.transition_request,
        saved_signed_authorization_verification_path=(
            args.saved_signed_verification
        ),
        signed_payload_path=args.signed_payload,
        signature_path=args.signature,
        allowed_signers_path=args.allowed_signers,
        expected_allowed_signers_sha256=(
            args.expected_allowed_signers_sha256
        ),
        now=args.now,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
