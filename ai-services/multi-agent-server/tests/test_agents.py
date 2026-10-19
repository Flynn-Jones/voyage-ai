"""LLM JSON validation → retry once → deterministic fallback, and the tool allowlist."""
import json

import pytest

from clients.mcp_client import MCPClient, ToolNotAllowedError
from clients.ollama_client import LLMError
from core.workflow import Workflow
from registry import Registry


def _plan_event(client, run_id, step):
    history = client.get(f"/runs/{run_id}/history")[1]["result"]
    return next(e for e in history if e["step"] == step)


def test_invalid_llm_json_twice_falls_back(client, chat):
    chat.queue("Planner", "this is not json", "```json\n[1, 2]\n```")
    chat.queue("Reviewer", "{\"risk\": \"catastrophic\"}", "nope")
    run = client.start(payload={"tools": ["list_destinations"]})
    assert run["plan"]["rationale"].startswith("deterministic fallback")
    assert [s["tool"] for s in run["plan"]["steps"]] == ["list_destinations"]
    plan_event = _plan_event(client, run["run_id"], "plan")
    assert plan_event["detail"]["source"] == "fallback"
    assert [a["ok"] for a in plan_event["detail"]["attempts"]] == [False, False]

    # list_destinations returned 2 records, so the deterministic review sees no problem.
    assert run["review"] == {"risk": "low", "findings": [], "recommendations": []}
    assert _plan_event(client, run["run_id"], "review")["detail"]["source"] == "fallback"
    audit = client.app.store.read_audit(run["run_id"])
    assert audit[0]["source"] == "fallback" and audit[0]["model"] is None


def test_retry_once_then_succeeds(client, chat):
    chat.queue("Planner", "garbage", json.dumps({"steps": [{"tool": "list_expenses", "args": {}}]}))
    run = client.start()
    assert [s["tool"] for s in run["plan"]["steps"]] == ["list_expenses"]
    event = _plan_event(client, run["run_id"], "plan")
    assert event["detail"]["source"] == "llm"
    assert [a["ok"] for a in event["detail"]["attempts"]] == [False, True]
    retry_prompt = [c[1] for c in chat.calls if "Planner agent" in c[0]][1]
    assert "previous reply was rejected" in retry_prompt


def test_ollama_unreachable_falls_back(client, chat):
    chat.queue("Planner", LLMError("ollama unreachable"), LLMError("ollama unreachable"))
    chat.queue("Reviewer", LLMError("ollama unreachable"), LLMError("ollama unreachable"))
    run = client.start()
    assert run["state"] == "awaiting_human_review"
    # list_expenses is empty in the fake data → the fallback reviewer flags it
    assert run["review"]["risk"] == "medium"
    assert run["review"]["findings"] == ["list_expenses returned no records"]


def test_planner_choosing_disallowed_tool_is_rejected(client, chat, fake_mcp):
    bad = json.dumps({"steps": [{"tool": "create_accommodation", "args": {"name": "x"}}]})
    chat.queue("Planner", bad, bad)
    run = client.start()
    assert [s["tool"] for s in run["plan"]["steps"]] == ["list_destinations", "list_expenses"]
    assert all(call[0] != "create_accommodation" for call in fake_mcp.calls)
    event = _plan_event(client, run["run_id"], "plan")
    assert "outside allowed_tools" in event["detail"]["attempts"][0]["error"]


def test_plan_ignoring_the_input_is_rejected(client, fake_mcp):
    # Found against real qwen2.5:7b: asked for list_destinations only, it planned list_expenses.
    run = client.start(payload={"tools": ["list_destinations"]})  # FakeChat's default plan uses both tools
    assert [s["tool"] for s in run["plan"]["steps"]] == ["list_destinations"]
    assert [c[0] for c in fake_mcp.calls] == ["list_destinations"]
    event = _plan_event(client, run["run_id"], "plan")
    assert event["detail"]["source"] == "fallback"
    assert "only asked for" in event["detail"]["attempts"][0]["error"]


class SneakyWorkflow(Workflow):
    name = "test_sneaky"
    description = "tries a tool it is not allowed"
    allowed_tools = ["list_destinations"]

    def validate_input(self, payload):
        return payload

    def planner_prompt(self, payload):
        return "plan"

    def fallback_plan(self, payload):
        return {"steps": []}

    def work(self, payload, plan, ctx):
        ctx.mcp("create_accommodation", {"name": "evil"})
        return {"items": [], "summary": "", "evidence": []}

    def reviewer_prompt(self, payload, plan, work):
        return "review"

    def fallback_review(self, payload, plan, work):
        return {"risk": "low", "findings": [], "recommendations": []}


def test_worker_disallowed_tool_is_refused_logged_and_fails_run(make_app, fake_mcp, chat):
    from tests.conftest import Client

    chat.queue("Planner", json.dumps({"steps": []}))
    registry = Registry()
    registry.workflows["test_sneaky"] = SneakyWorkflow()
    client = Client(make_app(registry))
    status, body = client.post("/workflows/test_sneaky/runs", {"input": {}})
    assert status == 500
    run_id = body["error"]["run_id"]
    assert fake_mcp.calls == []  # refused before any transport call

    history = client.get(f"/runs/{run_id}/history")[1]["result"]
    refused = next(e for e in history if e["step"] == "tool_refused")
    assert refused["detail"]["tool"] == "create_accommodation"
    assert history[-1]["state"] == "failed"

    audit = client.app.store.read_audit(run_id)
    refusal = next(e for e in audit if e["step"] == "tool_refused")
    assert refusal["tools_called"][0]["refused"] is True
    assert "allowlist" in refusal["error"]


def test_rag_refused_when_workflow_does_not_use_rag(make_app, chat):
    from tests.conftest import Client

    class RagSneak(SneakyWorkflow):
        name = "test_rag_sneak"

        def work(self, payload, plan, ctx):
            ctx.rag("anything")
            return {"items": [], "summary": "", "evidence": []}

    registry = Registry()
    registry.workflows["test_rag_sneak"] = RagSneak()
    client = Client(make_app(registry))
    status, body = client.post("/workflows/test_rag_sneak/runs", {"input": {}})
    assert status == 500 and "uses_rag = False" in body["error"]["message"]


def test_mcp_client_enforces_allowlist_before_network():
    called = []
    client = MCPClient(["list_expenses"], sdk_call=lambda *a: called.append(a), legacy_call=lambda *a: called.append(a))
    with pytest.raises(ToolNotAllowedError):
        client.call("create_accommodation", {})
    assert called == []


def test_mcp_client_falls_back_to_legacy_shim(fake_mcp):
    fake_mcp.fail_sdk = True
    result, transport = fake_mcp.factory(["list_expenses"]).call("list_expenses", {})
    assert transport == "legacy-shim"
    assert result["count"] == 0
    assert fake_mcp.calls == [("legacy:list_expenses", {})]


def test_worker_bad_output_shape_fails_run(make_app):
    from tests.conftest import Client

    class BadShape(SneakyWorkflow):
        name = "test_bad_shape"

        def work(self, payload, plan, ctx):
            return {"items": [{"no_id": True}], "summary": "x", "evidence": []}

    registry = Registry()
    registry.workflows["test_bad_shape"] = BadShape()
    client = Client(make_app(registry))
    status, body = client.post("/workflows/test_bad_shape/runs", {"input": {}})
    assert status == 500 and "must be an object with an 'id'" in body["error"]["message"]


def test_registry_reports_broken_workflow_modules(tmp_path, monkeypatch):
    package = tmp_path / "fake_workflows"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "broken_one.py").write_text("raise RuntimeError('syntax-ish error')\n")
    (package / "no_export.py").write_text("x = 1\n")
    (package / "_private.py").write_text("raise RuntimeError('never imported')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    import importlib

    registry = Registry(importlib.import_module("fake_workflows"))
    assert registry.names() == []
    assert set(registry.errors) == {"broken_one", "no_export"}
    assert "does not define WORKFLOW" in registry.errors["no_export"]
