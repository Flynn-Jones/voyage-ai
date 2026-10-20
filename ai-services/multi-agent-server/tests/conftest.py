"""Fixtures: Ollama, MCP and RAG are always faked — no test touches the network
except the in-process Multi-Agent Server bound to 127.0.0.1 on a free port."""
import json
import sys
import threading
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_DIR))

from clients.mcp_client import MCPClient  # noqa: E402
from registry import Registry  # noqa: E402
from server import build_app, create_server  # noqa: E402
from store import Store  # noqa: E402

FAKE_TOOL_RESULTS = {
    "list_destinations": {"source": "destination-database", "count": 2,
                          "destinations": [{"id": 1}, {"id": 2}]},
    "list_expenses": {"trip_reference": None, "count": 0, "expenses": []},
}

GOOD_PLAN = {"steps": [{"tool": "list_destinations", "args": {}}, {"tool": "list_expenses", "args": {}}],
             "rationale": "count both"}
GOOD_REVIEW = {"risk": "medium", "findings": ["list_expenses returned no records"],
               "recommendations": ["seed budget-db"]}


class FakeChat:
    """Stands in for ollama_client.chat. Queue replies per agent; defaults are valid JSON."""

    def __init__(self):
        self.replies = {"Planner": [], "Reviewer": []}
        self.calls = []

    def queue(self, agent: str, *replies):
        self.replies[agent].extend(replies)

    def __call__(self, system_prompt, user_prompt, json_mode=True):
        self.calls.append((system_prompt, user_prompt))
        agent = "Planner" if "Planner agent" in system_prompt else "Reviewer" if "Reviewer agent" in system_prompt else None
        if agent and self.replies[agent]:
            reply = self.replies[agent].pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply if isinstance(reply, str) else json.dumps(reply)
        if agent == "Planner":
            return json.dumps(GOOD_PLAN)
        if agent == "Reviewer":
            return json.dumps(GOOD_REVIEW)
        return json.dumps({"answer": "ok"})


class FakeMCP:
    """Records SDK-transport calls; MCPClient's real allowlist check runs in front of it."""

    def __init__(self):
        self.calls = []
        self.fail_sdk = False

    def sdk(self, tool, arguments):
        if self.fail_sdk:
            raise ConnectionError("sdk transport down")
        self.calls.append((tool, arguments))
        return FAKE_TOOL_RESULTS.get(tool, {"error": f"unknown tool {tool}"})

    def legacy(self, tool, arguments):
        self.calls.append(("legacy:" + tool, arguments))
        return FAKE_TOOL_RESULTS.get(tool, {"error": f"unknown tool {tool}"})

    def factory(self, allowed_tools):
        return MCPClient(allowed_tools, sdk_call=self.sdk, legacy_call=self.legacy)


@pytest.fixture
def paths(tmp_path):
    return {"db": tmp_path / "runs.db", "audit": tmp_path / "logs" / "audit.jsonl"}


@pytest.fixture
def chat():
    return FakeChat()


@pytest.fixture
def fake_mcp():
    return FakeMCP()


@pytest.fixture
def make_app(paths, chat, fake_mcp):
    def _make(registry=None):
        app = build_app(Store(paths["db"], paths["audit"]), registry or Registry(),
                        chat=chat, mcp_factory=fake_mcp.factory, rag_fn=lambda *a, **k: {"status": "success"})
        app.probes = {"ollama_reachable": lambda: True, "mcp_reachable": lambda: True,
                      "rag_reachable": lambda: False}
        return app
    return _make


@pytest.fixture
def app(make_app):
    return make_app()


class Client:
    """Drives App.dispatch like an HTTP client, without sockets."""

    def __init__(self, app):
        self.app = app

    def get(self, path):
        return self.app.dispatch("GET", path, None)

    def post(self, path, body=None):
        return self.app.dispatch("POST", path, body or {})

    def start(self, workflow="example_readonly", payload=None):
        status, body = self.post(f"/workflows/{workflow}/runs", {"input": payload or {}, "requested_by": "pytest"})
        assert status == 201, body
        return body["result"]


@pytest.fixture
def client(app):
    return Client(app)


@pytest.fixture
def live_server(app):
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
