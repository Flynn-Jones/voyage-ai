"""Unit tests for accommodation-be's Multi-Agent routes. The Multi-Agent Server is mocked."""
import pytest

from app import create_app
from services import multi_agent_api

RUN_ID = "run-abc123"
ITEMS = [
    {"id": 1, "name": "Hotel A", "price_per_night": 120.0, "score": 0.9},
    {"id": 2, "name": "Hotel B", "price_per_night": 210.0, "score": 0.6},
]


class FakeMAS:
    """In-memory stand-in for the Multi-Agent Server's HTTP API (same status codes and envelope)."""

    def __init__(self):
        self.state = "awaiting_human_review"
        self.workflow = "accommodation_shortlist"
        self.reviews = []
        self.applied = []
        self.down = False

    def _check(self):
        if self.down:
            raise multi_agent_api.MultiAgentServiceError("connection refused")

    def start_run(self, run_input, requested_by="accommodation-be"):
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
                                                     "input": {"destination": "Tokyo", "budget_cap": 150}}}

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
        self.state = "partially_accepted" if review["decision"] == "partial" else "approved"
        return 200, {"status": "success", "result": {"state": self.state, "approved_items": approved,
                                                     "rejected_ids": [str(i["id"]) for i in ITEMS if i not in approved]}}

    def report_applied(self, run_id):
        self._check()
        self.applied.append(run_id)
        self.state = "applied"
        return 200, {"status": "success", "result": {"state": "applied"}}


@pytest.fixture
def env(monkeypatch):
    mas = FakeMAS()
    monkeypatch.setattr(multi_agent_api, "MULTI_AGENT_ENABLED", True)
    for name in ("start_run", "get_run", "get_history", "submit_review", "report_applied"):
        monkeypatch.setattr(multi_agent_api, name, getattr(mas, name))
    return create_app().test_client(), mas


def review(client, **body):
    return client.post(f"/accommodation/multi-agent/runs/{RUN_ID}/review", json=body)


# ---- run ------------------------------------------------------------------------------------

def test_run_creates_a_shortlist_run(env):
    client, _ = env
    response = client.post("/accommodation/multi-agent/run",
                           json={"destination": "Tokyo", "budget_cap": 150, "interests": ["pool"]})
    assert response.status_code == 201
    assert response.get_json()["result"]["run_id"] == RUN_ID


@pytest.mark.parametrize("cap", [-50, "abc", 0])
def test_run_invalid_input_is_400(env, cap):
    client, _ = env
    response = client.post("/accommodation/multi-agent/run", json={"destination": "Tokyo", "budget_cap": cap})
    assert response.status_code == 400 and "budget_cap" in response.get_json()["error"]


def test_server_down_is_502_with_clear_error(env):
    client, mas = env
    mas.down = True
    response = client.post("/accommodation/multi-agent/run", json={"destination": "Tokyo", "budget_cap": 150})
    assert response.status_code == 502 and "Multi-Agent Server unavailable" in response.get_json()["error"]


def test_get_run_and_history(env):
    client, _ = env
    assert client.get(f"/accommodation/multi-agent/runs/{RUN_ID}").status_code == 200
    assert client.get(f"/accommodation/multi-agent/runs/{RUN_ID}/history").get_json()["result"][0]["step"] == "created"
    assert client.get("/accommodation/multi-agent/runs/run-nope").status_code == 404


def test_other_features_runs_are_not_exposed(env):
    client, mas = env
    mas.workflow = "budget_reconcile"
    assert client.get(f"/accommodation/multi-agent/runs/{RUN_ID}").status_code == 404


# ---- review -> adapt (read-only: nothing is written, just closed out) ------------------------

def test_partial_review_closes_the_run_as_applied(env):
    client, mas = env
    response = review(client, decision="partial", approved_ids=[1], rejected_ids=[2], note="over budget")
    assert response.status_code == 200, response.get_json()
    result = response.get_json()["result"]
    assert [i["id"] for i in result["approved_items"]] == [1]
    assert mas.applied == [RUN_ID] and result["state"] == "applied" and result["applied_recorded"] is True


def test_reject_does_not_report_applied(env):
    client, mas = env
    response = review(client, decision="reject", note="not now")
    assert response.status_code == 200
    assert response.get_json()["result"]["approved_items"] == []
    assert mas.applied == []


def test_second_review_is_409(env):
    client, _ = env
    assert review(client, decision="approve").status_code == 200
    second = review(client, decision="approve")
    assert second.status_code == 409


def test_invalid_decision_is_400_and_not_sent(env):
    client, mas = env
    response = review(client, decision="maybe")
    assert response.status_code == 400 and "decision must be one of" in response.get_json()["error"]
    assert mas.reviews == []


# ---- disabled (cloud default) -----------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("post", "/accommodation/multi-agent/run"),
    ("get", f"/accommodation/multi-agent/runs/{RUN_ID}"),
    ("get", f"/accommodation/multi-agent/runs/{RUN_ID}/history"),
    ("post", f"/accommodation/multi-agent/runs/{RUN_ID}/review"),
])
def test_disabled_returns_403_on_every_route(env, monkeypatch, method, path):
    client, mas = env
    monkeypatch.setattr(multi_agent_api, "MULTI_AGENT_ENABLED", False)
    response = getattr(client, method)(path, json={})
    assert response.status_code == 403 and "disabled" in response.get_json()["error"]
    assert mas.reviews == []
