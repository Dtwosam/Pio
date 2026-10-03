from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

DESCRIPTOR_READERS = (
    (
        "deploy/tools/check_phase2_mutation_post_audit_catalog.py",
        "_read_snapshot_bytes",
    ),
    (
        "deploy/tools/check_phase2_mutation_post_audit_catalog_handoff_snapshot.py",
        "_read_snapshot_bytes",
    ),
    (
        "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive_handoff_snapshot.py",
        "_read_snapshot_bytes",
    ),
    (
        "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive.py",
        "_read_archive_snapshot",
    ),
    (
        "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle.py",
        "_read_private_snapshot",
    ),
    (
        "deploy/tools/check_phase2_isolated_mutation_execution_receipt.py",
        "_private_json_snapshot",
    ),
)

ORCHESTRATOR_CONTRACTS = (
    (
        "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle.py",
        "verify_phase2_portable_handoff_bundle",
        (
            "_read_private_snapshot",
            "captured_handoff_sha",
            "captured_catalog_sha",
            "snapshot_sha256",
            "_assert_snapshot_path_stable",
        ),
    ),
    (
        "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive_handoff.py",
        "inspect_phase2_portable_archive_current_handoff",
        (
            "_capture_archive_snapshot",
            "captured_archive_sha256",
            "captured_archive_size",
            "archive.archive_sha256",
            "archive.archive_size",
            "_assert_archive_path_stable",
        ),
    ),
    (
        "deploy/tools/check_phase2_mutation_post_audit_handoff_bundle_archive_handoff_freshness.py",
        "freshly_reverify_phase2_portable_archive_handoff",
        (
            "_capture_snapshot_identity",
            "_capture_archive_identity",
            "captured_snapshot_sha256",
            "captured_archive_sha256",
            "captured_archive_size",
            "snapshot.snapshot_sha256",
            "archive.archive_sha256",
            "archive.archive_size",
            "_assert_snapshot_path_stable",
            "_assert_archive_path_stable",
        ),
    ),
    (
        "deploy/tools/check_phase2_mutation_post_audit_catalog_handoff.py",
        "inspect_phase2_post_audit_catalog_handoff",
        (
            "_capture_catalog_snapshot",
            "captured_snapshot_sha256",
            "snapshot.snapshot_sha256",
            "_assert_snapshot_path_stable",
        ),
    ),
    (
        "deploy/tools/check_phase2_mutation_post_audit_catalog_freshness.py",
        "freshly_reverify_phase2_post_audit_catalog",
        (
            "_capture_snapshot_identity",
            "captured_snapshot_sha256",
            "snapshot.snapshot_sha256",
            "_assert_snapshot_path_stable",
        ),
    ),
)


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _function(relative: str, name: str) -> tuple[ast.FunctionDef, str]:
    source = _source(relative)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return node, segment
    raise AssertionError(f"{relative} is missing function {name}")


def _has_os_call(node: ast.AST, attr: str) -> bool:
    for value in ast.walk(node):
        if not isinstance(value, ast.Call):
            continue
        if not isinstance(value.func, ast.Attribute):
            continue
        if value.func.attr != attr:
            continue
        root = value.func.value
        if isinstance(root, ast.Name) and root.id == "os":
            return True
    return False


@pytest.mark.parametrize(("relative", "function_name"), DESCRIPTOR_READERS)
def test_phase2_private_snapshot_readers_use_descriptor_bound_io(
    relative,
    function_name,
):
    node, source = _function(relative, function_name)

    assert _has_os_call(node, "open")
    assert _has_os_call(node, "fstat")
    assert "O_NOFOLLOW" in source
    assert "st_size" in source
    assert ".read_text(" not in source
    assert ".read_bytes(" not in source


@pytest.mark.parametrize(
    ("relative", "function_name", "required_tokens"),
    ORCHESTRATOR_CONTRACTS,
)
def test_phase2_verification_orchestrators_bind_children_to_captured_bytes(
    relative,
    function_name,
    required_tokens,
):
    _node, source = _function(relative, function_name)

    for token in required_tokens:
        assert token in source, (
            f"{relative}:{function_name} dropped verification invariant {token}"
        )
    assert ".read_text(" not in source
    assert ".read_bytes(" not in source


def test_portable_archive_parses_only_the_captured_archive_bytes():
    relative = (
        "deploy/tools/"
        "check_phase2_mutation_post_audit_handoff_bundle_archive.py"
    )
    _node, source = _function(
        relative,
        "verify_phase2_portable_bundle_archive",
    )

    assert "_read_archive_snapshot" in source
    assert "hashlib.sha256(archive_bytes)" in source
    assert "io.BytesIO(archive_bytes)" in source
    assert "_assert_archive_path_stable" in source
    assert ".read_text(" not in source
    assert ".read_bytes(" not in source
