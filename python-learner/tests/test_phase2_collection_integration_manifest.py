import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy" / "manifests" / "phase2-collection-integration.json"
STATE_READER = ROOT / "rust-executor" / "src" / "state_reader.rs"


def git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def test_phase2_collection_manifest_is_fail_closed_and_matches_state_reader():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["format_version"] == 1
    assert payload["production_deployment_authorized"] is False
    assert payload["detector_cursor_movement_authorized"] is False
    assert payload["service_restart_authorized"] is False
    assert payload["state_reader_target_blob"] == git_blob_sha(STATE_READER)

    components = payload["components"]
    assert components
    prs = [item["pr"] for item in components]
    assert len(prs) == len(set(prs))
    assert {7, 8, 12, 17, 19, 20, 22, 23, 25, 26, 28, 29, 30, 31} <= set(prs)
    assert all(
        re.fullmatch(r"[0-9a-f]{40}", str(item["head"]))
        for item in components
    )
