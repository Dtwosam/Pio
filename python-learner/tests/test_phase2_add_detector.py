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
        "os": os,
        "subprocess": subprocess,
        "RpcRateLimited": RuntimeError,
        "log": lambda *args, **kwargs: None,
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


def test_detector_cursor_is_fail_closed():
    branch = next(
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "batch_failed"
    )

    # A failed batch must not contain an assignment advancing the pool cursor.
    assert not any(
        _cursor_assignment(node)
        for statement in branch.body
        for node in ast.walk(statement)
    )

    # Cursor advancement must exist only in the successful else branch.
    assert any(
        _cursor_assignment(node)
        for statement in branch.orelse
        for node in ast.walk(statement)
    )

    failed_source = "\n".join(
        ast.get_source_segment(SOURCE, statement) or ""
        for statement in branch.body
    )

    assert '"BATCH_RETRY"' in failed_source
    assert "retained_cursor=cursor" in failed_source


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
