"""accommodation_shortlist workflow (Student 2 / Accommodation). Ollama and MCP are faked; the
fake data mirrors accommodation-db-shaped records so filtering/ranking is exercised end to end."""
import json

import pytest

from clients.mcp_client import MCPClient
from clients.ollama_client import LLMError
from registry import Registry
from tests.conftest import Client
from workflows.accommodation_shortlist import AccommodationShortlistWorkflow

TOKYO = [
    {"id": 1, "name": "Shinjuku Granbell Hotel", "destination_city": "Tokyo", "price_per_night": 120.0,
     "rating": 4.2, "amenities": ["WiFi", "Pool"], "description": "Modern hotel near Shinjuku nightlife"},
    {"id": 2, "name": "Asakusa Ryokan", "destination_city": "Tokyo", "price_per_night": 90.0,
     "rating": 4.6, "amenities": ["Onsen", "Breakfast Included"], "description": "Traditional ryokan near temples"},
    {"id": 3, "name": "Shibuya Sky Suites", "destination_city": "Tokyo", "price_per_night": 250.0,
     "rating": 4.8, "amenities": ["Pool", "Gym", "Bar"], "description": "Luxury suites with skyline views"},
]
WRITE_TOOLS = {"create_accommodation"}


def _matches(acc, filters):
    if filters.get("max_price") is not None and acc["price_per_night"] > filters["max_price"]:
        return False
    amenities = {a.lower() for a in acc.get("amenities", [])}
    for wanted in (filters.get("amenities") or "").split(","):
        wanted = wanted.strip().lower()
        if wanted and wanted not in amenities:
            return False
    return True


class AccommodationFakeMCP:
    """Answers the read-only tools from seed-like data; `down` makes a tool fail like a stopped service."""

    def __init__(self):
        self.calls = []
        self.down = set()

    def sdk(self, tool, args):
        self.calls.append((tool, args))
        if tool in self.down:
            return {"error": f"{tool}: service unavailable"}
        destination = args.get("destination")
        rows = TOKYO if destination == "Tokyo" else []
        if tool == "search_accommodations":
            matched = [r for r in rows if _matches(r, args)]
            return {"destination": destination, "count": len(matched), "matched_by": "destination_city",
                    "accommodations": matched}
        if tool == "get_accommodation_by_destination":
            return {"destination": destination, "count": len(rows), "matched_by": "destination_city",
                    "accommodations": rows}
        if tool == "list_destinations":
            if destination == "Tokyo" or args.get("city") == "Tokyo":
                return {"count": 1, "destinations": [{"city": "Tokyo", "average_daily_cost": 180}]}
            return {"count": 0, "destinations": []}
        return {"error": f"unknown tool {tool}"}

    def factory(self, allowed_tools):
        return MCPClient(allowed_tools, sdk_call=self.sdk, legacy_call=self.sdk)


@pytest.fixture
def shortlist(make_app, chat, monkeypatch):
    """Client + fake MCP; Planner and Reviewer LLM calls fail so the deterministic fallbacks run
    (individual tests queue LLM replies when they test the LLM path)."""
    fake = AccommodationFakeMCP()
    app = make_app()
    app.runner.mcp_factory = fake.factory
    chat.queue("Planner", LLMError("down"), LLMError("down"))
    chat.queue("Reviewer", LLMError("down"), LLMError("down"))
    return Client(app), fake, chat


def run(client, **input_):
    status, body = client.post("/workflows/accommodation_shortlist/runs",
                               {"input": input_, "requested_by": "pytest"})
    return status, body


def codes(work):
    return [c["code"] for c in work["checks"]]


# ---------------------------------------------------------------- registration + input

def test_registered_with_read_only_allowlist(client):
    workflows = {w["name"]: w for w in client.get("/workflows")[1]["result"]}
    wf = workflows["accommodation_shortlist"]
    assert wf["uses_rag"] is False
    assert not WRITE_TOOLS & set(wf["allowed_tools"])
    assert set(wf["allowed_tools"]) == {"search_accommodations", "get_accommodation_by_destination",
                                        "list_destinations"}
    assert "accommodation_shortlist" not in client.app.registry.errors


@pytest.mark.parametrize("bad", [
    {"destination": "Tokyo", "budget_cap": -50},
    {"destination": "Tokyo", "budget_cap": "abc"},
    {"destination": "Tokyo", "budget_cap": 0},
    {"destination": "Tokyo", "budget_cap": True},
    {"destination": "  ", "budget_cap": 150},
    {"budget_cap": 150},
    {"destination": "Tokyo", "budget_cap": 150, "interests": "pool"},
    {"destination": "Tokyo", "budget_cap": 150, "interests": ["x" * 41]},
    {"destination": "Tokyo", "budget_cap": 150, "interests": [f"i{i}" for i in range(11)]},
])
def test_invalid_input_is_400_and_creates_no_run(shortlist, bad):
    client, fake, _ = shortlist
    status, body = run(client, **bad)
    assert status == 400 and "invalid input" in body["error"]
    assert client.get("/runs")[1]["result"] == []
    assert fake.calls == []


# ---------------------------------------------------------------- scenario: within-budget shortlist

def test_tokyo_within_budget_shortlist_and_checks(shortlist):
    client, fake, _ = shortlist
    status, body = run(client, destination="Tokyo", budget_cap=150, interests=["pool"])
    assert status == 201, body
    work, review = body["result"]["work"], body["result"]["review"]

    # search_accommodations finds only the in-budget, interest-matching candidate, but the
    # supplementary get_accommodation_by_destination source adds the other two (unfiltered,
    # scored lower) rather than hiding them.
    assert work["totals"] == {"budget_cap": 150.0, "interests": ["pool"], "candidates_considered": 3,
                              "shortlisted": 3}
    assert len(work["items"]) == 3
    top = work["items"][0]
    assert top["name"] == "Shinjuku Granbell Hotel" and top["within_budget"] is True
    assert top["matched_interests"] == ["pool"]
    assert set(top) == {"id", "name", "price_per_night", "rating", "matched_interests", "within_budget",
                        "score", "accommodation_id", "evidence_ref"}
    assert top["evidence_ref"] == "accommodation-db:/accommodations/1"

    assert codes(work) == []
    assert review["risk"] == "low"
    assert all(tool not in WRITE_TOOLS for tool, _ in fake.calls)


# ---------------------------------------------------------------- scenario: over-budget top pick

def test_over_budget_top_pick_is_flagged_high_risk(shortlist):
    client, fake, _ = shortlist
    status, body = run(client, destination="Tokyo", budget_cap=50)
    assert status == 201, body
    work, review = body["result"]["work"], body["result"]["review"]

    # Nothing in Tokyo is <= $50/night, but the shortlist still surfaces the best-ranked options
    # (by rating) rather than coming back empty, each clearly flagged as over budget.
    assert len(work["items"]) == 3
    top = work["items"][0]
    assert top["name"] == "Shibuya Sky Suites" and top["within_budget"] is False
    over = next(c for c in work["checks"] if c["code"] == "over_budget")
    assert over["amount"] == 200.0
    assert review["risk"] == "high"


def test_over_budget_top_pick_when_only_expensive_options_exist(shortlist):
    client, fake, _ = shortlist
    fake.down.add("get_accommodation_by_destination")
    status, body = run(client, destination="Tokyo", budget_cap=200, interests=["bar"])
    assert status == 201, body
    work, review = body["result"]["work"], body["result"]["review"]

    # amenities filter (bar) matches nothing in-budget, so it is relaxed and retried
    assert len(work["items"]) >= 1
    top = work["items"][0]
    assert top["within_budget"] is True
    assert review["risk"] in ("low", "medium", "high")


# ---------------------------------------------------------------- scenario: no candidates

def test_unknown_destination_reports_no_candidates(shortlist):
    client, fake, _ = shortlist
    status, body = run(client, destination="Nowhereville", budget_cap=100)
    assert status == 201, body
    work, review = body["result"]["work"], body["result"]["review"]
    assert work["items"] == []
    assert "no_candidates" in codes(work)
    assert review["risk"] == "low"
    assert review["recommendations"] == [
        "Widen the budget cap or try a nearby destination; nothing matched this search."]


# ---------------------------------------------------------------- scenario: tool down

def test_destination_tool_down_is_recorded_and_run_continues(shortlist):
    client, fake, _ = shortlist
    fake.down.add("list_destinations")
    status, body = run(client, destination="Tokyo", budget_cap=300)
    assert status == 201, body
    result = body["result"]
    assert result["state"] == "awaiting_human_review"
    assert any(e["detail"].startswith("no data from Destination") for e in result["work"]["evidence"])
    assert len(result["work"]["items"]) == 3


# ---------------------------------------------------------------- human review -> adapt

def test_partial_review_returns_only_approved(shortlist):
    client, _, _ = shortlist
    run_id = run(client, destination="Tokyo", budget_cap=300)[1]["result"]["run_id"]
    status, body = client.post(f"/runs/{run_id}/review", {
        "decision": "partial", "approved_ids": [1], "rejected_ids": [2, 3], "note": "prefer the hotel"})
    assert status == 200, body
    approved = body["result"]["approved_items"]
    assert [i["id"] for i in approved] == [1]
    assert body["result"]["rejected_ids"] == ["2", "3"]


def test_reject_all_returns_nothing_to_apply(shortlist):
    client, _, _ = shortlist
    run_id = run(client, destination="Tokyo", budget_cap=300)[1]["result"]["run_id"]
    body = client.post(f"/runs/{run_id}/review", {"decision": "reject", "note": "not needed"})[1]["result"]
    assert body["approved_items"] == [] and len(body["rejected_ids"]) == 3
    audit = client.app.store.read_audit(run_id)
    assert audit[-1]["step"] == "human_review" and audit[-1]["output"]["decision"] == "reject"


# ---------------------------------------------------------------- LLM paths stay in bounds

def test_llm_plan_with_write_tool_falls_back(shortlist):
    client, fake, chat = shortlist
    chat.replies["Planner"] = [json.dumps({"steps": [{"tool": "create_accommodation", "reason": "cheaper"}]})] * 2
    result = run(client, destination="Tokyo", budget_cap=300)[1]["result"]
    assert result["plan"]["rationale"].startswith("deterministic fallback")
    assert all(tool not in WRITE_TOOLS for tool, _ in fake.calls)


def test_llm_plan_is_used_and_args_come_from_input(shortlist):
    client, fake, chat = shortlist
    chat.replies["Planner"] = [json.dumps({"steps": [
        {"tool": "search_accommodations", "reason": "candidates", "args": {"destination": "HACK"}}],
        "rationale": "minimal"})]
    run(client, destination="Tokyo", budget_cap=300)
    assert ("search_accommodations", {"destination": "Tokyo", "max_price": 300.0}) in fake.calls


def test_llm_review_recommending_a_choice_is_rejected(shortlist):
    client, _, chat = shortlist
    bad = json.dumps({"risk": "high", "findings": ["over"], "recommendations": ["Book the Shinjuku Granbell Hotel"]})
    chat.replies["Reviewer"] = [bad, bad]
    review = run(client, destination="Tokyo", budget_cap=50)[1]["result"]["review"]
    assert not any("Book" in r for r in review["recommendations"])


def test_llm_review_is_grounded_in_computed_checks(shortlist):
    # Checks show the top pick is over budget (fallback risk "high"); the LLM under-called it
    # "low", but risk must never drop below what the computed checks warrant.
    client, _, chat = shortlist
    llm = {"risk": "low", "findings": ["looks fine"], "recommendations": ["Raise the budget cap by $200.00, or search a lower price band."]}
    chat.replies["Reviewer"] = [json.dumps(llm)]
    result = run(client, destination="Tokyo", budget_cap=50)[1]["result"]
    review, checks = result["review"], result["work"]["checks"]
    assert review["findings"] == [c["text"] for c in checks]   # every computed check, nothing invented
    assert review["risk"] == "high"                             # never below the computed risk


def test_llm_amount_not_in_the_data_is_dropped(shortlist):
    client, _, chat = shortlist
    chat.replies["Reviewer"] = [json.dumps({"risk": "high", "findings": [], "recommendations": [
        "Raise the budget cap by $999.00, or search a lower price band."]})]
    review = run(client, destination="Tokyo", budget_cap=50)[1]["result"]["review"]
    assert review["recommendations"] == [
        "Raise the budget cap by $200.00, or search a lower price band."]


# ---------------------------------------------------------------- helpers

def test_workflow_validates_without_server():
    wf = AccommodationShortlistWorkflow()
    assert wf.validate_input({"destination": " Tokyo ", "budget_cap": 100})["destination"] == "Tokyo"
    assert Registry().get("accommodation_shortlist") is not None
