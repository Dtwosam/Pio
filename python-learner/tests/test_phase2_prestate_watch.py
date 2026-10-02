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



def test_watcher_rate_limit_backoff_escalates_without_capping_healthy_calls():
    nodes = _functions("rate_limit_backoff_seconds")
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace = {
        "RATE_LIMIT_BACKOFF_SECONDS": (
            5.0,
            10.0,
            20.0,
            40.0,
            80.0,
            160.0,
            300.0,
            600.0,
        ),
    }
    exec(compile(module, str(WATCHER), "exec"), namespace)

    backoff = namespace["rate_limit_backoff_seconds"]
    assert backoff(0) == 0.0
    assert backoff(1) == 5.0
    assert backoff(4) == 40.0
    assert backoff(8) == 600.0
    assert backoff(100) == 600.0


def test_watcher_only_delays_success_when_capture_would_be_duplicate():
    nodes = _functions("post_capture_delay")
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace = {
        "DUPLICATE_SLOT_RECHECK_SECONDS": 0.25,
    }
    exec(compile(module, str(WATCHER), "exec"), namespace)

    delay = namespace["post_capture_delay"]
    assert delay(stored=True) == 0.0
    assert delay(stored=False) == 0.25


def test_watcher_uses_secret_safe_rate_limit_category():
    import subprocess
    import urllib.error

    nodes = _functions("is_rate_limited_error", "error_category")
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace = {
        "subprocess": subprocess,
        "urllib": __import__("urllib"),
    }
    namespace["urllib"].error = urllib.error
    exec(compile(module, str(WATCHER), "exec"), namespace)

    category = namespace["error_category"](
        RuntimeError(
            "HTTP 429 Too Many Requests for "
            "https://rpc.invalid/?api-key=secret"
        )
    )
    assert category == "RPC_RATE_LIMITED"


def test_watcher_does_not_log_raw_rpc_exception_text():
    worker_nodes = _functions("continuous_worker", "dq9_worker")
    worker_source = "\n".join(ast.unparse(node) for node in worker_nodes)

    assert "RPC_BACKOFF" in worker_source
    assert "category=error_category(exc)" in worker_source
    assert 'error=f"{type(exc).__name__}: {exc}"' not in worker_source
