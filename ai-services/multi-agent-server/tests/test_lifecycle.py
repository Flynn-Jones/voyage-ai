"""Planner → Worker → Reviewer → Human Review → applied, over the HTTP routes."""
import pytest

from store import AUDIT_FIELDS


def test_health_reports_dependencies_and_workflows(client):
    status, body = client.get("/health")
    assert status == 200
    result = body["result"]
    assert result["service"] == "multi-agent-server"
    assert result["port"] == 7004
    assert result["ollama_reachable"] is True and result["rag_reachable"] is False
    assert "example_readonly" in result["workflows"]


def test_workflows_lists_name_description_and_allowlist(client):
    status, body = client.get("/workflows")
    assert status == 200
    example = next(w for w in body["result"] if w["name"] == "example_readonly")
    assert example["allowed_tools"] == ["list_destinations", "list_expenses"]
    assert example["description"]


def test_full_lifecycle(client, fake_mcp, paths):
    run = client.start()
    assert run["state"] == "awaiting_human_review"
    assert [s["tool"] for s in run["plan"]["steps"]] == ["list_destinations", "list_expenses"]
    assert run["work"]["summary"] == "2 tool call(s), 2 record(s) in total"
    assert run["review"]["risk"] == "medium"
    assert [c[0] for c in fake_mcp.calls] == ["list_destinations", "list_expenses"]

    status, body = client.post(f"/runs/{run['run_id']}/review",
                               {"decision": "approve", "reviewer": "tester", "note": "ok"})
    assert status == 200
    assert body["result"]["state"] == "approved"
    assert [i["id"] for i in body["result"]["approved_items"]] == [1, 2]

    status, body = client.post(f"/runs/{run['run_id']}/applied", {"created_ids": [101, 102], "target": "test-db"})
    assert status == 200 and body["result"]["state"] == "applied"

    status, body = client.get(f"/runs/{run['run_id']}")
    assert body["result"]["state"] == "applied"
    assert body["result"]["applied"] == {"created_ids": [101, 102], "target": "test-db"}
    assert body["result"]["decision"]["reviewer"] == "tester"

    status, body = client.get(f"/runs/{run['run_id']}/history")
    steps = [(e["step"], e["state"]) for e in body["result"]]
    assert steps == [
        ("created", "running"), ("plan", "running"), ("work", "running"), ("review", "running"),
        ("human_review", "awaiting_human_review"), ("human_review", "approved"), ("applied", "applied"),
    ]

    audit = client.app.store.read_audit(run["run_id"])
    assert [e["step"] for e in audit] == ["plan", "work", "review", "human_review", "applied"]
    for entry in audit:
        assert set(AUDIT_FIELDS) <= set(entry)
    work_entry = audit[1]
    assert work_entry["agent"] == "worker"
    assert [c["tool"] for c in work_entry["tools_called"]] == ["list_destinations", "list_expenses"]
    assert audit[0]["model"] == "qwen2.5:7b"


def test_list_runs_filters_by_workflow_and_limit(client):
    first = client.start()
    second = client.start()
    status, body = client.get("/runs?workflow=example_readonly&limit=1")
    assert status == 200 and len(body["result"]) == 1
    status, body = client.get("/runs?workflow=example_readonly")
    assert {r["run_id"] for r in body["result"]} == {first["run_id"], second["run_id"]}
    status, body = client.get("/runs?workflow=nope")
    assert body["result"] == []
    assert client.get("/runs?limit=abc")[0] == 400


@pytest.mark.parametrize("review, state, approved, rejected", [
    ({"decision": "approve"}, "approved", [(1, None), (2, None)], []),
    ({"decision": "partial", "approved_ids": [1], "rejected_ids": [2]}, "partially_accepted", [(1, None)], ["2"]),
    ({"decision": "partial", "approved_ids": ["1"], "edits": {"2": {"count": 9}}}, "partially_accepted",
     [(1, None), (2, 9)], []),
    ({"decision": "correct", "edits": {"1": {"count": 5}}}, "corrected", [(1, 5), (2, None)], []),
    ({"decision": "correct", "edits": {"1": {"count": 5}}, "rejected_ids": ["2"]}, "corrected", [(1, 5)], ["2"]),
    ({"decision": "reject", "note": "not needed"}, "rejected", [], ["1", "2"]),
])
def test_each_review_decision(client, review, state, approved, rejected):
    run = client.start()
    status, body = client.post(f"/runs/{run['run_id']}/review", review)
    assert status == 200, body
    result = body["result"]
    assert result["state"] == state
    assert result["rejected_ids"] == rejected
    got = [(i["id"], i["count"]) for i in result["approved_items"]]
    original = {i["id"]: i["count"] for i in run["work"]["items"]}
    assert got == [(i, c if c is not None else original[i]) for i, c in approved]


@pytest.mark.parametrize("review, message", [
    ({"decision": "maybe"}, "decision must be one of"),
    ({"decision": "partial", "approved_ids": [99]}, "unknown item ids"),
    ({"decision": "partial"}, "partial needs approved_ids"),
    ({"decision": "correct"}, "correct needs at least one edit"),
    ({"decision": "correct", "edits": {"1": {"id": 7}}}, "cannot change an item's id"),
    ({"decision": "partial", "approved_ids": [1], "rejected_ids": [1]}, "both approved and rejected"),
    ({"decision": "approve", "rejected_ids": [1]}, "use 'partial'"),
])
def test_invalid_review_is_400_and_leaves_run_reviewable(client, review, message):
    run = client.start()
    status, body = client.post(f"/runs/{run['run_id']}/review", review)
    assert status == 400 and message in str(body["error"])
    assert client.post(f"/runs/{run['run_id']}/review", {"decision": "approve"})[0] == 200


def test_second_review_is_409(client):
    run = client.start()
    assert client.post(f"/runs/{run['run_id']}/review", {"decision": "reject"})[0] == 200
    status, body = client.post(f"/runs/{run['run_id']}/review", {"decision": "approve"})
    assert status == 409 and "only one human decision" in body["error"]
    assert client.get(f"/runs/{run['run_id']}")[1]["result"]["state"] == "rejected"


def test_applying_rejected_run_is_409(client):
    run = client.start()
    client.post(f"/runs/{run['run_id']}/review", {"decision": "reject"})
    status, body = client.post(f"/runs/{run['run_id']}/applied", {"created_ids": [1], "target": "test-db"})
    assert status == 409 and "rejected" in body["error"]


def test_apply_only_once_and_only_after_review(client):
    run = client.start()
    status, body = client.post(f"/runs/{run['run_id']}/applied", {"created_ids": [], "target": "test-db"})
    assert status == 409 and "human decision first" in body["error"]
    client.post(f"/runs/{run['run_id']}/review", {"decision": "approve"})
    assert client.post(f"/runs/{run['run_id']}/applied", {"created_ids": [1], "target": "test-db"})[0] == 200
    status, body = client.post(f"/runs/{run['run_id']}/applied", {"created_ids": [1], "target": "test-db"})
    assert status == 409 and "already been applied" in body["error"]


def test_applied_validates_body(client):
    run = client.start()
    client.post(f"/runs/{run['run_id']}/review", {"decision": "approve"})
    assert client.post(f"/runs/{run['run_id']}/applied", {"target": "x"})[0] == 400
    assert client.post(f"/runs/{run['run_id']}/applied", {"created_ids": []})[0] == 400


def test_unknown_workflow_and_run_are_404(client):
    assert client.post("/workflows/nope_missing/runs", {"input": {}})[0] == 404
    assert client.get("/runs/run-missing")[0] == 404
    assert client.get("/runs/run-missing/history")[0] == 404
    assert client.post("/runs/run-missing/review", {"decision": "approve"})[0] == 404


def test_invalid_input_is_400_and_creates_no_run(client):
    status, body = client.post("/workflows/example_readonly/runs", {"input": {"tools": ["drop_tables"]}})
    assert status == 400 and "invalid input" in body["error"]
    status, body = client.post("/workflows/example_readonly/runs", {"input": "not-an-object"})
    assert status == 400
    assert client.get("/runs")[1]["result"] == []


def test_unknown_route_and_method(client):
    assert client.get("/nope")[0] == 404
    assert client.post("/health")[0] == 405


def test_worker_exception_fails_the_run(client, monkeypatch):
    from core.workflow import RunContext

    def boom(self, tool, args=None):
        raise RuntimeError("worker blew up")
    monkeypatch.setattr(RunContext, "mcp", boom)
    status, body = client.post("/workflows/example_readonly/runs", {"input": {}})
    assert status == 500
    run_id = body["error"]["run_id"]
    run = client.get(f"/runs/{run_id}")[1]["result"]
    assert run["state"] == "failed" and "worker blew up" in run["error"]
    history = client.get(f"/runs/{run_id}/history")[1]["result"]
    assert history[-1]["step"] == "failed" and history[-1]["agent"] == "worker"
    assert client.post(f"/runs/{run_id}/review", {"decision": "approve"})[0] == 409
    audit = client.app.store.read_audit(run_id)
    assert audit[-1]["error"] and audit[-1]["step"] == "work"
