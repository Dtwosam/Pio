from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


FORMAT_VERSION = 1
ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_DEPLOYMENT_PLAN_V1"
REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_HANDOFF_ARTIFACT_TYPE = "MANUAL_MARKET_PAPER_PRESERVED_HANDOFF_V1"

HANDOFF_TOOL = Path("deploy/tools/manual_market_paper_preserved_handoff.py")
BUNDLE_TOOL = Path(
    "deploy/tools/build_manual_market_paper_preserved_source_bundle.py"
)
PHASE2_MANIFEST = Path(
    "deploy/manifests/market-paper-phase2-prerequisites.json"
)
RUNTIME_MANIFEST = Path("deploy/manifests/market-paper-manual-cycle.json")

REVIEWED_SOURCE_BLOBS = {
    HANDOFF_TOOL: "3bc9c551a5bbe17bcedfc30e165a72e21966f407",
    BUNDLE_TOOL: "27c2d87bab3e896d84c22cdef8bd95082ca0cad3",
    PHASE2_MANIFEST: "dfb1eb62241f6fc3b6351e3b40b524fb68f4a61e",
    RUNTIME_MANIFEST: "1891afe2c88e267a3dd76fdcdb717a49bc94b15c",
}

LAYER_ORDER = (
    "PHASE2_SHARED_PREREQUISITES",
    "STATE_READER",
    "MARKET_PAPER_RUNTIME",
)

RESEARCH_STORE_PATH = "python-learner/src/meteora_learner/research_store.py"
STORAGE_PATH = "python-learner/src/meteora_learner/storage.py"
STATE_READER_PATH = "rust-executor/src/state_reader.rs"

EXPECTED_RESEARCH_STORE_CURRENT_BLOB = "f9deb47c10c88a4e1e364dd12d6c7569c3826a98"
EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB = "31bd88e3d74490f5d0b617ff4b36383e7e12e18f"
EXPECTED_STATE_READER_CURRENT_BLOB = "d1267db6708b91bc8cacabffcd397a380866c79a"
EXPECTED_STATE_READER_CANDIDATE_BLOB = "f54a1021cf8f89d285bde957d1f72d81857ec2fa"

PLAN_FIELDS = (
    "format_version",
    "artifact_type",
    "handoff_artifact_type",
    "handoff_sha256",
    "handoff_state_sha256",
    "private_bundle_sha256",
    "reviewed_source_blobs",
    "required_layer_order",
    "operations",
    "operation_count",
    "deployment_needed",
    "plan_ready",
    "candidate_content_included",
    "requires_fresh_handoff_verification",
    "requires_fresh_gate_verification",
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


def _git_blob_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    return _git_blob_sha_bytes(path.read_bytes())


def _is_hex_digest(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe deployment path: {value}")
    normalized = str(path)
    if normalized != value:
        raise ValueError(f"non-normalized deployment path: {value}")
    return normalized


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
            raise ValueError(f"reviewed plan artifact is missing: {relative}")
        if _git_blob_sha(path) != expected:
            raise ValueError(f"reviewed plan artifact mismatch: {relative}")


def _load_manifest(source: Path, relative: Path) -> dict[str, Any]:
    path = source / relative
    payload = _load_json(path)
    files = payload.get("deployment_files")
    targets = payload.get("deployment_target_file_blobs")
    bases = payload.get("deployment_base_file_blobs")
    if not isinstance(files, list) or not files:
        raise ValueError(f"manifest deployment_files is invalid: {relative}")
    if not isinstance(targets, dict) or not isinstance(bases, dict):
        raise ValueError(f"manifest blob maps are invalid: {relative}")

    normalized = [_safe_relative_path(str(item)) for item in files]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"manifest paths contain duplicates: {relative}")
    if set(normalized) != set(targets) or set(normalized) != set(bases):
        raise ValueError(f"manifest blob keys do not match files: {relative}")

    for item in normalized:
        target = targets[item]
        base = bases[item]
        if not isinstance(target, str) or re.fullmatch(r"[0-9a-f]{40}", target) is None:
            raise ValueError(f"manifest target blob is invalid: {item}")
        if base is not None and (
            not isinstance(base, str)
            or re.fullmatch(r"[0-9a-f]{40}", base) is None
        ):
            raise ValueError(f"manifest base blob is invalid: {item}")

    payload["deployment_files"] = normalized
    return payload


def _bundle_dir(
    *,
    bundle_module: Any,
    bundle_report: dict[str, Any],
    expected_bundle_sha256: str,
) -> Path:
    bundle_module.validate_bundle_report(bundle_report)
    if bundle_report.get("bundle_ready") is not True:
        raise ValueError("private preserved-source bundle is not ready")
    if bundle_report.get("bundle_sha256") != expected_bundle_sha256:
        raise ValueError("private bundle digest does not match handoff")

    path = Path(str(bundle_report["bundle_dir"]))
    if path.is_symlink():
        raise ValueError("private bundle directory must not be a symlink")
    resolved = path.resolve(strict=True)
    var_tmp = Path("/var/tmp").resolve()
    if var_tmp not in resolved.parents or not resolved.is_dir():
        raise ValueError("private bundle directory is outside reviewed scope")
    return resolved


def _has_symlink_parent(root: Path, path: Path) -> bool:
    current = path.parent
    while current != root:
        if current.is_symlink():
            return True
        if current == current.parent:
            return True
        current = current.parent
    return root.is_symlink()


def _source_blob(source: Path, relative: str) -> str:
    relative = _safe_relative_path(relative)
    path = source / relative
    try:
        path.resolve(strict=False).relative_to(source.resolve())
    except ValueError as exc:
        raise ValueError(f"source path escapes private bundle: {relative}") from exc
    if _has_symlink_parent(source, path):
        raise ValueError(f"source file has a symlinked parent: {relative}")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"source file is not regular: {relative}")
    blob = _git_blob_sha(path)
    if blob is None:
        raise ValueError(f"source blob is unavailable: {relative}")
    return blob


def _expected_phase2_contract(
    manifest: dict[str, Any],
) -> list[tuple[str, str | None, str]]:
    result: list[tuple[str, str | None, str]] = []
    for path in manifest["deployment_files"]:
        if path == RESEARCH_STORE_PATH:
            result.append(
                (
                    path,
                    EXPECTED_RESEARCH_STORE_CURRENT_BLOB,
                    EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
                )
            )
        else:
            result.append(
                (
                    path,
                    manifest["deployment_base_file_blobs"][path],
                    manifest["deployment_target_file_blobs"][path],
                )
            )
    return result


def _expected_runtime_contract(
    manifest: dict[str, Any],
) -> list[tuple[str, str | None, str]]:
    return [
        (
            path,
            manifest["deployment_base_file_blobs"][path],
            manifest["deployment_target_file_blobs"][path],
        )
        for path in manifest["deployment_files"]
    ]


def _validate_state_files(
    *,
    state_layer: dict[str, Any],
    contract: list[tuple[str, str | None, str]],
    label: str,
) -> list[dict[str, Any]]:
    files = state_layer.get("files")
    if not isinstance(files, list) or len(files) != len(contract):
        raise ValueError(f"{label} handoff file set is invalid")

    observed = [
        (
            item.get("path"),
            item.get("expected_current_blob"),
            item.get("target_blob"),
        )
        for item in files
        if isinstance(item, dict)
    ]
    if observed != contract:
        raise ValueError(f"{label} handoff deployment contract mismatch")

    for item in files:
        status = item.get("status")
        if status not in {"ALREADY_TARGET", "READY_CREATE", "READY_UPDATE"}:
            raise ValueError(f"{label} handoff contains non-ready status")
        target_blob = item["target_blob"]
        if item.get("source_blob") != target_blob:
            raise ValueError(f"{label} handoff source blob mismatch: {item['path']}")
        if status == "ALREADY_TARGET":
            if item.get("current_blob") != target_blob:
                raise ValueError(f"{label} deployed current blob mismatch: {item['path']}")
        elif status == "READY_CREATE":
            if item.get("expected_current_blob") is not None or item.get("current_blob") is not None:
                raise ValueError(f"{label} create evidence mismatch: {item['path']}")
        else:
            if item.get("current_blob") != item.get("expected_current_blob"):
                raise ValueError(f"{label} update current blob mismatch: {item['path']}")
    return files


def _standard_operation(
    *,
    layer: str,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    status = item["status"]
    if status == "ALREADY_TARGET":
        return None
    if status == "READY_CREATE":
        operation = "CREATE_FILE"
        backup_required = False
    elif status == "READY_UPDATE":
        operation = "UPDATE_FILE"
        backup_required = True
    else:
        raise ValueError(f"unsupported standard deployment status: {status}")

    return {
        "layer": layer,
        "operation": operation,
        "path": item["path"],
        "expected_current_blob": item["expected_current_blob"],
        "target_blob": item["target_blob"],
        "source_blob": item["target_blob"],
        "backup_required": backup_required,
    }


def _preserved_operation(
    *,
    layer: str,
    item: dict[str, Any],
    private_bundle_sha256: str,
) -> dict[str, Any] | None:
    status = item["status"]
    if status == "ALREADY_TARGET":
        return None
    if status != "READY_UPDATE":
        raise ValueError(f"preserved target must be an update: {item['path']}")
    return {
        "layer": layer,
        "operation": "UPDATE_PRESERVED_FILE",
        "path": item["path"],
        "expected_current_blob": item["expected_current_blob"],
        "target_blob": item["target_blob"],
        "source_blob": item["target_blob"],
        "backup_required": True,
        "private_bundle_sha256": private_bundle_sha256,
    }


def _verify_source_contract(
    *,
    bundle: Path,
    phase2_contract: list[tuple[str, str | None, str]],
    runtime_contract: list[tuple[str, str | None, str]],
) -> None:
    expected: dict[str, str] = {}
    for path, _base, target in phase2_contract + runtime_contract:
        if path in expected and expected[path] != target:
            raise ValueError(f"source contract target collision: {path}")
        expected[path] = target
    expected[STATE_READER_PATH] = EXPECTED_STATE_READER_CANDIDATE_BLOB

    for path, target in expected.items():
        if _source_blob(bundle, path) != target:
            raise ValueError(f"private bundle source target mismatch: {path}")


def build_preserved_deployment_plan(
    *,
    source_tree: str | Path,
    handoff_snapshot: dict[str, Any],
    private_bundle_report: dict[str, Any],
) -> dict[str, Any]:
    source = Path(source_tree).resolve()
    if not source.is_dir():
        raise ValueError("reviewed source tree is missing")
    _verify_reviewed_source(source)

    handoff_module = _load_module(
        source / HANDOFF_TOOL,
        "manual_market_paper_preserved_plan_handoff",
    )
    bundle_module = _load_module(
        source / BUNDLE_TOOL,
        "manual_market_paper_preserved_plan_bundle",
    )
    handoff_module.validate_handoff_snapshot(handoff_snapshot)
    if handoff_snapshot.get("handoff_ready") is not True:
        raise ValueError("preserved handoff is not ready")
    if handoff_snapshot.get("requires_preservation_aware_plan") is not True:
        raise ValueError("handoff does not require preservation-aware planning")

    private_bundle_sha256 = handoff_snapshot["state"]["private_bundle_sha256"]
    bundle = _bundle_dir(
        bundle_module=bundle_module,
        bundle_report=private_bundle_report,
        expected_bundle_sha256=private_bundle_sha256,
    )

    phase2_manifest = _load_manifest(source, PHASE2_MANIFEST)
    runtime_manifest = _load_manifest(source, RUNTIME_MANIFEST)
    phase2_contract = _expected_phase2_contract(phase2_manifest)
    runtime_contract = _expected_runtime_contract(runtime_manifest)
    _verify_source_contract(
        bundle=bundle,
        phase2_contract=phase2_contract,
        runtime_contract=runtime_contract,
    )

    state = handoff_snapshot["state"]
    phase2_files = _validate_state_files(
        state_layer=state["phase2"],
        contract=phase2_contract,
        label="Phase-2 prerequisite",
    )
    runtime_files = _validate_state_files(
        state_layer=state["market_paper"],
        contract=runtime_contract,
        label="manual PAPER runtime",
    )

    state_reader = state["state_reader"]
    if not isinstance(state_reader, dict):
        raise ValueError("state-reader handoff evidence is missing")
    if state_reader.get("expected_current_blob") != EXPECTED_STATE_READER_CURRENT_BLOB:
        raise ValueError("state-reader expected-current blob mismatch")
    if state_reader.get("target_blob") != EXPECTED_STATE_READER_CANDIDATE_BLOB:
        raise ValueError("state-reader preserved target blob mismatch")
    if state_reader.get("source_blob") != EXPECTED_STATE_READER_CANDIDATE_BLOB:
        raise ValueError("state-reader source blob mismatch")
    state_status = state_reader.get("status")
    if state_status not in {"ALREADY_TARGET", "READY_UPDATE"}:
        raise ValueError("state-reader handoff status is not deploy-ready")
    if state_status == "ALREADY_TARGET":
        if state_reader.get("current_blob") != EXPECTED_STATE_READER_CANDIDATE_BLOB:
            raise ValueError("state-reader deployed current blob mismatch")
    else:
        if state_reader.get("current_blob") != EXPECTED_STATE_READER_CURRENT_BLOB:
            raise ValueError("state-reader update current blob mismatch")

    operations: list[dict[str, Any]] = []
    for item in phase2_files:
        if item["path"] == RESEARCH_STORE_PATH:
            operation = _preserved_operation(
                layer="PHASE2_SHARED_PREREQUISITES",
                item=item,
                private_bundle_sha256=private_bundle_sha256,
            )
        else:
            operation = _standard_operation(
                layer="PHASE2_SHARED_PREREQUISITES",
                item=item,
            )
        if operation is not None:
            operations.append(operation)

    state_item = {
        "path": STATE_READER_PATH,
        "expected_current_blob": EXPECTED_STATE_READER_CURRENT_BLOB,
        "target_blob": EXPECTED_STATE_READER_CANDIDATE_BLOB,
        "status": state_status,
    }
    state_operation = _preserved_operation(
        layer="STATE_READER",
        item=state_item,
        private_bundle_sha256=private_bundle_sha256,
    )
    if state_operation is not None:
        operations.append(state_operation)

    for item in runtime_files:
        operation = _standard_operation(
            layer="MARKET_PAPER_RUNTIME",
            item=item,
        )
        if operation is not None:
            operations.append(operation)

    deployment_needed = bool(operations)
    if handoff_snapshot.get("deployment_needed") is not deployment_needed:
        raise ValueError("handoff deployment-needed flag disagrees with plan")

    identity = {
        "format_version": FORMAT_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "handoff_artifact_type": handoff_snapshot["artifact_type"],
        "handoff_sha256": handoff_snapshot["handoff_sha256"],
        "handoff_state_sha256": handoff_snapshot["production_state_sha256"],
        "private_bundle_sha256": private_bundle_sha256,
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
        "candidate_content_included": False,
        "requires_fresh_handoff_verification": True,
        "requires_fresh_gate_verification": True,
        "requires_separate_mutation_authorization": True,
        "production_deployment_authorized": False,
        "mutation_authorized": False,
        "service_restart_authorized": False,
        "detector_cursor_movement_authorized": False,
        "paper_timer_enable_authorized": False,
        "live_capital_authorized": False,
    }
    plan = {
        **identity,
        "plan_sha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
    }
    validate_preserved_deployment_plan(plan)
    return plan


def _standard_contract_map() -> dict[str, tuple[str, str | None, str]]:
    for relative, expected_blob in (
        (PHASE2_MANIFEST, REVIEWED_SOURCE_BLOBS[PHASE2_MANIFEST]),
        (RUNTIME_MANIFEST, REVIEWED_SOURCE_BLOBS[RUNTIME_MANIFEST]),
    ):
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"preserved plan manifest is missing: {relative}")
        if _git_blob_sha(path) != expected_blob:
            raise ValueError(f"preserved plan manifest blob mismatch: {relative}")

    phase2 = _load_manifest(REPO_ROOT, PHASE2_MANIFEST)
    runtime = _load_manifest(REPO_ROOT, RUNTIME_MANIFEST)
    result: dict[str, tuple[str, str | None, str]] = {}

    for path in phase2["deployment_files"]:
        if path == RESEARCH_STORE_PATH:
            continue
        result[path] = (
            "PHASE2_SHARED_PREREQUISITES",
            phase2["deployment_base_file_blobs"][path],
            phase2["deployment_target_file_blobs"][path],
        )
    for path in runtime["deployment_files"]:
        if path in result:
            raise ValueError(f"preserved plan standard path collision: {path}")
        result[path] = (
            "MARKET_PAPER_RUNTIME",
            runtime["deployment_base_file_blobs"][path],
            runtime["deployment_target_file_blobs"][path],
        )
    return result


def validate_preserved_deployment_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("preserved deployment plan must be a JSON object")
    if set(plan) != set(PLAN_FIELDS) | {"plan_sha256"}:
        raise ValueError("preserved deployment plan fields do not match reviewed schema")
    if plan.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported preserved deployment plan format")
    if plan.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected preserved deployment plan artifact type")
    if plan.get("handoff_artifact_type") != EXPECTED_HANDOFF_ARTIFACT_TYPE:
        raise ValueError("preserved plan handoff schema is unexpected")

    for field in (
        "handoff_sha256",
        "handoff_state_sha256",
        "private_bundle_sha256",
        "plan_sha256",
    ):
        if not _is_hex_digest(plan.get(field), 64):
            raise ValueError(f"preserved deployment plan {field} is invalid")

    expected_source_blobs = {
        str(path): blob
        for path, blob in sorted(
            REVIEWED_SOURCE_BLOBS.items(),
            key=lambda item: str(item[0]),
        )
    }
    if plan.get("reviewed_source_blobs") != expected_source_blobs:
        raise ValueError("preserved deployment plan source lineage mismatch")
    if plan.get("required_layer_order") != list(LAYER_ORDER):
        raise ValueError("preserved deployment plan layer order is unexpected")

    operations = plan.get("operations")
    if not isinstance(operations, list):
        raise ValueError("preserved deployment plan operations must be a list")
    if plan.get("operation_count") != len(operations):
        raise ValueError("preserved deployment plan operation count mismatch")
    if plan.get("deployment_needed") is not bool(operations):
        raise ValueError("preserved deployment plan deployment-needed mismatch")
    if plan.get("plan_ready") is not True:
        raise ValueError("preserved deployment plan must be ready")
    if plan.get("candidate_content_included") is not False:
        raise ValueError("preserved deployment plan must not embed candidate content")

    for field in (
        "requires_fresh_handoff_verification",
        "requires_fresh_gate_verification",
        "requires_separate_mutation_authorization",
    ):
        if plan.get(field) is not True:
            raise ValueError(f"preserved deployment plan requires {field}=true")
    for field in (
        "production_deployment_authorized",
        "mutation_authorized",
        "service_restart_authorized",
        "detector_cursor_movement_authorized",
        "paper_timer_enable_authorized",
        "live_capital_authorized",
    ):
        if plan.get(field) is not False:
            raise ValueError(f"preserved deployment plan requires {field}=false")

    seen_paths: set[str] = set()
    previous_layer = -1
    preserved_expected = {
        RESEARCH_STORE_PATH: (
            "PHASE2_SHARED_PREREQUISITES",
            EXPECTED_RESEARCH_STORE_CURRENT_BLOB,
            EXPECTED_RESEARCH_STORE_CANDIDATE_BLOB,
        ),
        STATE_READER_PATH: (
            "STATE_READER",
            EXPECTED_STATE_READER_CURRENT_BLOB,
            EXPECTED_STATE_READER_CANDIDATE_BLOB,
        ),
    }
    standard_expected = _standard_contract_map()

    for item in operations:
        if not isinstance(item, dict):
            raise ValueError("preserved deployment plan operation is invalid")
        layer = item.get("layer")
        if layer not in LAYER_ORDER:
            raise ValueError("preserved deployment plan layer is invalid")
        layer_index = LAYER_ORDER.index(layer)
        if layer_index < previous_layer:
            raise ValueError("preserved deployment plan operations are out of order")
        previous_layer = layer_index

        path = item.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("preserved deployment plan path is invalid")
        if path in seen_paths:
            raise ValueError(f"preserved deployment plan path is duplicated: {path}")
        seen_paths.add(path)

        target_blob = item.get("target_blob")
        source_blob = item.get("source_blob")
        if not _is_hex_digest(target_blob, 40) or source_blob != target_blob:
            raise ValueError(f"preserved deployment plan source/target mismatch: {path}")

        operation = item.get("operation")
        if operation == "UPDATE_PRESERVED_FILE":
            expected = preserved_expected.get(path)
            if expected is None:
                raise ValueError("preserved operation targets unexpected path")
            expected_layer, expected_current, expected_target = expected
            if layer != expected_layer:
                raise ValueError("preserved operation layer mismatch")
            if item.get("expected_current_blob") != expected_current:
                raise ValueError("preserved operation expected-current mismatch")
            if target_blob != expected_target:
                raise ValueError("preserved operation target mismatch")
            if item.get("private_bundle_sha256") != plan["private_bundle_sha256"]:
                raise ValueError("preserved operation bundle digest mismatch")
            if item.get("backup_required") is not True:
                raise ValueError("preserved operation must require backup")
            if set(item) != {
                "layer",
                "operation",
                "path",
                "expected_current_blob",
                "target_blob",
                "source_blob",
                "backup_required",
                "private_bundle_sha256",
            }:
                raise ValueError("preserved operation schema mismatch")
            continue

        if operation not in {"CREATE_FILE", "UPDATE_FILE"}:
            raise ValueError(f"standard operation type is invalid: {path}")
        if path in preserved_expected:
            raise ValueError("preserved path cannot use standard operation")
        expected_standard = standard_expected.get(path)
        if expected_standard is None:
            raise ValueError("standard operation targets unexpected path")
        expected_layer, expected_current, expected_target = expected_standard
        if layer != expected_layer:
            raise ValueError("standard operation layer mismatch")
        if item.get("expected_current_blob") != expected_current:
            raise ValueError("standard operation expected-current mismatch")
        if target_blob != expected_target:
            raise ValueError("standard operation target mismatch")
        expected_operation = (
            "CREATE_FILE"
            if expected_current is None
            else "UPDATE_FILE"
        )
        if operation != expected_operation:
            raise ValueError("standard operation type disagrees with manifest base")
        if set(item) != {
            "layer",
            "operation",
            "path",
            "expected_current_blob",
            "target_blob",
            "source_blob",
            "backup_required",
        }:
            raise ValueError("standard operation schema mismatch")
        if operation == "CREATE_FILE":
            if item.get("expected_current_blob") is not None:
                raise ValueError("create operation requires absent target")
            if item.get("backup_required") is not False:
                raise ValueError("create operation must not require backup")
        else:
            if not _is_hex_digest(item.get("expected_current_blob"), 40):
                raise ValueError("update expected-current blob is invalid")
            if item.get("backup_required") is not True:
                raise ValueError("update operation must require backup")

    identity = {field: plan[field] for field in PLAN_FIELDS}
    expected_digest = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    if plan["plan_sha256"] != expected_digest:
        raise ValueError("preserved deployment plan digest mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic, non-mutating deployment plan from a sealed "
            "preserved-readiness handoff and private source bundle. The only "
            "new operation type is UPDATE_PRESERVED_FILE for the two validated "
            "private candidates. This tool never copies files into production, "
            "changes services/timers/cursors, or authorizes mutation."
        )
    )
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--private-bundle-report", required=True)
    args = parser.parse_args()

    plan = build_preserved_deployment_plan(
        source_tree=args.source_tree,
        handoff_snapshot=_load_json(Path(args.handoff)),
        private_bundle_report=_load_json(Path(args.private_bundle_report)),
    )
    print(json.dumps(plan, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
