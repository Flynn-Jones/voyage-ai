"""Unit tests for budget-be's Multi-Agent routes. The Multi-Agent Server and budget-db are mocked."""
import pytest

from app import create_app
from services import database_api, multi_agent_api

RUN_ID = "run-abc123"
ITEMS = [
    {"id": 1, "expense": "Guided historic centre walk", "category": "Activities", "estimated_cost": 24.0,
     "destination_id": "dest-paris", "source": "itinerary", "evidence_ref": "itinerary-db:/itinerary-items/1"},
    {"id": 2, "expense": "Visit the local art museum", "category": "Activities", "estimated_cost": 18.5,
     "destination_id": "dest-paris", "source": "itinerary", "evidence_ref": "itinerary-db:/itinerary-items/2"},
]


class FakeMAS:
    """In-memory stand-in for the Multi-Agent Server's HTTP API (same status codes and envelope)."""

    def __init__(self):
        self.state = "awaiting_human_review"
        self.workflow = "budget_reconcile"
        self.reviews = []
        self.applied = []
        self.down = False

    def _check(self):
        if self.down:
            raise multi_agent_api.MultiAgentServiceError("connection refused")

    def start_run(self, run_input, requested_by="budget-be"):
        self._check()
        cap = run_input.get("budget_cap")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
            return 400, {"status": "error", "error": "invalid input: budget_cap must be a number greater than 0"}
        return 201, {"status": "success", "result": {"run_id": RUN_ID, "state": "awaiting_human_review",
                                                     "work": {"items": ITEMS}}}

    def get_run(self, run_id):
        self._check()
        if run_id != RUN_ID:
            return 404, {"status": "error", "error": f"unknown run: {run_id}"}
        return 200, {"status": "success", "result": {"run_id": RUN_ID, "workflow": self.workflow, "state": self.state,
                                                     "input": {"trip_reference": "TRIP-1001", "budget_cap": 1300}}}

    def get_history(self, run_id):
        self._check()
        return 200, {"status": "success", "result": [{"seq": 1, "step": "created"}]}

    def submit_review(self, run_id, review):
        self._check()
        self.reviews.append(review)
        if self.state != "awaiting_human_review":
            return 409, {"status": "error", "error": "only one human decision is allowed per run"}
        if review["decision"] == "reject":
            self.state = "rejected"
            return 200, {"status": "success", "result": {"state": "rejected", "approved_items": [],
                                                         "rejected_ids": ["1", "2"]}}
        approved = [dict(i) for i in ITEMS if i["id"] in review["approved_ids"] or review["decision"] == "approve"]
        for item in approved:
            item.update(review["edits"].get(str(item["id"]), {}))
        self.state = "partially_accepted" if review["decision"] == "partial" else "approved"
        return 200, {"status": "success", "result": {"state": self.state, "approved_items": approved,
                                                     "rejected_ids": [str(i["id"]) for i in ITEMS if i not in approved]}}

    def report_applied(self, run_id, created_ids):
        self._check()
        self.applied.append(created_ids)
        self.state = "applied"
        return 200, {"status": "success", "result": {"state": "applied"}}


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.next_id = 100
        self.fail_after = None

    def create_expense(self, payload):
        if self.fail_after is not None and len(self.rows) >= self.fail_after:
            raise database_api.DatabaseServiceError("budget-db down")
        self.next_id += 1
        self.rows[self.next_id] = payload
        return {"id": self.next_id, **payload}

    def delete_expense(self, expense_id):
        self.rows.pop(expense_id)


@pytest.fixture
def env(monkeypatch):
    mas, db = FakeMAS(), FakeDB()
    monkeypatch.setattr(multi_agent_api, "MULTI_AGENT_ENABLED", True)
    for name in ("start_run", "get_run", "get_history", "submit_review", "report_applied"):
        monkeypatch.setattr(multi_agent_api, name, getattr(mas, name))
    monkeypatch.setattr(database_api, "create_expense", db.create_expense)
    monkeypatch.setattr(database_api, "delete_expense", db.delete_expense)
    return create_app().test_client(), mas, db


def review(client, **body):
    return client.post(f"/budget/multi-agent/runs/{RUN_ID}/review", json=body)


# ---- run ------------------------------------------------------------------------------------

def test_run_creates_a_budget_reconcile_run(env):
    client, _, _ = env
    response = client.post("/budget/multi-agent/run", json={"trip_reference": "TRIP-1001", "budget_cap": 1300})
    assert response.status_code == 201
    assert response.get_json()["result"]["run_id"] == RUN_ID


@pytest.mark.parametrize("cap", [-50, "abc", 0])
def test_run_invalid_input_is_400(env, cap):
    client, _, _ = env
    response = client.post("/budget/multi-agent/run", json={"trip_reference": "TRIP-1001", "budget_cap": cap})
    assert response.status_code == 400 and "budget_cap" in response.get_json()["error"]


def test_server_down_is_502_with_clear_error(env):
    client, mas, _ = env
    mas.down = True
    response = client.post("/budget/multi-agent/run", json={"trip_reference": "TRIP-1001", "budget_cap": 1300})
    assert response.status_code == 502 and "Multi-Agent Server unavailable" in response.get_json()["error"]


def test_get_run_and_history(env):
    client, _, _ = env
    assert client.get(f"/budget/multi-agent/runs/{RUN_ID}").status_code == 200
    assert client.get(f"/budget/multi-agent/runs/{RUN_ID}/history").get_json()["result"][0]["step"] == "created"
    assert client.get("/budget/multi-agent/runs/run-nope").status_code == 404


def test_other_features_runs_are_not_exposed(env):
    client, mas, _ = env
    mas.workflow = "itinerary_something"
    assert client.get(f"/budget/multi-agent/runs/{RUN_ID}").status_code == 404


# ---- review -> adapt ------------------------------------------------------------------------

def test_partial_review_writes_only_approved_and_edited_lines(env):
    client, mas, db = env
    response = review(client, decision="partial", approved_ids=[1], rejected_ids=[2],
                      edits={"1": {"estimated_cost": 20.0, "category": "Sightseeing"}}, note="ok")
    assert response.status_code == 200, response.get_json()
    result = response.get_json()["result"]
    assert result["created_expense_ids"] == [101]
    assert db.rows[101] == {"trip_reference": "TRIP-1001", "expense": "Guided historic centre walk",
                            "category": "Sightseeing", "estimated_cost": 20.0, "destination_id": "dest-paris",
                            "status": "Planned"}
    assert mas.applied == [[101]] and result["state"] == "applied" and result["applied_recorded"] is True


def test_reject_writes_nothing_and_does_not_report_applied(env):
    client, mas, db = env
    response = review(client, decision="reject", note="not now")
    assert response.status_code == 200
    assert response.get_json()["result"]["created_expense_ids"] == []
    assert db.rows == {} and mas.applied == []


def test_second_review_is_409_and_creates_no_duplicates(env):
    client, _, db = env
    assert review(client, decision="approve").status_code == 200
    assert len(db.rows) == 2
    second = review(client, decision="approve")
    assert second.status_code == 409
    assert len(db.rows) == 2


@pytest.mark.parametrize("body, message", [
    ({"decision": "maybe"}, "decision must be one of"),
    ({"decision": "partial", "approved_ids": [1], "edits": {"1": {"expense": "renamed"}}}, "can be edited"),
    ({"decision": "correct", "edits": {"1": {"estimated_cost": -5}}}, "number >= 0"),
    ({"decision": "correct", "edits": {"1": {"estimated_cost": "cheap"}}}, "number >= 0"),
    ({"decision": "correct", "edits": {"1": {"category": ""}}}, "category"),
])
def test_invalid_review_is_400_and_decision_not_spent(env, body, message):
    client, mas, db = env
    response = review(client, **body)
    assert response.status_code == 400 and message in response.get_json()["error"]
    assert mas.reviews == [] and db.rows == {}  # the one-time decision was not sent


def test_db_failure_rolls_back_partial_writes(env):
    client, mas, db = env
    db.fail_after = 1
    response = review(client, decision="approve")
    assert response.status_code == 502 and "nothing was saved" in response.get_json()["error"]
    assert db.rows == {} and mas.applied == []


# ---- disabled (cloud default) -----------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("post", "/budget/multi-agent/run"),
    ("get", f"/budget/multi-agent/runs/{RUN_ID}"),
    ("get", f"/budget/multi-agent/runs/{RUN_ID}/history"),
    ("post", f"/budget/multi-agent/runs/{RUN_ID}/review"),
])
def test_disabled_returns_403_on_every_route(env, monkeypatch, method, path):
    client, mas, _ = env
    monkeypatch.setattr(multi_agent_api, "MULTI_AGENT_ENABLED", False)
    response = getattr(client, method)(path, json={})
    assert response.status_code == 403 and "disabled" in response.get_json()["error"]
    assert mas.reviews == []
