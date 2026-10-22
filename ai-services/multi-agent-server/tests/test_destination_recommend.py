"""destination_recommend workflow (Student 1 / Destination). Ollama and MCP are faked; the fake
data mirrors the destination-db seed rows so filtering/ranking is exercised end to end."""
import json

import pytest

from clients.mcp_client import MCPClient
from clients.ollama_client import LLMError
from registry import Registry
from tests.conftest import Client
from workflows.destination_recommend import DestinationRecommendWorkflow

SEED = [
    {"destination_id": 1, "city": "Tokyo", "country": "Japan", "description": "Neon-lit metropolis.",
     "average_daily_cost": 150.0, "recommended_trip_length": 6, "travel_style": "City break",
     "categories": ["food", "culture", "nightlife", "shopping"]},
    {"destination_id": 2, "city": "Kyoto", "country": "Japan", "description": "Temples, shrines and gardens.",
     "average_daily_cost": 130.0, "recommended_trip_length": 4, "travel_style": "Cultural",
     "categories": ["culture", "history", "nature"]},
    {"destination_id": 3, "city": "Osaka", "country": "Japan", "description": "Street food and nightlife.",
     "average_daily_cost": 125.0, "recommended_trip_length": 3, "travel_style": "Food and nightlife",
     "categories": ["food", "nightlife"]},
    {"destination_id": 5, "city": "Bangkok", "country": "Thailand", "description": "Street food and temples.",
     "average_daily_cost": 70.0, "recommended_trip_length": 5, "travel_style": "Budget",
     "categories": ["food", "culture", "budget"]},
    {"destination_id": 9, "city": "Paris", "country": "France", "description": "Art, food and romance.",
     "average_daily_cost": 200.0, "recommended_trip_length": 5, "travel_style": "Romantic",
     "categories": ["culture", "food", "romance"]},
]
WRITE_TOOLS = {"create_accommodation", "create_destination", "update_destination", "delete_destination"}


class DestinationFakeMCP:
    """Answers list_destinations from seed-like data; `down` makes the tool fail like a stopped service."""

    def __init__(self):
        self.calls = []
        self.down = False

    def sdk(self, tool, args):
        self.calls.append((tool, args))
        if self.down:
            return {"error": "destination-db unavailable"}
        if tool == "list_destinations":
            return {"source": "destination-database", "filters": {}, "count": len(SEED), "destinations": SEED}
        return {"error": f"unknown tool {tool}"}

    def factory(self, allowed_tools):
        return MCPClient(allowed_tools, sdk_call=self.sdk, legacy_call=self.sdk)


@pytest.fixture
def recommend(make_app, chat):
    """Client + fake MCP; Planner and Reviewer LLM calls fail so the deterministic fallbacks run
    (individual tests queue LLM replies when they test the LLM path)."""
    fake = DestinationFakeMCP()
    app = make_app()
    app.runner.mcp_factory = fake.factory
    chat.queue("Planner", LLMError("down"), LLMError("down"))
    chat.queue("Reviewer", LLMError("down"), LLMError("down"))
    return Client(app), fake, chat


def run(client, **input_):
    return client.post("/workflows/destination_recommend/runs", {"input": input_, "requested_by": "pytest"})


def codes(work):
    return [c["code"] for c in work["checks"]]


# ---------------------------------------------------------------- registration + input

def test_registered_mcp_only_and_read_only(client):
    workflows = {w["name"]: w for w in client.get("/workflows")[1]["result"]}
    wf = workflows["destination_recommend"]
    assert wf["allowed_tools"] == ["list_destinations"]
    assert wf["uses_rag"] is False
    assert not WRITE_TOOLS & set(wf["allowed_tools"])
    assert "destination_recommend" not in client.app.registry.errors
    assert "destination_recommend" in client.get("/health")[1]["result"]["workflows"]


@pytest.mark.parametrize("bad", [
    {},
    {"interests": []},
    {"interests": "food"},
    {"interests": ["x" * 41]},
    {"interests": [f"i{i}" for i in range(11)]},
    {"interests": ["food"], "max_daily_cost": -5},
    {"interests": ["food"], "max_daily_cost": 0},
    {"interests": ["food"], "max_daily_cost": True},
    {"interests": ["food"], "max_daily_cost": "abc"},
    {"interests": ["food"], "max_daily_cost": float("nan")},
    {"interests": ["food"], "trip_days": 0},
    {"interests": ["food"], "trip_days": 2.5},
    {"interests": ["food"], "trip_days": True},
    {"interests": ["food"], "country": 7},
    {"interests": ["food"], "country": "x" * 101},
])
def test_invalid_input_is_400_and_creates_no_run(recommend, bad):
    client, fake, _ = recommend
    status, body = run(client, **bad)
    assert status == 400 and "invalid input" in body["error"]
    assert client.get("/runs")[1]["result"] == []
    assert fake.calls == []


# ---------------------------------------------------------------- scenario: within-budget recommendation

def test_food_nightlife_within_budget_is_ranked_with_evidence(recommend):
    client, fake, _ = recommend
    status, body = run(client, interests=["Food", "nightlife"], max_daily_cost=130)
    assert status == 201
    result = body["result"]
    assert result["state"] == "awaiting_human_review"
    assert result["workflow"] == "destination_recommend"
    assert fake.calls == [("list_destinations", {})]            # MCP-only, no RAG
    work = result["work"]
    top = work["items"][0]
    assert top["city"] == "Osaka"
    assert top["matched_interests"] == ["food", "nightlife"]
    assert top["within_budget"] is True
    assert top["evidence_ref"] == "destination-db:/destinations/3"
    assert top["id"] == 3
    assert work["evidence"][0]["source"] == "list_destinations" and work["evidence"][0]["status"] == "ok"
    assert codes(work) == []
    assert result["review"]["risk"] == "low"
    assert not any("rag" in str(t).lower() for t in work["evidence"])


def test_country_filter_is_case_insensitive_and_trip_days_drive_cost(recommend):
    client, _, _ = recommend
    work = run(client, country="japan", trip_days=4)[1]["result"]["work"]
    assert {i["country"] for i in work["items"]} == {"Japan"}
    kyoto = next(i for i in work["items"] if i["city"] == "Kyoto")
    assert kyoto["fits_trip_length"] is True
    assert kyoto["estimated_trip_cost"] == 520.0                 # 130 x 4 days


def test_over_budget_top_pick_is_high_risk(recommend):
    client, _, _ = recommend
    result = run(client, interests=["romance"], max_daily_cost=50)[1]["result"]
    work, review = result["work"], result["review"]
    assert "over_budget" in codes(work)
    assert review["risk"] == "high"
    assert any("$" in r for r in review["recommendations"])


def test_weak_match_is_flagged(recommend):
    client, _, _ = recommend
    work = run(client, interests=["skiing"], country="Thailand")[1]["result"]["work"]
    assert codes(work) == ["weak_match"]


def test_no_candidates_still_reaches_human_review(recommend):
    client, _, _ = recommend
    result = run(client, country="Atlantis")[1]["result"]
    assert result["state"] == "awaiting_human_review"
    assert result["work"]["items"] == []
    assert codes(result["work"]) == ["no_candidates"]
    assert result["review"]["risk"] == "low"


def test_mcp_down_is_no_data_not_a_failed_run(recommend):
    client, fake, _ = recommend
    fake.down = True
    result = run(client, interests=["food"])[1]["result"]
    assert result["state"] == "awaiting_human_review"
    assert "no_data" in codes(result["work"])
    assert result["work"]["evidence"][0]["status"] == "no_data"
    assert result["review"]["risk"] == "medium"


# ---------------------------------------------------------------- human review closes the run, nothing is written

def _start(client):
    return run(client, interests=["food"], max_daily_cost=150)[1]["result"]


def test_all_four_decisions(recommend):
    client, _, _ = recommend
    states = {}
    for decision, extra in (("approve", {}), ("partial", {"approved_ids": [3]}),
                            ("correct", {"edits": {"3": {"reviewer_correction": "cheaper"}}}),
                            ("reject", {})):
        run_id = _start(client)["run_id"]
        status, body = client.post(f"/runs/{run_id}/review", {"decision": decision, **extra})
        assert status == 200, body
        states[decision] = body["result"]
    assert states["approve"]["state"] == "approved" and states["approve"]["approved_items"]
    assert states["partial"]["state"] == "partially_accepted"
    assert [i["id"] for i in states["partial"]["approved_items"]] == [3]
    assert states["correct"]["state"] == "corrected"
    assert next(i for i in states["correct"]["approved_items"] if i["id"] == 3)["reviewer_correction"] == "cheaper"
    assert states["reject"]["state"] == "rejected" and states["reject"]["approved_items"] == []


def test_second_decision_conflicts_and_applied_closes_run(recommend):
    client, _, _ = recommend
    run_id = _start(client)["run_id"]
    assert client.post(f"/runs/{run_id}/review", {"decision": "approve"})[0] == 200
    assert client.post(f"/runs/{run_id}/review", {"decision": "reject"})[0] == 409
    status, body = client.post(f"/runs/{run_id}/applied",
                               {"created_ids": [], "target": "none (read-only destination_recommend)"})
    assert status == 200 and body["result"]["state"] == "applied"


def test_history_and_audit_cover_every_step(recommend, paths):
    client, _, _ = recommend
    run_id = _start(client)["run_id"]
    client.post(f"/runs/{run_id}/review", {"decision": "approve"})
    client.post(f"/runs/{run_id}/applied", {"created_ids": [], "target": "none (read-only destination_recommend)"})
    steps = [e["step"] for e in client.get(f"/runs/{run_id}/history")[1]["result"]]
    assert steps == ["created", "plan", "work", "review", "human_review", "human_review", "applied"]
    audit = [json.loads(line) for line in paths["audit"].read_text().splitlines()]
    assert {a["workflow"] for a in audit} == {"destination_recommend"}
    assert [a["step"] for a in audit] == ["plan", "work", "review", "human_review", "applied"]
    assert all(t["tool"] == "list_destinations" for a in audit for t in a["tools_called"] if t["tool"] != "llm")


# ---------------------------------------------------------------- LLM paths stay in bounds

def test_llm_plan_with_write_or_rag_tool_falls_back(recommend):
    client, fake, chat = recommend
    for tool in ("create_destination", "rag"):
        chat.replies["Planner"] = [json.dumps({"steps": [{"tool": tool, "reason": "x"}]})] * 2
        result = run(client, interests=["food"])[1]["result"]
        assert result["plan"]["rationale"].startswith("deterministic fallback")
    assert {tool for tool, _ in fake.calls} == {"list_destinations"}


def test_llm_plan_is_used_and_args_come_from_input(recommend):
    client, fake, chat = recommend
    chat.replies["Planner"] = [json.dumps({"steps": [
        {"tool": "list_destinations", "reason": "candidates", "args": {"city": "HACK"}}],
        "rationale": "minimal"})]
    result = run(client, interests=["food"])[1]["result"]
    assert result["plan"]["rationale"] == "minimal"
    assert fake.calls == [("list_destinations", {})]


@pytest.mark.parametrize("rec", ["Delete the Paris destination", "Update Tokyo's cost", "Book Kyoto",
                                 "Choose Osaka for the traveller", "Create a new destination"])
def test_llm_review_that_writes_or_chooses_is_rejected(recommend, rec):
    client, _, chat = recommend
    bad = json.dumps({"risk": "high", "findings": ["over"], "recommendations": [rec]})
    chat.replies["Reviewer"] = [bad, bad]
    review = run(client, interests=["romance"], max_daily_cost=50)[1]["result"]["review"]
    assert rec not in review["recommendations"]


def test_llm_review_is_grounded_in_computed_checks(recommend):
    client, _, chat = recommend
    llm = {"risk": "low", "findings": ["looks fine"], "recommendations": ["Raise the daily budget cap by $150.00, or look at a lower-cost country."]}
    chat.replies["Reviewer"] = [json.dumps(llm)]
    result = run(client, interests=["romance"], max_daily_cost=50)[1]["result"]
    review, checks = result["review"], result["work"]["checks"]
    assert review["findings"] == [c["text"] for c in checks]
    assert review["risk"] == "high"


def test_llm_amount_not_in_the_data_is_dropped(recommend):
    client, _, chat = recommend
    chat.replies["Reviewer"] = [json.dumps({"risk": "high", "findings": [], "recommendations": [
        "Raise the daily budget cap by $999.00, or look at a lower-cost country."]})]
    review = run(client, interests=["romance"], max_daily_cost=50)[1]["result"]["review"]
    assert review["recommendations"] == [
        "Raise the daily budget cap by $150.00, or look at a lower-cost country."]


# ---------------------------------------------------------------- helpers

def test_workflow_validates_without_server():
    wf = DestinationRecommendWorkflow()
    clean = wf.validate_input({"interests": [" Food "], "country": " Japan "})
    assert clean == {"interests": ["food"], "max_daily_cost": None, "trip_days": None, "country": "Japan"}
    assert Registry().get("destination_recommend") is not None
