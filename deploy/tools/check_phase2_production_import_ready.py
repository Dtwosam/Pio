#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class Phase2ProductionImportGate:
    issue: int
    snapshot_directory: str
    repo_root: str
    required_sources: int
    required_test_files: int
    required_regression_tests: int
    snapshot_imports_verified: int
    test_files_verified: int
    test_functions_verified: int
    runbook_verified: bool
    ready: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or is a symlink: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _safe_repo_path(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe repo-relative path: {value}")
    resolved = (root / relative).resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError(f"repo path escapes root: {value}")
    return resolved


def _test_functions(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ValueError(f"cannot parse regression test file: {path}") from exc
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def evaluate_phase2_production_import_gate(
    *,
    repo_root: str | Path,
    snapshot_directory: str | Path,
    contract_path: str | Path | None = None,
) -> Phase2ProductionImportGate:
    root = Path(repo_root).resolve()
    snapshot = Path(snapshot_directory).resolve()
    if not root.is_dir():
        raise ValueError(f"repo root does not exist: {root}")
    if not snapshot.is_dir():
        raise ValueError(f"snapshot directory does not exist: {snapshot}")

    contract_file = (
        Path(contract_path).resolve()
        if contract_path is not None
        else root / "deploy/manifests/phase2-production-import-contract.json"
    )
    contract = _load_json(contract_file, label="import contract")
    if int(contract.get("format_version", -1)) != 1:
        raise ValueError("unsupported import contract format")
    if int(contract.get("issue", -1)) != 6:
        raise ValueError("import contract must target Issue #6")
    if contract.get("production_import_ready_requires_snapshot_match") is not True:
        raise ValueError("import contract must require exact snapshot match")

    source_contracts = contract.get("required_sources")
    if not isinstance(source_contracts, list) or not source_contracts:
        raise ValueError("import contract contains no required sources")

    manifest = _load_json(snapshot / "manifest.json", label="snapshot manifest")
    if int(manifest.get("format_version", -1)) != 1:
        raise ValueError("unsupported production source snapshot format")
    manifest_sources = manifest.get("sources")
    if not isinstance(manifest_sources, list) or not manifest_sources:
        raise ValueError("snapshot manifest contains no sources")

    by_label: dict[str, dict[str, Any]] = {}
    for row in manifest_sources:
        if not isinstance(row, dict):
            raise ValueError("snapshot source entry must be an object")
        label = str(row.get("label", ""))
        if not label or label in by_label:
            raise ValueError(f"invalid or duplicate snapshot label: {label}")
        expected_hash = str(row.get("sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(f"invalid snapshot SHA-256 for {label}")
        captured_name = str(row.get("captured_path", ""))
        captured_rel = Path(captured_name)
        if (
            not captured_name
            or captured_rel.is_absolute()
            or len(captured_rel.parts) != 1
            or ".." in captured_rel.parts
        ):
            raise ValueError(f"unsafe captured path for {label}")
        captured = snapshot / captured_rel
        if captured.is_symlink() or not captured.is_file():
            raise ValueError(f"captured source missing for {label}")
        if _sha256(captured) != expected_hash:
            raise ValueError(f"captured source hash mismatch for {label}")
        by_label[label] = row

    verified_imports = 0
    seen_labels: set[str] = set()
    seen_repo_paths: set[str] = set()
    for row in source_contracts:
        if not isinstance(row, dict):
            raise ValueError("required source contract must be an object")
        label = str(row.get("label", ""))
        repo_path = str(row.get("repo_path", ""))
        if not label or label in seen_labels:
            raise ValueError(f"invalid or duplicate required source label: {label}")
        if not repo_path or repo_path in seen_repo_paths:
            raise ValueError(f"invalid or duplicate repo path: {repo_path}")
        seen_labels.add(label)
        seen_repo_paths.add(repo_path)
        if label not in by_label:
            raise ValueError(f"required source missing from snapshot: {label}")

        imported = _safe_repo_path(root, repo_path)
        if imported.is_symlink() or not imported.is_file():
            raise ValueError(f"required repo import missing: {repo_path}")
        expected_hash = str(by_label[label]["sha256"])
        if _sha256(imported) != expected_hash:
            raise ValueError(
                f"repo import does not match production snapshot: {repo_path}"
            )
        verified_imports += 1

    test_files = contract.get("required_test_files")
    if not isinstance(test_files, list) or not test_files:
        raise ValueError("import contract contains no required test files")
    test_functions: set[str] = set()
    verified_test_files = 0
    for value in test_files:
        path = _safe_repo_path(root, str(value))
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"required regression test file missing: {value}")
        test_functions.update(_test_functions(path))
        verified_test_files += 1

    required_tests = contract.get("required_regression_tests")
    if not isinstance(required_tests, list) or not required_tests:
        raise ValueError("import contract contains no required regression tests")
    required_test_names = [str(value) for value in required_tests]
    if len(required_test_names) != len(set(required_test_names)):
        raise ValueError("required regression test names must be unique")
    missing_tests = sorted(set(required_test_names) - test_functions)
    if missing_tests:
        raise ValueError(
            "required regression tests missing: " + ", ".join(missing_tests)
        )

    runbook_value = str(contract.get("required_runbook", ""))
    if not runbook_value:
        raise ValueError("required_runbook is missing from import contract")
    runbook = _safe_repo_path(root, runbook_value)
    if runbook.is_symlink() or not runbook.is_file():
        raise ValueError(f"required deployment runbook missing: {runbook_value}")
    if not runbook.read_text(encoding="utf-8").strip():
        raise ValueError("deployment runbook cannot be empty")

    return Phase2ProductionImportGate(
        issue=6,
        snapshot_directory=str(snapshot),
        repo_root=str(root),
        required_sources=len(source_contracts),
        required_test_files=len(test_files),
        required_regression_tests=len(required_test_names),
        snapshot_imports_verified=verified_imports,
        test_files_verified=verified_test_files,
        test_functions_verified=len(required_test_names),
        runbook_verified=True,
        ready=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed Issue #6 gate: verify exact production detector/"
            "watcher imports, required regression tests and deployment runbook."
        )
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--contract")
    args = parser.parse_args()

    result = evaluate_phase2_production_import_gate(
        repo_root=args.repo_root,
        snapshot_directory=args.snapshot,
        contract_path=args.contract,
    )
    print(json.dumps(result.to_record(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
