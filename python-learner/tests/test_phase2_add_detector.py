import ast
import io
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
DETECTOR = ROOT / "scripts" / "phase2-add-detector.py"
SOURCE = DETECTOR.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(DETECTOR))


def _function(name):
    return next(
        node
        for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def _load_function(name):
    node = _function(name)
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)

    namespace = {
        "json": json,
        "time": time,
        "urllib": __import__("urllib"),
        "RPC": "https://rpc.invalid",
        "RUST": "/executor",
        "ROOT": "/opt/pio",
        "DEFAULT_POOLS": {
            "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ": 120.0,
            "DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf": 300.0,
        },
        "os": os,
        "subprocess": subprocess,
        "RpcRateLimited": RuntimeError,
        "log": lambda *args, **kwargs: None,
        "save_state": lambda state: None,
    }
    # import urllib.request/error populates these on the urllib package.
    namespace["urllib"].request = urllib.request
    namespace["urllib"].error = urllib.error
    if name != "is_rate_limited_text":
        namespace["is_rate_limited_text"] = _load_function(
            "is_rate_limited_text"
        )

    exec(compile(module, str(DETECTOR), "exec"), namespace)
    return namespace[name]


class _Response(io.StringIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def _cursor_assignment(node):
    if not isinstance(node, ast.Assign):
        return False

    for target in node.targets:
        # state["cursors"][pool] = ...
        if not isinstance(target, ast.Subscript):
            continue
        inner = target.value
        if not isinstance(inner, ast.Subscript):
            continue
        if not isinstance(inner.value, ast.Name) or inner.value.id != "state":
            continue
        if not isinstance(inner.slice, ast.Constant):
            continue
        if inner.slice.value != "cursors":
            continue
        if isinstance(target.slice, ast.Name) and target.slice.id == "pool":
            return True

    return False


def test_detector_has_resolved_run_wrapper():
    node = _function("run")

    calls = [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "subprocess"
        and call.func.attr == "run"
    ]

    assert len(calls) == 1
    call = calls[0]

    keywords = {
        item.arg: ast.unparse(item.value)
        for item in call.keywords
        if item.arg is not None
    }

    assert keywords["input"] == "input_text"
    assert keywords["timeout"] == "timeout"
    assert keywords["text"] == "True"
    assert keywords["capture_output"] == "True"
    assert keywords["check"] == "False"


def test_detector_cursor_advances_only_after_completed_signature():
    advance = _load_function("advance_cursor")
    state = {"cursors": {"pool": "old"}}

    advance(state, "pool", "completed")

    assert state["cursors"]["pool"] == "completed"
    assert 'retained_cursor=state["cursors"].get(pool)' in SOURCE

    # The batch-level all-or-nothing cursor assignment is intentionally gone.
    # A failed signature is retried, while the completed prefix is retained.
    assert 'state["cursors"][pool] = rows[0]["signature"]' not in SOURCE

    inspect_handlers = [
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "Exception"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "error_category"
            for child in ast.walk(node)
        )
    ]
    assert any(
        any(isinstance(child, ast.Break) for child in ast.walk(handler))
        for handler in inspect_handlers
    )


def test_detector_pagination_reaches_retained_cursor_without_skipping(monkeypatch):
    rpc_signatures = _load_function("rpc_signatures")

    pages = [
        [
            {"signature": "sig-4"},
            {"signature": "sig-3"},
        ],
        [
            {"signature": "sig-2"},
        ],
    ]
    configs = []

    def fake_urlopen(request, timeout):
        assert timeout == 30
        body = json.loads(request.data.decode())
        assert body["method"] == "getSignaturesForAddress"
        configs.append(dict(body["params"][1]))
        return _Response(json.dumps({"result": pages.pop(0)}))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda _: None)

    rows = rpc_signatures(
        "pool-address",
        until="retained-cursor",
        limit=2,
    )

    assert [row["signature"] for row in rows] == [
        "sig-4",
        "sig-3",
        "sig-2",
    ]

    assert configs == [
        {
            "limit": 2,
            "commitment": "confirmed",
            "until": "retained-cursor",
        },
        {
            "limit": 2,
            "commitment": "confirmed",
            "until": "retained-cursor",
            "before": "sig-3",
        },
    ]


def test_detector_retries_rpc_failures_with_backoff(monkeypatch):
    rpc_signatures = _load_function("rpc_signatures")

    calls = 0
    sleeps = []

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1

        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "7"},
                None,
            )

        return _Response(
            json.dumps(
                {
                    "result": [
                        {"signature": "after-backoff"},
                    ]
                }
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda value: sleeps.append(value))

    rows = rpc_signatures(
        "pool-address",
        until="retained-cursor",
        limit=1000,
    )

    assert calls == 2
    assert sleeps == [7.0]
    assert rows == [{"signature": "after-backoff"}]


def test_detector_supports_1000_signature_pages(monkeypatch):
    rpc_signatures = _load_function("rpc_signatures")

    first_page = [
        {"signature": f"sig-{index}"}
        for index in range(1000)
    ]
    pages = [first_page, []]
    configs = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode())
        configs.append(dict(body["params"][1]))
        return _Response(json.dumps({"result": pages.pop(0)}))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda _: None)

    rows = rpc_signatures(
        "pool-address",
        until="retained-cursor",
        limit=1000,
    )

    assert len(rows) == 1000
    assert configs[0]["limit"] == 1000
    assert configs[0]["until"] == "retained-cursor"

    assert configs[1]["limit"] == 1000
    assert configs[1]["until"] == "retained-cursor"
    assert configs[1]["before"] == "sig-999"



def test_detector_stops_after_rate_limit_retries_are_exhausted(monkeypatch):
    rpc_signatures = _load_function("rpc_signatures")
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            {"Retry-After": "0"},
            None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda _: None)

    import pytest

    with pytest.raises(RuntimeError, match="RPC_RATE_LIMITED") as excinfo:
        rpc_signatures(
            "pool-address",
            until="retained-cursor",
            limit=1000,
        )

    assert calls == 5
    assert "rpc.invalid" not in str(excinfo.value)


def test_detector_transaction_inspection_stops_after_rate_limit_retries(
    monkeypatch,
):
    inspect_transaction = _load_function("inspect_transaction")
    calls = 0
    secret = "https://rpc.invalid/?api-key=secret"

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            args[0],
            1,
            "",
            f"HTTP 429 Too Many Requests at {secret}",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(time, "sleep", lambda _: None)

    import pytest

    with pytest.raises(RuntimeError) as excinfo:
        inspect_transaction("sig")

    assert calls == 5
    assert str(excinfo.value) == "RPC_RATE_LIMITED"
    assert secret not in str(excinfo.value)


def test_detector_rate_limit_paths_are_secret_safe_and_abort_batch():
    assert 'category="RPC_RATE_LIMITED"' in SOURCE
    assert "rate_limited_batch = True" in SOURCE
    assert '"PENDING_RETRY_PAUSED"' in SOURCE
    assert 'error=f"{type(exc).__name__}: {exc}"' not in SOURCE
    assert '"inspect-transaction-events failed: "' not in SOURCE

    handlers = [
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "RpcRateLimited"
    ]
    assert handlers
    assert any(
        any(isinstance(child, ast.Break) for child in ast.walk(handler))
        for handler in handlers
    )



def test_detector_pool_config_preserves_legacy_defaults():
    parse = _load_function("parse_detector_pools")

    pools = parse("")

    assert pools == {
        "54Vp27uLaw4wNLo5n7r4fcC6zLamoQc28xBARjss4EUJ": 120.0,
        "DQ9weJhfiU4iL5LUoeshDrm5KxDHCMiSbnnKJz7buMcf": 300.0,
    }


def test_detector_pool_config_accepts_explicit_pool_cadences():
    parse = _load_function("parse_detector_pools")

    pools = parse("pool-a:15,pool-b:90.5")

    assert pools == {"pool-a": 15.0, "pool-b": 90.5}


def test_detector_pool_config_rejects_invalid_or_duplicate_entries():
    parse = _load_function("parse_detector_pools")
    import pytest

    with pytest.raises(ValueError, match="ADDRESS:SECONDS"):
        parse("pool-without-interval")
    with pytest.raises(ValueError, match="positive"):
        parse("pool-a:0")
    with pytest.raises(ValueError, match="duplicate"):
        parse("pool-a:10,pool-a:20")



def _load_candidate_processor(**overrides):
    nodes = [
        _function("pending_record"),
        _function("process_candidate"),
    ]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace = {
        "now": lambda: "2026-10-02T16:00:00+00:00",
        "promote_prestate": lambda *args, **kwargs: {"ok": True},
        "collect_history": lambda position: None,
        "composition_prestate": lambda position: {"candidates": []},
        "verify_prestate": lambda candidate: {"eligible": True, "reasons": []},
        "reconcile": lambda position: {
            "eligible_samples": 1,
            "exact_samples": 1,
            "mismatched_samples": 0,
        },
        "phase2_evidence": lambda: None,
        "log": lambda *args, **kwargs: None,
        "RpcRateLimited": RuntimeError,
        "error_category": lambda exc: type(exc).__name__.upper(),
    }
    namespace.update(overrides)
    exec(compile(module, str(DETECTOR), "exec"), namespace)
    return namespace["process_candidate"]


def test_detector_keeps_add_pending_until_cached_prestate_exists():
    calls = []

    def promote(pool, *, target_slot, active_bin_id):
        calls.append(("promote", pool, target_slot, active_bin_id))
        return None

    def history(_position):
        calls.append(("history",))
        raise AssertionError("history must not run without cached prestate")

    process_candidate = _load_candidate_processor(
        promote_prestate=promote,
        collect_history=history,
    )
    state = {"pending": {}, "processed": []}

    process_candidate(
        state,
        "pool",
        "sig",
        {
            "position": "position",
            "target_slot": 123,
            "active_bin_id": 7,
        },
    )

    assert calls == [("promote", "pool", 123, 7)]
    assert state["processed"] == []
    assert state["pending"]["sig:position"] == {
        "pool": "pool",
        "signature": "sig",
        "position": "position",
        "reason": "NO_CACHED_PRESTATE",
        "last_attempt": "2026-10-02T16:00:00+00:00",
        "target_slot": 123,
        "active_bin_id": 7,
    }


def test_detector_pending_metadata_survives_history_lag_after_prestate():
    process_candidate = _load_candidate_processor(
        promote_prestate=lambda *args, **kwargs: {"cached": True},
        composition_prestate=lambda position: {"candidates": []},
    )
    state = {"pending": {}, "processed": []}

    process_candidate(
        state,
        "pool",
        "sig",
        {
            "position": "position",
            "target_slot": 456,
            "active_bin_id": 11,
        },
    )

    pending = state["pending"]["sig:position"]
    assert pending["reason"] == "Meteora history has not exposed target add yet"
    assert pending["target_slot"] == 456
    assert pending["active_bin_id"] == 11
    assert state["processed"] == []


def test_detector_pending_retry_passes_prestate_coordinates_back_to_processor():
    assert '"target_slot": item.get("target_slot")' in SOURCE
    assert '"active_bin_id": item.get("active_bin_id")' in SOURCE
    assert '"PENDING_PRESTATE"' in SOURCE



def test_detector_success_path_has_no_fixed_rpc_sleep(monkeypatch):
    rpc_signatures = _load_function("rpc_signatures")
    sleeps = []
    pages = [
        [
            {"signature": "sig-2"},
            {"signature": "sig-1"},
        ],
        [],
    ]

    def fake_urlopen(request, timeout):
        return _Response(json.dumps({"result": pages.pop(0)}))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda value: sleeps.append(value))

    rows = rpc_signatures(
        "pool-address",
        until="cursor",
        limit=2,
    )

    assert [row["signature"] for row in rows] == ["sig-2", "sig-1"]
    assert sleeps == []

    inspect_transaction = _load_function("inspect_transaction")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"signature": command[-1]}),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    inspect_transaction("sig-a")
    inspect_transaction("sig-b")

    assert sleeps == []


def test_detector_rate_limit_cooldown_escalates_only_after_rejection():
    cooldown = _load_function("rate_limit_cooldown_seconds")

    assert cooldown(0) == 0.0
    assert cooldown(1) == 60.0
    assert cooldown(2) == 120.0
    assert cooldown(3) == 300.0
    assert cooldown(4) == 600.0
    assert cooldown(5) == 900.0
    assert cooldown(100) == 900.0


def test_detector_has_global_rejection_cooldown_and_baseline_recovery():
    assert '"RPC_COOLDOWN"' in SOURCE
    assert "mono < rpc_cooldown_until" in SOURCE
    assert '"BASELINE_RECOVERED"' in SOURCE
    assert 'operation="baseline_recovery"' in SOURCE
    assert 'operation="pending_candidate"' in SOURCE
    assert 'operation="inspect_transaction"' in SOURCE
    assert "clear_rate_limit_cooldown()" in SOURCE
