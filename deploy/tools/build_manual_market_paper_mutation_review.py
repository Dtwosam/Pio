from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_MUTATION_REVIEW_V1"

GATE_TOOL = Path("deploy/tools/check_manual_market_paper_deployment_gate.py")
PLAN_TOOL = Path("deploy/tools/build_manual_market_paper_deployment_plan.py")

REVIEWED_SOURCE_BLOBS = {
    GATE_TOOL: "eea6e4ed14336bfeaf38926725f1113f980dc2b2",
    PLAN_TOOL: "837eb93e51b066c84dafd9808ca61bf517003836",
}

LAYER_ORDER = (
    "PHASE2_SHARED_PREREQUISITES",
    "STATE_READER",
    "MARKET_PAPER_RUNTIME",
)

REVIEW_IDENTITY_FIELDS = (
    "format_version",
    "artifact_type",
    "reviewed_source_blobs",
    "production_repository",
    "handoff_state_sha256",
    "plan_sha256",
    "saved_gate_sha256",
    "fresh_gate_sha256",
    "fresh_gate_matches_saved",
    "fresh_gate_ready",
    "operation_checks",
    "operation_count",
    "deployment_needed",
    "source_targets_match_plan",
    "current_files_match_plan",
    "rollback_recipe_complete",
    "backup_capture_required",
    "backup_material_captured",
    "review_ready",
    "requires_fresh_execution_recheck",
    "requires_backup_capture_before_mutation",
    "requires_separate_mutation_authorization",
    "production_deployment_authorized",
    "mutation_authorized",
    "service_restart_authorized",
    "detector_cursor_movement_authorized",
    "paper_timer_enable_authorized",
    "live_capital_authorized",
)

OPERATION_CHECK_FIELDS = (
    "index",
    "layer",
    "operation",
    "path",
    "plan_operation_sha256",
    "expected_current_blob",
    "observed_current_blob",
    "current_state",
    "current_matches_expected",
    "target_blob",
    "observed_source_blob",
    "source_state",
    "source_matches_target",
    "patch_sha256",
    "observed_patch_sha256",
    "patch_matches_plan",
    "backup_required",
    "rollback_operation",
    "rollback_blob",
    "rollback_recipe_complete",
    "operation_ready",
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            raise ValueError(f"reviewed mutation-review artifact is missing: {relative}")
        if path.is_symlink():
            raise ValueError(f"reviewed mutation-review artifact is a symlink: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed mutation-review artifact mismatch: {relative}")


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any]:
    _verify_reviewed_source(source)
    gate_module = _load_module(
        source / GATE_TOOL,
        "manual_market_paper_mutation_review_gate",
    )
    plan_module, handoff_module = gate_module._load_reviewed_modules(source)
    if _git_blob_sha(source / PLAN_TOOL) != REVIEWED_SOURCE_BLOBS[PLAN_TOOL]:
        raise ValueError("reviewed mutation-review planner lineage mismatch")
    return gate_module, plan_module, handoff_module


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _safe_relative_path(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError("mutation-review operation path is invalid")
    if "\\" in raw:
        raise ValueError(f"mutation-review path contains a backslash: {raw}")
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise ValueError(f"mutation-review path must be relative: {raw}")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"mutation-review path contains unsafe segments: {raw}")
    normalized = "/".join(pure.parts)
    if normalized != raw:
        raise ValueError(f"mutation-review path is not normalized: {raw}")
    return Path(*pure.parts)


def _parent_safety(root: Path, relative: Path) -> tuple[bool, str | None]:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            return False, "PARENT_SYMLINK"
        if not current.exists():
            return False, "PARENT_MISSING"
        if not current.is_dir():
            return False, "PARENT_NOT_DIRECTORY"
    return True, None


def _inspect_current_target(
    repository: Path,
    relative: Path,
    expected_current_blob: str | None,
) -> tuple[str, str | None, bool]:
    parent_safe, parent_state = _parent_safety(repository, relative)
    if not parent_safe:
        return str(parent_state), None, False

    target = repository / relative
    if target.is_symlink():
        return "TARGET_SYMLINK", None, False

    if expected_current_blob is None:
        if target.exists():
            return "UNEXPECTED_PRESENT", None, False
        return "ABSENT_AS_EXPECTED", None, True

    if not target.exists():
        return "MISSING", None, False
    if not target.is_file():
        return "NOT_REGULAR_FILE", None, False

    observed = _git_blob_sha(target)
    if observed == expected_current_blob:
        return "BLOB_MATCH", observed, True
    return "BLOB_MISMATCH", observed, False


def _inspect_source_target(
    source: Path,
    relative: Path,
    target_blob: str,
) -> tuple[str, str | None, bool]:
    parent_safe, parent_state = _parent_safety(source, relative)
    if not parent_safe:
        return str(parent_state), None, False

    target = source / relative
    if target.is_symlink():
        return "TARGET_SYMLINK", None, False
    if not target.exists():
        return "MISSING", None, False
    if not target.is_file():
        return "NOT_REGULAR_FILE", None, False

    observed = _git_blob_sha(target)
    if observed == target_blob:
        return "BLOB_MATCH", observed, True
    return "BLOB_MISMATCH", observed, False


def _inspect_patch(
    source: Path,
    plan_module: Any,
    expected_sha256: str,
) -> tuple[str | None, bool]:
    relative = _safe_relative_path(str(plan_module.STATE_READER_PATCH))
    parent_safe, _ = _parent_safety(source, relative)
    if not parent_safe:
        return None, False

    patch = source / relative
    if patch.is_symlink() or not patch.is_file():
        return None, False
    observed = _sha256(patch)
    return observed, observed == expected_sha256


def _operation_check(
    *,
    index: int,
    operation: dict[str, Any],
    source: Path,
    repository: Path,
    plan_module: Any,
) -> dict[str, Any]:
    relative = _safe_relative_path(operation["path"])
    expected_current_blob = operation.get("expected_current_blob")
    target_blob = str(operation["target_blob"])

    current_state, observed_current_blob, current_matches = _inspect_current_target(
        repository,
        relative,
        expected_current_blob,
    )
    source_state, observed_source_blob, source_matches = _inspect_source_target(
        source,
        relative,
        target_blob,
    )

    patch_sha256: str | None = None
    observed_patch_sha256: str | None = None
    patch_matches_plan: bool | None = None
    if operation["operation"] == "APPLY_REVIEWED_PATCH":
        patch_sha256 = str(operation["patch_sha256"])
        observed_patch_sha256, patch_matches_plan = _inspect_patch(
            source,
            plan_module,
            patch_sha256,
        )

    backup_required = bool(operation["backup_required"])
    if operation["operation"] == "CREATE_FILE":
        rollback_operation = "DELETE_CREATED_FILE"
        rollback_blob = None
        rollback_recipe_complete = (
            expected_current_blob is None and backup_required is False
        )
    else:
        rollback_operation = "RESTORE_EXPECTED_BLOB"
        rollback_blob = expected_current_blob
        rollback_recipe_complete = (
            backup_required is True
            and _is_hex_digest(expected_current_blob, 40)
        )

    patch_ready = patch_matches_plan is not False
    operation_ready = bool(
        current_matches
        and source_matches
        and patch_ready
        and rollback_recipe_complete
    )

    return {
        "index": index,
        "layer": operation["layer"],
        "operation": operation["operation"],
        "path": operation["path"],
        "plan_operation_sha256": hashlib.sha256(
            _canonical_bytes(operation)
        ).hexdigest(),
        "expected_current_blob": expected_current_blob,
        "observed_current_blob": observed_current_blob,
        "current_state": current_state,
        "current_matches_expected": current_matches,
        "target_blob": target_blob,
        "observed_source_blob": observed_source_blob,
        "source_state": source_state,
        "source_matches_target": source_matches,
        "patch_sha256": patch_sha256,
        "observed_patch_sha256": observed_patch_sha256,
        "patch_matches_plan": patch_matches_plan,
        "backup_required": backup_required,
        "rollback_operation": rollback_operation,
        "rollback_blob": rollback_blob,
        "rollback_recipe_complete": rollback_recipe_complete,
        "operation_ready": operation_ready,
    }


def validate_mutation_review(review: dict[str, Any]) -> None:
    if not isinstance(review, dict):
        raise ValueError("mutation review must be a JSON object")

    expected_keys = set(REVIEW_IDENTITY_FIELDS) | {"review_sha256"}
    if set(review) != expected_keys:
        raise ValueError("mutation review fields do not match reviewed schema")
    if review.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported mutation review format")
    if review.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected mutation review artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if review.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("mutation review source lineage mismatch")

    repository = review.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("mutation review production repository must be absolute")

    for field in (
        "handoff_state_sha256",
        "plan_sha256",
        "saved_gate_sha256",
        "fresh_gate_sha256",
        "review_sha256",
    ):
        if not _is_hex_digest(review.get(field), 64):
            raise ValueError(f"mutation review {field} is invalid")

    bool_fields = (
        "fresh_gate_matches_saved",
        "fresh_gate_ready",
        "deployment_needed",
        "source_targets_match_plan",
        "current_files_match_plan",
        "rollback_recipe_complete",
        "backup_capture_required",
        "backup_material_captured",
        "review_ready",
        "requires_fresh_execution_recheck",
        "requires_backup_capture_before_mutation",
        "requires_separate_mutation_authorization",
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    )
    for field in bool_fields:
        if not isinstance(review.get(field), bool):
            raise ValueError(f"mutation review {field} must be boolean")

    checks = review.get("operation_checks")
    if not isinstance(checks, list):
        raise ValueError("mutation review operation_checks must be a list")
    operation_count = review.get("operation_count")
    if (
        not isinstance(operation_count, int)
        or isinstance(operation_count, bool)
        or operation_count < 0
        or operation_count != len(checks)
    ):
        raise ValueError("mutation review operation count mismatch")
    if review["deployment_needed"] is not bool(checks):
        raise ValueError("mutation review deployment-needed flag mismatch")

    seen_paths: set[str] = set()
    previous_layer = -1
    for expected_index, item in enumerate(checks):
        if not isinstance(item, dict) or set(item) != set(OPERATION_CHECK_FIELDS):
            raise ValueError("mutation review operation-check schema mismatch")
        if item.get("index") != expected_index:
            raise ValueError("mutation review operation indexes are not contiguous")
        layer = item.get("layer")
        if layer not in LAYER_ORDER:
            raise ValueError("mutation review operation layer is invalid")
        layer_index = LAYER_ORDER.index(layer)
        if layer_index < previous_layer:
            raise ValueError("mutation review operation layers are out of order")
        previous_layer = layer_index

        path = item.get("path")
        _safe_relative_path(path)
        if path in seen_paths:
            raise ValueError(f"mutation review path is duplicated: {path}")
        seen_paths.add(path)

        operation = item.get("operation")
        if operation not in {"CREATE_FILE", "UPDATE_FILE", "APPLY_REVIEWED_PATCH"}:
            raise ValueError("mutation review operation type is invalid")
        if not _is_hex_digest(item.get("plan_operation_sha256"), 64):
            raise ValueError("mutation review operation digest is invalid")
        if not _is_hex_digest(item.get("target_blob"), 40):
            raise ValueError("mutation review target blob is invalid")

        expected_current = item.get("expected_current_blob")
        if expected_current is not None and not _is_hex_digest(expected_current, 40):
            raise ValueError("mutation review expected-current blob is invalid")
        for field in ("observed_current_blob", "observed_source_blob"):
            value = item.get(field)
            if value is not None and not _is_hex_digest(value, 40):
                raise ValueError(f"mutation review {field} is invalid")

        for field in (
            "current_matches_expected",
            "source_matches_target",
            "backup_required",
            "rollback_recipe_complete",
            "operation_ready",
        ):
            if not isinstance(item.get(field), bool):
                raise ValueError(f"mutation review operation {field} must be boolean")

        for field in ("current_state", "source_state"):
            if not isinstance(item.get(field), str) or not item[field]:
                raise ValueError(f"mutation review operation {field} is invalid")

        if operation == "CREATE_FILE":
            if expected_current is not None or item["backup_required"] is not False:
                raise ValueError("mutation review create rollback semantics are invalid")
            if item.get("rollback_operation") != "DELETE_CREATED_FILE":
                raise ValueError("mutation review create rollback action is invalid")
            if item.get("rollback_blob") is not None:
                raise ValueError("mutation review create rollback blob must be null")
            if item.get("patch_sha256") is not None:
                raise ValueError("mutation review create patch digest must be null")
            if item.get("observed_patch_sha256") is not None:
                raise ValueError("mutation review create observed patch must be null")
            if item.get("patch_matches_plan") is not None:
                raise ValueError("mutation review create patch result must be null")
        else:
            if not _is_hex_digest(expected_current, 40):
                raise ValueError("mutation review update expected blob is invalid")
            if item["backup_required"] is not True:
                raise ValueError("mutation review update must require backup")
            if item.get("rollback_operation") != "RESTORE_EXPECTED_BLOB":
                raise ValueError("mutation review update rollback action is invalid")
            if item.get("rollback_blob") != expected_current:
                raise ValueError("mutation review rollback blob mismatch")

        if operation == "APPLY_REVIEWED_PATCH":
            if not _is_hex_digest(item.get("patch_sha256"), 64):
                raise ValueError("mutation review patch digest is invalid")
            observed_patch = item.get("observed_patch_sha256")
            if observed_patch is not None and not _is_hex_digest(observed_patch, 64):
                raise ValueError("mutation review observed patch digest is invalid")
            if not isinstance(item.get("patch_matches_plan"), bool):
                raise ValueError("mutation review patch match flag must be boolean")
        elif operation != "CREATE_FILE":
            if item.get("patch_sha256") is not None:
                raise ValueError("mutation review copy patch digest must be null")
            if item.get("observed_patch_sha256") is not None:
                raise ValueError("mutation review copy observed patch must be null")
            if item.get("patch_matches_plan") is not None:
                raise ValueError("mutation review copy patch result must be null")

        expected_operation_ready = bool(
            item["current_matches_expected"]
            and item["source_matches_target"]
            and item["patch_matches_plan"] is not False
            and item["rollback_recipe_complete"]
        )
        if item["operation_ready"] is not expected_operation_ready:
            raise ValueError("mutation review operation-ready flag is inconsistent")

    source_targets_match = all(item["source_matches_target"] for item in checks)
    current_files_match = all(item["current_matches_expected"] for item in checks)
    rollback_complete = all(item["rollback_recipe_complete"] for item in checks)
    backup_required = any(item["backup_required"] for item in checks)

    if review["source_targets_match_plan"] is not source_targets_match:
        raise ValueError("mutation review source-target summary mismatch")
    if review["current_files_match_plan"] is not current_files_match:
        raise ValueError("mutation review current-file summary mismatch")
    if review["rollback_recipe_complete"] is not rollback_complete:
        raise ValueError("mutation review rollback summary mismatch")
    if review["backup_capture_required"] is not backup_required:
        raise ValueError("mutation review backup-capture summary mismatch")
    if review["backup_material_captured"] is not False:
        raise ValueError("mutation review must not claim backup material capture")
    if review["requires_fresh_execution_recheck"] is not True:
        raise ValueError("mutation review must require a fresh execution recheck")
    if review["requires_backup_capture_before_mutation"] is not backup_required:
        raise ValueError("mutation review backup-before-mutation flag mismatch")
    if review["requires_separate_mutation_authorization"] is not True:
        raise ValueError("mutation review must require separate mutation authorization")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if review[field] is not False:
            raise ValueError(f"mutation review requires {field}=false")

    expected_review_ready = bool(
        review["fresh_gate_matches_saved"]
        and review["fresh_gate_ready"]
        and source_targets_match
        and current_files_match
        and rollback_complete
    )
    if review["review_ready"] is not expected_review_ready:
        raise ValueError("mutation review ready flag is inconsistent with its evidence")

    identity = {
        field: review[field]
        for field in REVIEW_IDENTITY_FIELDS
    }
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if review["review_sha256"] != expected_digest:
        raise ValueError("mutation review digest mismatch")


def evaluate_mutation_review(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_handoff: dict[str, Any],
    saved_plan: dict[str, Any],
    saved_gate: dict[str, Any],
    fresh_gate: dict[str, Any],
    modules: tuple[Any, Any, Any] | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")
    if not production.is_dir():
        raise ValueError(f"production repository is missing: {production}")

    if modules is None:
        gate_module, plan_module, handoff_module = _load_reviewed_modules(source)
    else:
        gate_module, plan_module, handoff_module = modules
        _verify_reviewed_source(source)

    handoff_module.validate_handoff_snapshot(saved_handoff)
    plan_module.validate_deployment_plan(saved_plan)
    gate_module.validate_deployment_gate_report(saved_gate)
    gate_module.validate_deployment_gate_report(fresh_gate)

    if saved_handoff.get("handoff_ready") is not True:
        raise ValueError("saved handoff is not ready")
    if saved_gate.get("gate_ready") is not True:
        raise ValueError("saved deployment gate is not ready")
    if saved_handoff.get("mutation_authorized") is not False:
        raise ValueError("saved handoff must not authorize mutation")
    if saved_plan.get("mutation_authorized") is not False:
        raise ValueError("saved plan must not authorize mutation")
    if saved_gate.get("mutation_authorized") is not False:
        raise ValueError("saved gate must not authorize mutation")

    rebuilt_plan = plan_module.build_deployment_plan(
        source_tree=source,
        handoff_snapshot=saved_handoff,
    )
    plan_module.validate_deployment_plan(rebuilt_plan)
    if rebuilt_plan != saved_plan:
        raise ValueError("saved plan no longer matches reviewed source and handoff")

    handoff_state_sha = str(saved_handoff["production_state_sha256"])
    plan_sha = str(saved_plan["plan_sha256"])
    if saved_plan.get("handoff_state_sha256") != handoff_state_sha:
        raise ValueError("saved plan does not bind the saved handoff")
    if saved_gate.get("plan_sha256") != plan_sha:
        raise ValueError("saved gate does not bind the saved plan")
    if saved_gate.get("saved_handoff_state_sha256") != handoff_state_sha:
        raise ValueError("saved gate does not bind the saved handoff")
    if saved_gate.get("current_handoff_state_sha256") != handoff_state_sha:
        raise ValueError("saved passing gate does not match the saved handoff state")

    if fresh_gate.get("plan_sha256") != plan_sha:
        raise ValueError("fresh gate does not bind the saved plan")
    if fresh_gate.get("saved_handoff_state_sha256") != handoff_state_sha:
        raise ValueError("fresh gate does not bind the saved handoff")

    checks = [
        _operation_check(
            index=index,
            operation=operation,
            source=source,
            repository=production,
            plan_module=plan_module,
        )
        for index, operation in enumerate(saved_plan["operations"])
    ]

    source_targets_match = all(item["source_matches_target"] for item in checks)
    current_files_match = all(item["current_matches_expected"] for item in checks)
    rollback_complete = all(item["rollback_recipe_complete"] for item in checks)
    backup_required = any(item["backup_required"] for item in checks)
    fresh_gate_matches_saved = fresh_gate == saved_gate
    fresh_gate_ready = bool(fresh_gate["gate_ready"])

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
        "production_repository": str(production),
        "handoff_state_sha256": handoff_state_sha,
        "plan_sha256": plan_sha,
        "saved_gate_sha256": str(saved_gate["gate_sha256"]),
        "fresh_gate_sha256": str(fresh_gate["gate_sha256"]),
        "fresh_gate_matches_saved": fresh_gate_matches_saved,
        "fresh_gate_ready": fresh_gate_ready,
        "operation_checks": checks,
        "operation_count": len(checks),
        "deployment_needed": bool(checks),
        "source_targets_match_plan": source_targets_match,
        "current_files_match_plan": current_files_match,
        "rollback_recipe_complete": rollback_complete,
        "backup_capture_required": backup_required,
        "backup_material_captured": False,
        "review_ready": bool(
            fresh_gate_matches_saved
            and fresh_gate_ready
            and source_targets_match
            and current_files_match
            and rollback_complete
        ),
        "requires_fresh_execution_recheck": True,
        "requires_backup_capture_before_mutation": backup_required,
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
    validate_mutation_review(review)
    return review


def build_live_mutation_review(
    *,
    repository: str | Path,
    source_tree: str | Path,
    saved_handoff: dict[str, Any],
    saved_plan: dict[str, Any],
    saved_gate: dict[str, Any],
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")
    if not production.is_dir():
        raise ValueError(f"production repository is missing: {production}")

    modules = _load_reviewed_modules(source)
    gate_module, _, _ = modules
    fresh_gate = gate_module.build_live_deployment_gate(
        repository=production,
        source_tree=source,
        saved_handoff=saved_handoff,
        saved_plan=saved_plan,
    ).to_record()

    return evaluate_mutation_review(
        repository=production,
        source_tree=source,
        saved_handoff=saved_handoff,
        saved_plan=saved_plan,
        saved_gate=saved_gate,
        fresh_gate=fresh_gate,
        modules=modules,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run the fresh read-only manual market/PAPER deployment gate, "
            "verify every planned production file against its expected-current "
            "blob, verify reviewed source targets and patch bytes, and emit a "
            "sealed rollback-aware mutation review. This tool never changes "
            "production files, captures backups, restarts services, enables "
            "timers, moves detector cursors, or authorizes mutation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--gate", required=True)
    args = parser.parse_args()

    review = build_live_mutation_review(
        repository=args.repo,
        source_tree=args.source_tree,
        saved_handoff=_load_json(Path(args.handoff)),
        saved_plan=_load_json(Path(args.plan)),
        saved_gate=_load_json(Path(args.gate)),
    )
    print(json.dumps(review, indent=2, sort_keys=True))
    if not review["review_ready"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
