import ast
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WATCHER = ROOT / "scripts" / "phase2-prestate-watch.py"
SOURCE = WATCHER.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(WATCHER))


def _functions(*names):
    wanted = set(names)
    return [
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef)
        and node.name in wanted
    ]


def test_watcher_promotes_verified_prestate():
    nodes = _functions("trim_to_active_bin", "persist_cache")

    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    captured = {}

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return SimpleNamespace(rowcount=1)

    namespace = {
        "json": json,
        "now": lambda: "2026-09-26T23:00:00+00:00",
        "connect_cache": lambda: FakeConnection(),
    }

    exec(compile(module, str(WATCHER), "exec"), namespace)

    snapshot = {
        "pool_address": "pool-1",
        "capture_slot_start": 100,
        "capture_slot_end": 102,
        "active_bin_id": 7,
        "bin_arrays": [
            {
                "address": "array-1",
                "index": 0,
                "lower_bin_id": 0,
                "upper_bin_id": 10,
                "bins": [
                    {"bin_id": 6, "amount_x": "1"},
                    {"bin_id": 7, "amount_x": "2"},
                    {"bin_id": 8, "amount_x": "3"},
                ],
            }
        ],
    }

    stored = namespace["persist_cache"](snapshot)

    assert stored is True
    assert "INSERT OR IGNORE INTO prestate_snapshots" in captured["sql"]

    params = captured["params"]

    assert params[1] == "pool-1"
    assert params[2] == 100
    assert params[3] == 102
    assert params[4] == 7
    assert params[5] == "array-1"

    persisted = json.loads(params[6])

    # The watcher preserves the capture-slot provenance and retains only the
    # exact active bin needed by later prestate verification.
    assert persisted["capture_slot_start"] == 100
    assert persisted["capture_slot_end"] == 102
    assert persisted["active_bin_id"] == 7
    assert len(persisted["bin_arrays"]) == 1
    assert persisted["bin_arrays"][0]["address"] == "array-1"
    assert persisted["bin_arrays"][0]["bins"] == [
        {"bin_id": 7, "amount_x": "2"}
    ]
