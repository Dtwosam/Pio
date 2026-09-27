from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_MUTATION_REVIEW_V1"

GATE_TOOL = Path(
    "deploy/tools/check_manual_market_paper_preserved_deployment_gate.py"
)
PLAN_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_deployment_plan.py"
)

REVIEWED_SOURCE_BLOBS = {
    GATE_TOOL: "1489160a5888285a63b14bc8b323318e934b76dd",
    PLAN_TOOL: "0887331414259b22dace2ad90aa74296f1c4dd49",
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
    "private_bundle_sha256",
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
    "candidate_content_included",
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
    "source_origin",
    "expected_current_blob",
    "observed_current_blob",
    "current_state",
    "current_matches_expected",
    "target_blob",
    "observed_source_blob",
    "source_state",
    "source_matches_target",
    "backup_required",
    "rollback_operation",
    "rollback_blob",
    "rollback_recipe_complete",
    "private_bundle_sha256",
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


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"JSON artifact must not be a symlink: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"JSON artifact must be a regular file: {path}")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _verify_reviewed_source(source: Path) -> None:
    for relative, expected in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"reviewed preserved-review artifact is missing: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed preserved-review artifact mismatch: {relative}")


def _load_reviewed_modules(source: Path) -> tuple[Any, Any, Any]:
    _verify_reviewed_source(source)
    gate_module = _load_module(
        source / GATE_TOOL,
        "manual_market_paper_preserved_mutation_review_gate",
    )
    plan_module = _load_module(
        source / PLAN_TOOL,
        "manual_market_paper_preserved_mutation_review_plan",
    )
    gate_module._verify_reviewed_source(source)
    handoff_module = _load_module(
        source / gate_module.HANDOFF_TOOL,
        "manual_market_paper_preserved_mutation_review_handoff",
    )
    if _git_blob_sha(source / PLAN_TOOL) != REVIEWED_SOURCE_BLOBS[PLAN_TOOL]:
        raise ValueError("reviewed preserved-review planner lineage mismatch")
    return gate_module, plan_module, handoff_module


def _safe_relative_path(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError("preserved mutation-review path is invalid")
    if "\" in raw:
        raise ValueError(f"preserved mutation-review path contains a backslash: {raw}")
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise ValueError(f"preserved mutation-review path must be relative: {raw}")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"preserved mutation-review path is unsafe: {raw}")
    if "/".join(pure.parts) != raw:
        raise ValueError(f"preserved mutation-review path is not normalized: {raw}")
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


def _inspect_target(
    root: Path,
    relative: Path,
    expected_blob: str | None,
    *,
    absent_allowed: bool,
) -> tuple[str, str | None, bool]:
    parent_safe, state = _parent_safety(root, relative)
    if not parent_safe:
        return str(state), None, False

    target = root / relative
    if target.is_symlink():
        return "TARGET_SYMLINK", None, False

    if expected_blob is None:
        if absent_allowed and not target.exists():
            return "ABSENT_AS_EXPECTED", None, True
        if target.exists():
            return "UNEXPECTED_PRESENT", None, False
        return "MISSING", None, False

    if not target.exists():
        return "MISSING", None, False
    if not target.is_file():
        return "NOT_REGULAR_FILE", None, False

    observed = _git_blob_sha(target)
    if observed == expected_blob:
        return "BLOB_MATCH", observed, True
    return "BLOB_MISMATCH", observed, False


def _private_bundle_dir(
    *,
    source: Path,
    plan_module: Any,
    bundle_report: dict[str, Any],
    expected_bundle_sha256: str,
) -> Path:
    bundle_module = _load_module(
        source / plan_module.BUNDLE_TOOL,
        "manual_market_paper_preserved_mutation_review_bundle",
    )
    return plan_module._bundle_dir(
        bundle_module=bundle_module,
        bundle_report=bundle_report,
        expected_bundle_sha256=expected_bundle_sha256,
    )


def _operation_check(
    *,
    index: int,
    operation: dict[str, Any],
    reviewed_source: Path,
    private_bundle: Path,
    production: Path,
    private_bundle_sha256: str,
) -> dict[str, Any]:
    relative = _safe_relative_path(operation["path"])
    operation_type = operation["operation"]
    expected_current_blob = operation.get("expected_current_blob")
    target_blob = str(operation["target_blob"])

    current_state, observed_current_blob, current_matches = _inspect_target(
        production,
        relative,
        expected_current_blob,
        absent_allowed=operation_type == "CREATE_FILE",
    )

    if operation_type == "UPDATE_PRESERVED_FILE":
        source_origin = "PRIVATE_BUNDLE"
        source_root = private_bundle
        operation_bundle_sha = operation.get("private_bundle_sha256")
        if operation_bundle_sha != private_bundle_sha256:
            raise ValueError(
                f"preserved operation bundle digest mismatch: {operation['path']}"
            )
    elif operation_type in {"CREATE_FILE", "UPDATE_FILE"}:
        source_origin = "REVIEWED_SOURCE"
        source_root = reviewed_source
        operation_bundle_sha = None
        if "private_bundle_sha256" in operation:
            raise ValueError(
                f"standard operation cannot bind private bundle: {operation['path']}"
            )
    else:
        raise ValueError(
            f"preserved mutation-review operation type is invalid: {operation_type}"
        )

    source_state, observed_source_blob, source_matches = _inspect_target(
        source_root,
        relative,
        target_blob,
        absent_allowed=False,
    )

    backup_required = bool(operation["backup_required"])
    if operation_type == "CREATE_FILE":
        rollback_operation = "DELETE_CREATED_FILE"
        rollback_blob = None
        rollback_recipe_complete = (
            expected_current_blob is None and backup_required is False
        )
    else:
        rollback_operation = "RESTORE_EXPECTED_BLOB"
        rollback_blob = expected_current_blob
        rollback_recipe_complete = bool(
            backup_required is True
            and _is_hex_digest(expected_current_blob, 40)
        )

    operation_ready = bool(
        current_matches
        and source_matches
        and rollback_recipe_complete
    )

    return {
        "index": index,
        "layer": operation["layer"],
        "operation": operation_type,
        "path": operation["path"],
        "plan_operation_sha256": hashlib.sha256(
            _canonical_bytes(operation)
        ).hexdigest(),
        "source_origin": source_origin,
        "expected_current_blob": expected_current_blob,
        "observed_current_blob": observed_current_blob,
        "current_state": current_state,
        "current_matches_expected": current_matches,
        "target_blob": target_blob,
        "observed_source_blob": observed_source_blob,
        "source_state": source_state,
        "source_matches_target": source_matches,
        "backup_required": backup_required,
        "rollback_operation": rollback_operation,
        "rollback_blob": rollback_blob,
        "rollback_recipe_complete": rollback_recipe_complete,
        "private_bundle_sha256": operation_bundle_sha,
        "operation_ready": operation_ready,
    }


def validate_preserved_mutation_review(review: dict[str, Any]) -> None:
    if not isinstance(review, dict):
        raise ValueError("preserved mutation review must be a JSON object")
    if set(review) != set(REVIEW_IDENTITY_FIELDS) | {"review_sha256"}:
        raise ValueError("preserved mutation review fields do not match reviewed schema")
    if review.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preserved mutation review format")
    if review.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preserved mutation review artifact type")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if review.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("preserved mutation review source lineage mismatch")

    repository = review.get("production_repository")
    if not isinstance(repository, str) or not repository.startswith("/"):
        raise ValueError("preserved mutation review repository must be absolute")

    for field in (
        "handoff_state_sha256",
        "plan_sha256",
        "private_bundle_sha256",
        "saved_gate_sha256",
        "fresh_gate_sha256",
        "review_sha256",
    ):
        if not _is_hex_digest(review.get(field), 64):
            raise ValueError(f"preserved mutation review {field} is invalid")

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
        "candidate_content_included",
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
            raise ValueError(f"preserved mutation review {field} must be boolean")

    expected_gate_match = (
        review["saved_gate_sha256"] == review["fresh_gate_sha256"]
    )
    if review["fresh_gate_matches_saved"] is not expected_gate_match:
        raise ValueError("preserved mutation review fresh-gate match flag mismatch")

    checks = review.get("operation_checks")
    if not isinstance(checks, list):
        raise ValueError("preserved mutation review operation checks must be a list")
    count = review.get("operation_count")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or count != len(checks)
    ):
        raise ValueError("preserved mutation review operation count mismatch")
    if review["deployment_needed"] is not bool(checks):
        raise ValueError("preserved mutation review deployment-needed mismatch")

    seen: set[str] = set()
    previous_layer = -1
    for expected_index, item in enumerate(checks):
        if not isinstance(item, dict) or set(item) != set(OPERATION_CHECK_FIELDS):
            raise ValueError("preserved mutation review operation schema mismatch")
        if item.get("index") != expected_index:
            raise ValueError("preserved mutation review indexes are not contiguous")

        layer = item.get("layer")
        if layer not in LAYER_ORDER:
            raise ValueError("preserved mutation review layer is invalid")
        layer_index = LAYER_ORDER.index(layer)
        if layer_index < previous_layer:
            raise ValueError("preserved mutation review layers are out of order")
        previous_layer = layer_index

        path = item.get("path")
        _safe_relative_path(path)
        if path in seen:
            raise ValueError(f"preserved mutation review path duplicated: {path}")
        seen.add(path)

        operation = item.get("operation")
        if operation not in {
            "CREATE_FILE",
            "UPDATE_FILE",
            "UPDATE_PRESERVED_FILE",
        }:
            raise ValueError("preserved mutation review operation type is invalid")

        if not _is_hex_digest(item.get("plan_operation_sha256"), 64):
            raise ValueError("preserved mutation review operation digest is invalid")
        if not _is_hex_digest(item.get("target_blob"), 40):
            raise ValueError("preserved mutation review target blob is invalid")

        expected_current = item.get("expected_current_blob")
        if expected_current is not None and not _is_hex_digest(expected_current, 40):
            raise ValueError("preserved mutation review expected-current blob is invalid")
        for field in ("observed_current_blob", "observed_source_blob"):
            value = item.get(field)
            if value is not None and not _is_hex_digest(value, 40):
                raise ValueError(f"preserved mutation review {field} is invalid")

        for field in (
            "current_matches_expected",
            "source_matches_target",
            "backup_required",
            "rollback_recipe_complete",
            "operation_ready",
        ):
            if not isinstance(item.get(field), bool):
                raise ValueError(f"preserved mutation review {field} must be boolean")
        for field in ("current_state", "source_state", "source_origin"):
            if not isinstance(item.get(field), str) or not item[field]:
                raise ValueError(f"preserved mutation review {field} is invalid")

        bundle_sha = item.get("private_bundle_sha256")
        if operation == "UPDATE_PRESERVED_FILE":
            if item["source_origin"] != "PRIVATE_BUNDLE":
                raise ValueError("preserved update must source from private bundle")
            if bundle_sha != review["private_bundle_sha256"]:
                raise ValueError("preserved update bundle digest mismatch")
        else:
            if item["source_origin"] != "REVIEWED_SOURCE":
                raise ValueError("standard operation must source from reviewed source")
            if bundle_sha is not None:
                raise ValueError("standard operation private-bundle digest must be null")

        if operation == "CREATE_FILE":
            if expected_current is not None or item["backup_required"] is not False:
                raise ValueError("preserved mutation review create semantics are invalid")
            if item["rollback_operation"] != "DELETE_CREATED_FILE":
                raise ValueError("preserved mutation review create rollback action is invalid")
            if item["rollback_blob"] is not None:
                raise ValueError("preserved mutation review create rollback blob must be null")
        else:
            if not _is_hex_digest(expected_current, 40):
                raise ValueError("preserved mutation review update base is invalid")
            if item["backup_required"] is not True:
                raise ValueError("preserved mutation review update must require backup")
            if item["rollback_operation"] != "RESTORE_EXPECTED_BLOB":
                raise ValueError("preserved mutation review update rollback action is invalid")
            if item["rollback_blob"] != expected_current:
                raise ValueError("preserved mutation review rollback blob mismatch")

        expected_ready = bool(
            item["current_matches_expected"]
            and item["source_matches_target"]
            and item["rollback_recipe_complete"]
        )
        if item["operation_ready"] is not expected_ready:
            raise ValueError("preserved mutation review operation-ready mismatch")

    source_targets_match = all(item["source_matches_target"] for item in checks)
    current_files_match = all(item["current_matches_expected"] for item in checks)
    rollback_complete = all(item["rollback_recipe_complete"] for item in checks)
    backup_required = any(item["backup_required"] for item in checks)

    if review["source_targets_match_plan"] is not source_targets_match:
        raise ValueError("preserved mutation review source-target summary mismatch")
    if review["current_files_match_plan"] is not current_files_match:
        raise ValueError("preserved mutation review current-file summary mismatch")
    if review["rollback_recipe_complete"] is not rollback_complete:
        raise ValueError("preserved mutation review rollback summary mismatch")
    if review["backup_capture_required"] is not backup_required:
        raise ValueError("preserved mutation review backup summary mismatch")
    if review["backup_material_captured"] is not False:
        raise ValueError("preserved mutation review must not claim backup capture")
    if review["candidate_content_included"] is not False:
        raise ValueError("preserved mutation review must not embed candidate content")
    if review["requires_fresh_execution_recheck"] is not True:
        raise ValueError("preserved mutation review requires fresh execution recheck")
    if review["requires_backup_capture_before_mutation"] is not backup_required:
        raise ValueError("preserved mutation review backup-before-mutation mismatch")
    if review["requires_separate_mutation_authorization"] is not True:
        raise ValueError("preserved mutation review requires separate authorization")

    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if review[field] is not False:
            raise ValueError(f"preserved mutation review requires {field}=false")

    expected_review_ready = bool(
        review["fresh_gate_matches_saved"]
        and review["fresh_gate_ready"]
        and source_targets_match
        and current_files_match
        and rollback_complete
    )
    if review["review_ready"] is not expected_review_ready:
        raise ValueError("preserved mutation review ready flag is inconsistent")

    identity = {field: review[field] for field in REVIEW_IDENTITY_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if review["review_sha256"] != expected_digest:
        raise ValueError("preserved mutation review digest mismatch")


def evaluate_preserved_mutation_review(
    *,
    repository: str | Path,
    source_tree: str | Path,
    private_bundle_report: dict[str, Any],
    saved_handoff: dict[str, Any],
    saved_plan: dict[str, Any],
    saved_gate: dict[str, Any],
    fresh_gate: dict[str, Any],
    modules: tuple[Any, Any, Any] | None = None,
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository is missing")

    if modules is None:
        gate_module, plan_module, handoff_module = _load_reviewed_modules(source)
    else:
        gate_module, plan_module, handoff_module = modules
        _verify_reviewed_source(source)

    handoff_module.validate_handoff_snapshot(saved_handoff)
    plan_module.validate_preserved_deployment_plan(saved_plan)
    gate_module.validate_preserved_gate(saved_gate)
    gate_module.validate_preserved_gate(fresh_gate)

    if saved_handoff.get("handoff_ready") is not True:
        raise ValueError("saved preserved handoff is not ready")
    if saved_plan.get("plan_ready") is not True:
        raise ValueError("saved preserved plan is not ready")
    if saved_gate.get("gate_ready") is not True:
        raise ValueError("saved preserved gate is not ready")
    for label, artifact in (
        ("handoff", saved_handoff),
        ("plan", saved_plan),
        ("gate", saved_gate),
    ):
        if artifact.get("mutation_authorized") is not False:
            raise ValueError(f"saved preserved {label} must not authorize mutation")

    rebuilt_plan = plan_module.build_preserved_deployment_plan(
        source_tree=source,
        handoff_snapshot=saved_handoff,
        private_bundle_report=private_bundle_report,
    )
    plan_module.validate_preserved_deployment_plan(rebuilt_plan)
    if rebuilt_plan != saved_plan:
        raise ValueError("saved preserved plan no longer matches reviewed inputs")

    handoff_state_sha = str(saved_handoff["production_state_sha256"])
    plan_sha = str(saved_plan["plan_sha256"])
    bundle_sha = str(saved_plan["private_bundle_sha256"])

    if saved_plan.get("handoff_sha256") != saved_handoff["handoff_sha256"]:
        raise ValueError("saved preserved plan does not bind saved handoff")
    if saved_plan.get("handoff_state_sha256") != handoff_state_sha:
        raise ValueError("saved preserved plan state lineage mismatch")
    if private_bundle_report.get("bundle_sha256") != bundle_sha:
        raise ValueError("private bundle report does not bind saved plan")
    if saved_gate.get("saved_handoff_sha256") != saved_handoff["handoff_sha256"]:
        raise ValueError("saved preserved gate does not bind saved handoff")
    if saved_gate.get("saved_handoff_state_sha256") != handoff_state_sha:
        raise ValueError("saved preserved gate state lineage mismatch")
    if saved_gate.get("saved_plan_sha256") != plan_sha:
        raise ValueError("saved preserved gate does not bind saved plan")
    if saved_gate.get("private_bundle_sha256") != bundle_sha:
        raise ValueError("saved preserved gate does not bind private bundle")

    if fresh_gate.get("saved_handoff_sha256") != saved_handoff["handoff_sha256"]:
        raise ValueError("fresh preserved gate does not bind saved handoff")
    if fresh_gate.get("saved_plan_sha256") != plan_sha:
        raise ValueError("fresh preserved gate does not bind saved plan")
    if fresh_gate.get("private_bundle_sha256") != bundle_sha:
        raise ValueError("fresh preserved gate does not bind private bundle")

    private_bundle = _private_bundle_dir(
        source=source,
        plan_module=plan_module,
        bundle_report=private_bundle_report,
        expected_bundle_sha256=bundle_sha,
    )

    checks = [
        _operation_check(
            index=index,
            operation=operation,
            reviewed_source=source,
            private_bundle=private_bundle,
            production=production,
            private_bundle_sha256=bundle_sha,
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
        "private_bundle_sha256": bundle_sha,
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
        "candidate_content_included": False,
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
    validate_preserved_mutation_review(review)
    return review


def build_live_preserved_mutation_review(
    *,
    repository: str | Path,
    source_tree: str | Path,
    private_bundle_report_path: str | Path,
    saved_handoff: dict[str, Any],
    saved_plan: dict[str, Any],
    saved_gate: dict[str, Any],
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    production = Path(repository).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    if not production.is_dir():
        raise ValueError("production repository is missing")

    modules = _load_reviewed_modules(source)
    gate_module, _, _ = modules
    bundle_path = Path(private_bundle_report_path)
    private_bundle_report = _load_json(bundle_path)

    fresh_gate = gate_module.build_preserved_gate(
        repository=production,
        source_tree=source,
        private_bundle_report_path=bundle_path,
        saved_handoff=saved_handoff,
        saved_plan=saved_plan,
    )

    return evaluate_preserved_mutation_review(
        repository=production,
        source_tree=source,
        private_bundle_report=private_bundle_report,
        saved_handoff=saved_handoff,
        saved_plan=saved_plan,
        saved_gate=saved_gate,
        fresh_gate=fresh_gate,
        modules=modules,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run the preserved read-only deployment gate, verify each planned "
            "production file against its expected-current blob, verify preserved "
            "candidate sources only from the sealed private bundle and standard "
            "sources only from reviewed source, and emit a rollback-aware review. "
            "This tool captures no backups and authorizes no mutation."
        )
    )
    parser.add_argument("--repo", default="/opt/pio")
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--gate", required=True)
    args = parser.parse_args()

    review = build_live_preserved_mutation_review(
        repository=args.repo,
        source_tree=args.source_tree,
        private_bundle_report_path=args.private_bundle_report,
        saved_handoff=_load_json(Path(args.handoff)),
        saved_plan=_load_json(Path(args.plan)),
        saved_gate=_load_json(Path(args.gate)),
    )
    print(json.dumps(review, indent=2, sort_keys=True))
    if not review["review_ready"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
