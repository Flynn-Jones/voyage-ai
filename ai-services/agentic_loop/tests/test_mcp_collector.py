"""MCP collector tests: real ClientSession against the real FastMCP server object (in-memory transport).

Only the Destination DB HTTP call (tools.requests.get) is faked.
"""
import pytest
import requests
from mcp.shared.memory import create_connected_server_and_client_session

import tools
from collectors import mcp_collector
from core.reporter import Trace
from server import mcp

ROWS = [
    {"destination_id": 1, "city": "Tokyo", "country": "Japan"},
    {"destination_id": 2, "city": "Kyoto", "country": "Japan"},
    {"destination_id": 3, "city": "Osaka", "country": "Japan"},
]


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture
def db(monkeypatch):
    state = {"respond": lambda params: FakeResponse(ROWS), "calls": []}

    def _get(url, params=None, timeout=None):
        state["calls"].append(dict(params or {}))
        result = state["respond"](params or {})
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(tools.requests, "get", _get)
    return state


async def run(task=None):
    trace = Trace("MCP")
    async with create_connected_server_and_client_session(mcp) as session:
        ok = await mcp_collector.run_task(session, task or mcp_collector.TASK, trace)
    return ok, trace


def phases(trace):
    return [p for p, _ in trace.steps]


def adapt_text(trace):
    return " ".join(m for p, m in trace.steps if p == "ADAPT")


async def test_happy_path(db):
    ok, trace = await run()
    assert ok
    assert phases(trace) == ["PLAN", "PLAN", "ACT", "OBSERVE", "ADAPT"]
    assert db["calls"] == [{"country": "Japan"}]
    observe = [m for p, m in trace.steps if p == "OBSERVE"][0]
    assert "Tokyo" in observe and "Kyoto" in observe and "Osaka" in observe
    assert "PASS" in adapt_text(trace)


async def test_db_unavailable_fails(db):
    db["respond"] = lambda params: requests.exceptions.ConnectionError("down")
    ok, trace = await run()
    assert not ok
    assert "FAIL tool_error" in adapt_text(trace) and "destination-db unavailable" in adapt_text(trace)


@pytest.mark.parametrize("response", [FakeResponse({}, 500), FakeResponse({"a": 1}), FakeResponse(ValueError("bad"))])
async def test_malformed_db_fails(db, response):
    db["respond"] = lambda params: response
    ok, trace = await run()
    assert not ok
    assert "FAIL tool_error" in adapt_text(trace)


async def test_wrong_country_fails(db):
    db["respond"] = lambda params: FakeResponse(ROWS + [{"destination_id": 9, "city": "Paris", "country": "France"}])
    ok, trace = await run()
    assert not ok
    assert "FAIL unsatisfied" in adapt_text(trace) and "Paris" in adapt_text(trace)


async def test_filtered_empty_unfiltered_non_empty(db):
    db["respond"] = lambda params: FakeResponse([] if params else ROWS)
    ok, trace = await run()
    assert not ok
    assert db["calls"] == [{"country": "Japan"}, {}]
    assert "filter matched nothing" in adapt_text(trace)


async def test_both_empty_is_dependency(db):
    db["respond"] = lambda params: FakeResponse([])
    ok, trace = await run()
    assert not ok
    assert len(db["calls"]) == 2
    assert "FAIL dependency" in adapt_text(trace)


async def test_unregistered_tool_makes_no_call(db):
    ok, trace = await run({**mcp_collector.TASK, "tool": "no_such_tool"})
    assert not ok
    assert db["calls"] == []
    assert "unregistered" in adapt_text(trace)
    assert "ACT" not in phases(trace)


async def test_unsupported_argument_makes_no_call(db):
    ok, trace = await run({**mcp_collector.TASK, "arguments": {"continent": "Asia"}})
    assert not ok and db["calls"] == []
    assert "unsupported_args" in adapt_text(trace)


class _Result:
    def __init__(self, structured=None, is_error=False):
        self.structuredContent, self.isError, self.content = structured, is_error, []


@pytest.mark.parametrize("structured,fragment", [
    (None, "structuredContent"),
    ({"source": "destination-database", "filters": {"country": "Japan"}, "count": 3, "destinations": ROWS[:2]}, "count"),
    ({"source": "other", "filters": {"country": "Japan"}, "count": 3, "destinations": ROWS}, "source"),
    ({"source": "destination-database", "filters": {}, "count": 3, "destinations": ROWS}, "filters"),
    ({"source": "destination-database", "filters": {"country": "Japan"}, "count": 3, "destinations": "x"}, "destinations"),
])
def test_evaluate_malformed(structured, fragment):
    reason, detail = mcp_collector.evaluate(_Result(structured), {"country": "Japan"})
    assert reason == "malformed" and fragment in detail


def test_evaluate_is_error():
    reason, _ = mcp_collector.evaluate(_Result(is_error=True), {})
    assert reason == "tool_error"


def test_collect_unreachable_transport(monkeypatch, capsys):
    monkeypatch.setattr(mcp_collector, "MCP_SERVICE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(mcp_collector, "TIMEOUT_SECONDS", 5.0)
    ok, text = mcp_collector.collect()
    assert ok is False
    assert "FAIL transport" in text
