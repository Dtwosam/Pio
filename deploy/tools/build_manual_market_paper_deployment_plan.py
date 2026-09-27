from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_DEPLOYMENT_PLAN_V1"

HANDOFF_TOOL = Path("deploy/tools/manual_market_paper_handoff.py")
PREREQUISITE_MANIFEST = Path(
    "deploy/manifests/market-paper-phase2-prerequisites.json"
)
MANUAL_RUNTIME_MANIFEST = Path(
    "deploy/manifests/market-paper-manual-cycle.json"
)
RUNTIME_MANIFEST = Path("deploy/manifests/market-paper-runtime.json")
STATE_READER_TOOL = Path(
    "deploy/tools/apply_phase2_single_slot_stack_patch.py"
)
STATE_READER_PATCH = Path(
    "deploy/patches/phase2-single-slot-stack-state-reader.patch"
)

REVIEWED_SOURCE_BLOBS = {
    HANDOFF_TOOL: "506e5b92cad25ef3990fc2e123221458159d3546",
    PREREQUISITE_MANIFEST: "dfb1eb62241f6fc3b6351e3b40b524fb68f4a61e",
    MANUAL_RUNTIME_MANIFEST: "1891afe2c88e267a3dd76fdcdb717a49bc94b15c",
    RUNTIME_MANIFEST: "5d4330b905aba20692bcd75ecf324cb8b1a0f684",
    STATE_READER_TOOL: "a2e4e0c0435f15be7126e253d1861955be6c2ecd",
    STATE_READER_PATCH: "330e2c33956f8a96850a1e072d6a2fa0a4d619af",
}

READY_STATUSES = {"ALREADY_TARGET", "READY_CREATE", "READY_UPDATE"}
PENDING_STATUSES = {"READY_CREATE", "READY_UPDATE"}
LAYER_ORDER = (
    "PHASE2_SHARED_PREREQUISITES",
    "STATE_READER",
    "MARKET_PAPER_RUNTIME",
)


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load reviewed module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _verify_reviewed_source(source: Path) -> None:
    for relative, expected_blob in REVIEWED_SOURCE_BLOBS.items():
        path = source / relative
        if not path.is_file():
            raise ValueError(f"reviewed deployment-plan artifact is missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(
                f"reviewed deployment-plan artifact mismatch: {relative}"
            )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _validate_manifest(
    payload: dict[str, Any],
    *,
    label: str,
) -> tuple[str, ...]:
    if payload.get("production_deployment_authorized") is not False:
        raise ValueError(f"{label} must remain production-deployment locked")
    if payload.get("deployment_guard_apply_locked") is not True:
        raise ValueError(f"{label} must remain apply locked")

    files = payload.get("deployment_files")
    targets = payload.get("deployment_target_file_blobs")
    bases = payload.get("deployment_base_file_blobs")
    if not isinstance(files, list) or not files:
        raise ValueError(f"{label} deployment_files must be non-empty")
    if not isinstance(targets, dict) or not isinstance(bases, dict):
        raise ValueError(f"{label} blob maps are missing")
    if len(files) != len(set(files)):
        raise ValueError(f"{label} deployment_files contains duplicates")
    if set(files) != set(targets) or set(files) != set(bases):
        raise ValueError(f"{label} manifest path/blob maps disagree")

    for path in files:
        target = targets[path]
        base = bases[path]
        if not isinstance(target, str) or len(target) != 40:
            raise ValueError(f"{label} target blob is invalid: {path}")
        if base is not None and (
            not isinstance(base, str) or len(base) != 40
        ):
            raise ValueError(f"{label} base blob is invalid: {path}")
    return tuple(str(path) for path in files)


def _verify_source_targets(
    source: Path,
    payload: dict[str, Any],
    *,
    label: str,
) -> None:
    for relative in payload["deployment_files"]:
        path = source / relative
        if not path.is_file():
            raise ValueError(f"{label} source file is missing: {relative}")
        actual = _git_blob_sha(path)
        expected = payload["deployment_target_file_blobs"][relative]
        if actual != expected:
            raise ValueError(f"{label} source target mismatch: {relative}")


def _verify_runtime_manifest_equivalence(
    manual_runtime: dict[str, Any],
    runtime: dict[str, Any],
) -> None:
    for field in (
        "deployment_files",
        "deployment_target_file_blobs",
        "deployment_base_file_blobs",
    ):
        if manual_runtime.get(field) != runtime.get(field):
            raise ValueError(
                f"manual/runtime deployment manifests disagree on {field}"
            )
    if manual_runtime.get("prerequisite_collection_manifest") != (
        runtime.get("phase2_collection_prerequisite_manifest")
    ):
        raise ValueError("manual/runtime prerequisite manifest linkage disagrees")
    if manual_runtime.get("prerequisite_collection_target_ref") != (
        runtime.get("phase2_collection_prerequisite_ref")
    ):
        raise ValueError("manual/runtime prerequisite target lineage disagrees")


def _validate_overlay_summary(
    summary: dict[str, Any],
    manifest: dict[str, Any],
    *,
    label: str,
) -> tuple[dict[str, str], ...]:
    if not isinstance(summary, dict):
        raise ValueError(f"{label} handoff summary is missing")
    if summary.get("content_ready") is not True:
        raise ValueError(f"{label} handoff summary is not content-ready")
    if summary.get("apply_authorized") is not False:
        raise ValueError(f"{label} handoff summary must not authorize apply")

    nonready = summary.get("nonready")
    pending = summary.get("pending")
    status_counts = summary.get("status_counts")
    files_changed = summary.get("files_changed")
    deployed = summary.get("deployed")

    if nonready not in ([], ()):
        raise ValueError(f"{label} handoff summary contains non-ready files")
    if not isinstance(pending, (list, tuple)):
        raise ValueError(f"{label} pending list is invalid")
    if not isinstance(status_counts, dict):
        raise ValueError(f"{label} status counts are invalid")
    if any(status not in READY_STATUSES for status in status_counts):
        raise ValueError(f"{label} contains unsupported ready status")

    total = sum(int(value) for value in status_counts.values())
    if total != len(manifest["deployment_files"]):
        raise ValueError(f"{label} status count does not match manifest scope")

    pending_records: list[dict[str, str]] = []
    seen: set[str] = set()
    pending_counts = {status: 0 for status in PENDING_STATUSES}
    manifest_paths = set(manifest["deployment_files"])
    for item in pending:
        if not isinstance(item, dict):
            raise ValueError(f"{label} pending item is invalid")
        path = item.get("path")
        status = item.get("status")
        if not isinstance(path, str) or status not in PENDING_STATUSES:
            raise ValueError(f"{label} pending item is invalid")
        if path not in manifest_paths:
            raise ValueError(f"{label} pending path is outside manifest: {path}")
        if path in seen:
            raise ValueError(f"{label} pending path is duplicated: {path}")
        seen.add(path)
        pending_counts[status] += 1
        pending_records.append({"path": path, "status": status})

    if int(files_changed) != len(pending_records):
        raise ValueError(f"{label} files_changed does not match pending list")
    for status in PENDING_STATUSES:
        if int(status_counts.get(status, 0)) != pending_counts[status]:
            raise ValueError(f"{label} pending/status counts disagree")

    all_target = int(status_counts.get("ALREADY_TARGET", 0)) == total
    if bool(deployed) != all_target:
        raise ValueError(f"{label} deployed flag disagrees with status counts")

    return tuple(pending_records)


def _copy_operations(
    *,
    layer: str,
    pending: tuple[dict[str, str], ...],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    by_path = {item["path"]: item["status"] for item in pending}
    operations: list[dict[str, Any]] = []
    for path in manifest["deployment_files"]:
        status = by_path.get(path)
        if status is None:
            continue
        base = manifest["deployment_base_file_blobs"][path]
        target = manifest["deployment_target_file_blobs"][path]
        if status == "READY_CREATE" and base is not None:
            raise ValueError(f"{layer} READY_CREATE has non-null base: {path}")
        if status == "READY_UPDATE" and base is None:
            raise ValueError(f"{layer} READY_UPDATE has null base: {path}")
        operations.append(
            {
                "layer": layer,
                "operation": (
                    "CREATE_FILE" if status == "READY_CREATE" else "UPDATE_FILE"
                ),
                "path": path,
                "expected_current_blob": base,
                "target_blob": target,
                "source_blob": target,
                "backup_required": status == "READY_UPDATE",
            }
        )
    return operations


def _state_reader_operation(
    summary: dict[str, Any],
    *,
    state_module: Any,
    patch_sha256: str,
) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        raise ValueError("state-reader handoff summary is missing")
    if summary.get("preflight_ready") is not True:
        raise ValueError("state-reader preflight is not ready")
    if summary.get("error_category") is not None:
        raise ValueError("state-reader preflight has an error category")

    status = summary.get("status")
    deployed = summary.get("deployed")
    if status == "ALREADY_TARGET":
        if deployed is not True:
            raise ValueError("state-reader deployed flag disagrees with status")
        return []
    if status != "READY_UPDATE" or deployed is not False:
        raise ValueError(f"unsupported state-reader status: {status}")

    return [
        {
            "layer": "STATE_READER",
            "operation": "APPLY_REVIEWED_PATCH",
            "path": str(state_module.TARGET_PATH),
            "expected_current_blob": str(state_module.STACK_BASE_BLOB_SHA),
            "target_blob": str(state_module.STACK_TARGET_BLOB_SHA),
            "patch_sha256": patch_sha256,
            "backup_required": True,
        }
    ]


def build_deployment_plan(
    *,
    source_tree: str | Path,
    handoff_snapshot: dict[str, Any],
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError(f"reviewed source tree is missing: {source}")
    _verify_reviewed_source(source)

    handoff_module = _load_module(
        source / HANDOFF_TOOL,
        "manual_market_paper_deployment_plan_handoff",
    )
    handoff_module.validate_handoff_snapshot(handoff_snapshot)
    if handoff_snapshot.get("handoff_ready") is not True:
        raise ValueError("handoff snapshot is not ready")
    if handoff_snapshot.get("mutation_authorized") is not False:
        raise ValueError("handoff snapshot must not authorize mutation")

    state = handoff_snapshot.get("state")
    if not isinstance(state, dict):
        raise ValueError("handoff state is missing")
    if state.get("deployment_preflight_clean") is not True:
        raise ValueError("deployment preflight is not clean")
    if state.get("mutation_authorized") is not False:
        raise ValueError("readiness state must not authorize mutation")

    prerequisite = _load_json(source / PREREQUISITE_MANIFEST)
    manual_runtime = _load_json(source / MANUAL_RUNTIME_MANIFEST)
    runtime = _load_json(source / RUNTIME_MANIFEST)
    _validate_manifest(prerequisite, label="shared prerequisite")
    _validate_manifest(manual_runtime, label="manual runtime")
    _validate_manifest(runtime, label="runtime")
    _verify_runtime_manifest_equivalence(manual_runtime, runtime)
    _verify_source_targets(source, prerequisite, label="shared prerequisite")
    _verify_source_targets(source, manual_runtime, label="manual runtime")

    prerequisite_paths = set(prerequisite["deployment_files"])
    runtime_paths = set(manual_runtime["deployment_files"])
    if prerequisite_paths & runtime_paths:
        raise ValueError("shared prerequisite and runtime manifests overlap")
    if manual_runtime.get("prerequisite_collection_manifest") != str(
        PREREQUISITE_MANIFEST
    ):
        raise ValueError("manual runtime points to an unexpected prerequisite")
    if runtime.get("phase2_collection_prerequisite_manifest") != str(
        PREREQUISITE_MANIFEST
    ):
        raise ValueError("runtime points to an unexpected prerequisite")
    if set(runtime.get("phase2_prerequisite_files", ())) != prerequisite_paths:
        raise ValueError("runtime prerequisite file list disagrees with manifest")

    state_module = _load_module(
        source / STATE_READER_TOOL,
        "manual_market_paper_deployment_plan_state_reader",
    )
    state_path = str(state_module.TARGET_PATH)
    if state_path in prerequisite_paths or state_path in runtime_paths:
        raise ValueError("state-reader path overlaps a copy manifest")
    if manual_runtime.get("prerequisite_state_reader_target_blob") != str(
        state_module.STACK_TARGET_BLOB_SHA
    ):
        raise ValueError("manual runtime state-reader target lineage disagrees")
    state_source = source / state_path
    if not state_source.is_file():
        raise ValueError("reviewed state-reader target source is missing")
    if _git_blob_sha(state_source) != str(state_module.STACK_TARGET_BLOB_SHA):
        raise ValueError("reviewed state-reader source is not the target blob")

    patch_path = source / STATE_READER_PATCH
    changed_paths = state_module.changed_paths(
        patch_path.read_text(encoding="utf-8")
    )
    if changed_paths != (state_path,):
        raise ValueError("reviewed state-reader patch scope is unexpected")
    patch_sha256 = _sha256(patch_path)

    phase2_pending = _validate_overlay_summary(
        state.get("phase2"),
        prerequisite,
        label="shared prerequisite",
    )
    runtime_pending = _validate_overlay_summary(
        state.get("market_paper"),
        manual_runtime,
        label="manual runtime",
    )

    operations: list[dict[str, Any]] = []
    operations.extend(
        _copy_operations(
            layer="PHASE2_SHARED_PREREQUISITES",
            pending=phase2_pending,
            manifest=prerequisite,
        )
    )
    operations.extend(
        _state_reader_operation(
            state.get("state_reader"),
            state_module=state_module,
            patch_sha256=patch_sha256,
        )
    )
    operations.extend(
        _copy_operations(
            layer="MARKET_PAPER_RUNTIME",
            pending=runtime_pending,
            manifest=manual_runtime,
        )
    )

    deployment_needed = bool(operations)
    if bool(handoff_snapshot.get("deployment_needed")) != deployment_needed:
        raise ValueError("handoff deployment-needed flag disagrees with plan")

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "handoff_artifact_type": handoff_snapshot.get("artifact_type"),
        "handoff_state_sha256": handoff_snapshot.get("production_state_sha256"),
        "reviewed_source_blobs": {
            str(path): blob
            for path, blob in sorted(
                REVIEWED_SOURCE_BLOBS.items(),
                key=lambda item: str(item[0]),
            )
        },
        "required_layer_order": list(LAYER_ORDER),
        "operations": operations,
        "operation_count": len(operations),
        "deployment_needed": deployment_needed,
        "plan_ready": True,
        "requires_fresh_handoff_verification": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    return {
        **identity,
        "plan_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }


def _load_handoff(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic, non-mutating manual market/PAPER deployment "
            "plan from a sealed preflight handoff. This tool never copies or "
            "patches production files, restarts services, enables timers, "
            "moves detector cursors, or authorizes mutation."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--handoff", required=True)
    args = parser.parse_args()

    plan = build_deployment_plan(
        source_tree=args.source_tree,
        handoff_snapshot=_load_handoff(Path(args.handoff)),
    )
    print(json.dumps(plan, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
