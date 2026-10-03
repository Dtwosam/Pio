from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

CREATE_ONLY_JSON_PUBLISHERS = (
    "deploy/tools/save_phase2_isolated_mutation_post_audit.py",
    "deploy/tools/save_phase2_mutation_post_audit_catalog.py",
    "deploy/tools/save_phase2_mutation_post_audit_catalog_handoff.py",
    "deploy/tools/save_phase2_mutation_post_audit_handoff_bundle_archive_handoff.py",
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


def _is_attr_call(node: ast.AST, root: str, attr: str) -> bool:
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == root
    )


def _has_attr_call(node: ast.AST, root: str, attr: str) -> bool:
    return any(_is_attr_call(value, root, attr) for value in ast.walk(node))


def _has_method_call(node: ast.AST, attr: str, receiver: str | None = None) -> bool:
    for value in ast.walk(node):
        if not isinstance(value, ast.Call):
            continue
        if not isinstance(value.func, ast.Attribute):
            continue
        if value.func.attr != attr:
            continue
        if receiver is None:
            return True
        if isinstance(value.func.value, ast.Name) and value.func.value.id == receiver:
            return True
    return False


def _hashes_name(node: ast.AST, name: str) -> bool:
    for value in ast.walk(node):
        if not _is_attr_call(value, "hashlib", "sha256"):
            continue
        if (
            len(value.args) == 1
            and isinstance(value.args[0], ast.Name)
            and value.args[0].id == name
        ):
            return True
    return False


@pytest.mark.parametrize("relative", CREATE_ONLY_JSON_PUBLISHERS)
def test_create_only_json_publishers_share_no_clobber_exact_payload_contract(
    relative,
):
    node, source = _function(relative, "_atomic_write_new")

    assert _has_attr_call(node, "os", "link")
    assert not _has_attr_call(node, "os", "replace")
    assert not _has_method_call(node, "read_bytes")
    assert _hashes_name(node, "encoded")
    assert "st_size != len(encoded)" in source
    assert "path changed after publish" in source
    assert "stat.S_IMODE(current.st_mode) != 0o600" in source
    assert "os.fsync(directory_fd)" in source


def test_mutation_preview_keeps_overwrite_explicit_and_default_no_clobber():
    relative = "deploy/tools/save_phase2_isolated_mutation_preview.py"
    node, source = _function(relative, "_atomic_write")

    replace_branch = next(
        (
            value
            for value in ast.walk(node)
            if isinstance(value, ast.If)
            and isinstance(value.test, ast.Name)
            and value.test.id == "replace"
        ),
        None,
    )
    assert replace_branch is not None
    assert any(
        _is_attr_call(value, "os", "replace")
        for statement in replace_branch.body
        for value in ast.walk(statement)
    )
    assert any(
        _is_attr_call(value, "os", "link")
        for statement in replace_branch.orelse
        for value in ast.walk(statement)
    )
    assert not _has_method_call(node, "read_bytes")
    assert "st_size != len(payload)" in source
    assert "path changed after publish" in source

    save_node, _ = _function(relative, "save_mutation_preview")
    assert not _has_method_call(save_node, "read_bytes")
    assert _hashes_name(save_node, "payload")


def test_portable_archive_hashes_fsynced_temp_bytes_and_never_clobbers():
    relative = "deploy/tools/archive_phase2_mutation_post_audit_handoff_bundle.py"
    source = _source(relative)
    tree = ast.parse(source)

    publisher = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and _has_attr_call(node, "os", "link")
        ),
        None,
    )
    assert publisher is not None
    segment = ast.get_source_segment(source, publisher)
    assert segment is not None

    assert not _has_attr_call(publisher, "os", "replace")
    assert _has_method_call(publisher, "read_bytes", receiver="temp")
    assert not _has_method_call(publisher, "read_bytes", receiver="output")
    assert _hashes_name(publisher, "raw")
    assert "st_size != len(raw)" in segment
    assert "path changed after publish" in segment
    assert "os.fsync(directory_fd)" in segment


def test_portable_bundle_directory_publication_requires_rename_noreplace():
    relative = "deploy/tools/build_phase2_mutation_post_audit_handoff_bundle.py"
    node, source = _function(relative, "_publish_directory_noreplace")

    assert not _has_attr_call(node, "os", "replace")
    assert "renameat2" in source
    assert "rename_noreplace = 1" in source
    assert "publication is unavailable" in source
    assert "path changed after publish" in source
    assert "os.fsync(directory_fd)" in source



def test_portable_bundle_tree_is_fsynced_before_atomic_publication():
    relative = "deploy/tools/build_phase2_mutation_post_audit_handoff_bundle.py"

    file_node, file_source = _function(relative, "_fsync_regular_file")
    dir_node, dir_source = _function(relative, "_fsync_private_directory")
    build_node, build_source = _function(
        relative,
        "build_phase2_portable_handoff_bundle",
    )

    assert _has_attr_call(file_node, "os", "fsync")
    assert "O_NOFOLLOW" in file_source
    assert "stat.S_IMODE(before.st_mode) != 0o600" in file_source

    assert _has_attr_call(dir_node, "os", "fsync")
    assert "O_NOFOLLOW" in dir_source
    assert "stat.S_IMODE(before.st_mode) != 0o700" in dir_source

    assert "_fsync_bundle_tree(temp)" in build_source
    assert "_publish_directory_noreplace(temp, output)" in build_source
    assert build_source.index("_fsync_bundle_tree(temp)") < build_source.index(
        "_publish_directory_noreplace(temp, output)"
    )
