"""budget_reconcile workflow (Student 4 / Budget). Ollama, MCP and RAG are faked; the fake data
mirrors the budget-db and itinerary-db seeds so the totals match the Release 2 test scenarios."""
import json

import pytest

from clients.mcp_client import MCPClient
from clients.ollama_client import LLMError
from registry import Registry
from tests.conftest import Client
from workflows.budget_reconcile import BudgetReconcileWorkflow, similar

EXPENSES = [
    {"id": 1, "trip_reference": "TRIP-1001", "expense": "Flight to Paris", "category": "Transport",
     "estimated_cost": 450.0, "actual_cost": 480.0, "destination_id": "dest-paris", "status": "Paid"},
    {"id": 2, "trip_reference": "TRIP-1001", "expense": "Hotel Le Marais - 4 nights", "category": "Accommodation",
     "estimated_cost": 620.0, "actual_cost": 620.0, "destination_id": "dest-paris", "status": "Paid"},
    {"id": 3, "trip_reference": "TRIP-1001", "expense": "Museum passes", "category": "Activities",
     "estimated_cost": 90.0, "actual_cost": None, "destination_id": "dest-paris", "status": "Planned"},
    {"id": 7, "trip_reference": "TRIP-1003", "expense": "Flight to Tokyo", "category": "Transport",
     "estimated_cost": 900.0, "actual_cost": 940.0, "destination_id": "dest-tokyo", "status": "Paid"},
    {"id": 8, "trip_reference": "TRIP-1003", "expense": "Shinjuku Granbell Hotel - 5 nights",
     "category": "Accommodation", "estimated_cost": 700.0, "actual_cost": None, "destination_id": "dest-tokyo",
     "status": "Planned"},
    {"id": 9, "trip_reference": "TRIP-1003", "expense": "JR rail pass", "category": "Transport",
     "estimated_cost": 210.0, "actual_cost": None, "destination_id": "dest-tokyo", "status": "Cancelled"},
]
ITINERARY = [
    (1, "TRIP-1001", 101, 24.00, "Guided historic centre walk"),
    (2, "TRIP-1001", 102, 18.50, "Visit the local art museum"),
    (3, "TRIP-1001", 103, 32.00, "Riverside food tour"),
    (4, "TRIP-1001", 104, 12.00, "Breakfast market visit"),
    (5, "TRIP-1001", 105, 45.00, "Architecture and gardens tour"),
    (6, "TRIP-1001", 106, 68.00, "Evening cooking class"),
    (10, "TRIP-1003", 301, 15.00, "Temple visit before peak hours"),
    (11, "TRIP-1003", 302, 55.00, "Technology district tour"),
    (12, "TRIP-1003", 303, 40.00, "Sunset observation deck"),
]
WRITE_TOOLS = {"create_accommodation"}


class BudgetFakeMCP:
    """Answers the read-only tools from seed-like data; `down` makes a tool fail like a stopped service."""

    def __init__(self):
        self.calls = []
        self.down = set()

    def sdk(self, tool, args):
        self.calls.append((tool, args))
        if tool in self.down:
            return {"error": f"{tool}: service unavailable"}
        trip = args.get("trip_reference")
        if tool == "list_expenses":
            rows = [e for e in EXPENSES if e["trip_reference"] == trip]
            return {"trip_reference": trip, "count": len(rows), "expenses": rows}
        if tool == "get_itinerary":
            rows = [{"itinerary_item_id": i, "trip_reference": t, "day": 1, "activity_id": a,
                     "destination_id": 1, "estimated_cost": c, "notes": n}
                    for i, t, a, c, n in ITINERARY if t == trip]
            return {"trip_reference": trip, "count": len(rows), "items": rows}
        if tool == "list_activities":
            return {"count": 1, "activities": [{"id": 101, "activity_type": "Sightseeing"}]}
        if tool == "list_destinations":
            return {"count": 1, "destinations": [{"city": args.get("city"), "average_daily_cost": 180}]}
        if tool in ("get_accommodation_by_destination", "search_accommodations"):
            return {"count": 2, "accommodations": [{"price_per_night": 120}, {"price_per_night": 210}]}
        return {"error": f"unknown tool {tool}"}

    def factory(self, allowed_tools):
        return MCPClient(allowed_tools, sdk_call=self.sdk, legacy_call=self.sdk)


@pytest.fixture
def budget(make_app, chat, monkeypatch):
    """Client + fake MCP; Planner and Reviewer LLM calls fail so the deterministic fallbacks run
    (individual tests queue LLM replies when they test the LLM path)."""
    fake = BudgetFakeMCP()
    app = make_app()
    app.runner.mcp_factory = fake.factory
    app.runner.rag_fn = lambda endpoint, query, k=5, **scope: {"status": "success", "results": []}
    chat.queue("Planner", LLMError("down"), LLMError("down"))
    chat.queue("Reviewer", LLMError("down"), LLMError("down"))
    return Client(app), fake, chat


def run(client, **input_):
    status, body = client.post("/workflows/budget_reconcile/runs", {"input": input_, "requested_by": "pytest"})
    return status, body


def codes(work):
    return [c["code"] for c in work["checks"]]


# ---------------------------------------------------------------- registration + input

def test_registered_with_read_only_allowlist(client):
    workflows = {w["name"]: w for w in client.get("/workflows")[1]["result"]}
    wf = workflows["budget_reconcile"]
    assert wf["uses_rag"] is True
    assert not WRITE_TOOLS & set(wf["allowed_tools"])
    assert set(wf["allowed_tools"]) == {"list_expenses", "get_itinerary", "search_accommodations",
                                        "get_accommodation_by_destination", "list_destinations", "list_activities"}
    assert "budget_reconcile" not in client.app.registry.errors


@pytest.mark.parametrize("bad", [
    {"trip_reference": "TRIP-1001", "budget_cap": -50},
    {"trip_reference": "TRIP-1001", "budget_cap": "abc"},
    {"trip_reference": "TRIP-1001", "budget_cap": 0},
    {"trip_reference": "TRIP-1001", "budget_cap": True},
    {"trip_reference": "  ", "budget_cap": 100},
    {"budget_cap": 100},
    {"trip_reference": "TRIP-1001", "budget_cap": 100, "note": "x" * 501},
])
def test_invalid_input_is_400_and_creates_no_run(budget, bad):
    client, fake, _ = budget
    status, body = run(client, **bad)
    assert status == 400 and "invalid input" in body["error"]
    assert client.get("/runs")[1]["result"] == []
    assert fake.calls == []


# ---------------------------------------------------------------- scenario 1: TRIP-1001

def test_trip_1001_totals_flags_and_lines(budget):
    client, fake, _ = budget
    status, body = run(client, trip_reference="TRIP-1001", budget_cap=1300)
    assert status == 201, body
    work, review = body["result"]["work"], body["result"]["review"]

    assert work["totals"] == {"budget_cap": 1300.0, "existing_estimated": 1160.0, "existing_actual": 1100.0,
                              "proposed_additions": 199.5, "projected_total": 1359.5, "remaining_or_over": -59.5}
    assert len(work["items"]) == 6
    item = work["items"][1]
    assert set(item) == {"id", "expense", "category", "estimated_cost", "destination_id", "source", "evidence_ref"}
    assert item["destination_id"] == "dest-paris" and item["evidence_ref"] == "itinerary-db:/itinerary-items/2"

    found = codes(work)
    assert "overspend" in found and "actual_above_estimate" in found
    dup = next(c for c in work["checks"] if c["code"] == "possible_duplicate")
    assert dup["item_id"] == 2 and dup["category"] == "Activities" and "Museum passes" in dup["text"]

    assert review["risk"] == "high"
    assert any("Reduce" in r and "$60" in r for r in review["recommendations"])
    assert any("Transport" in r and "$30.00" in r for r in review["recommendations"])
    assert all(tool not in WRITE_TOOLS for tool, _ in fake.calls)


def test_trip_1001_partial_review_returns_only_approved_and_edited(budget):
    client, _, _ = budget
    run_id = run(client, trip_reference="TRIP-1001", budget_cap=1300)[1]["result"]["run_id"]
    status, body = client.post(f"/runs/{run_id}/review", {
        "decision": "partial", "approved_ids": [1, 3, 4, 6], "rejected_ids": [2],
        "edits": {"5": {"estimated_cost": 40.0}}, "note": "museum already covered by passes"})
    assert status == 200, body
    approved = body["result"]["approved_items"]
    assert [i["id"] for i in approved] == [1, 3, 4, 5, 6]
    assert next(i for i in approved if i["id"] == 5)["estimated_cost"] == 40.0
    assert body["result"]["rejected_ids"] == ["2"]


# ---------------------------------------------------------------- scenarios 2-4, 6-8

def test_trip_1003_ignores_cancelled_and_flags_flight_overrun(budget):
    client, _, _ = budget
    work = run(client, trip_reference="TRIP-1003", budget_cap=1500)[1]["result"]["work"]
    assert work["ignored_cancelled"] == ["JR rail pass"]
    assert work["totals"]["existing_estimated"] == 1600.0
    over = next(c for c in work["checks"] if c["code"] == "actual_above_estimate")
    assert over["category"] == "Transport" and over["amount"] == 40.0


def test_unknown_trip_reports_no_evidence_and_invents_nothing(budget):
    client, _, _ = budget
    result = run(client, trip_reference="TRIP-9999", budget_cap=1000)[1]["result"]
    assert result["work"]["items"] == []
    assert result["work"]["summary"].startswith("No evidence found for TRIP-9999")
    assert "no_evidence_found" in codes(result["work"])
    assert result["review"]["risk"] == "low"
    assert result["review"]["recommendations"] == [
        "Record budget lines or schedule itinerary items for this trip, then reconcile again."]


def test_no_evidence_drops_generic_llm_advice(budget):
    # qwen2.5:7b live on TRIP-9999: "Ensure all categories and items are included in future reconciliations".
    client, _, chat = budget
    chat.replies["Reviewer"] = [json.dumps({"risk": "low", "findings": [], "recommendations": [
        "Ensure all categories and items are included in future reconciliations"]})]
    review = run(client, trip_reference="TRIP-9999", budget_cap=1000)[1]["result"]["review"]
    assert review["recommendations"] == [
        "Record budget lines or schedule itinerary items for this trip, then reconcile again."]


def test_activity_tool_down_is_recorded_and_run_continues(budget):
    client, fake, _ = budget
    fake.down.add("list_activities")
    result = run(client, trip_reference="TRIP-1001", budget_cap=1300)[1]["result"]
    assert result["state"] == "awaiting_human_review"
    assert any(e["detail"].startswith("no data from Activity") for e in result["work"]["evidence"])
    assert len(result["work"]["items"]) == 6


def test_out_of_scope_request_is_declined_and_nothing_written(budget):
    client, fake, _ = budget
    result = run(client, trip_reference="TRIP-1001", budget_cap=1300,
                 note="Also book a cheaper hotel")[1]["result"]
    assert result["work"]["declined_requests"] == ["book a cheaper hotel"]
    assert any(c["code"] == "declined" for c in result["work"]["checks"])
    assert all(tool not in WRITE_TOOLS for tool, _ in fake.calls)
    assert {i["source"] for i in result["work"]["items"]} == {"itinerary"}


def test_reject_all_returns_nothing_to_apply(budget):
    client, _, _ = budget
    run_id = run(client, trip_reference="TRIP-1001", budget_cap=1300)[1]["result"]["run_id"]
    body = client.post(f"/runs/{run_id}/review", {"decision": "reject", "note": "not now"})[1]["result"]
    assert body["approved_items"] == [] and len(body["rejected_ids"]) == 6
    audit = client.app.store.read_audit(run_id)
    assert audit[-1]["step"] == "human_review" and audit[-1]["output"]["decision"] == "reject"


# ---------------------------------------------------------------- LLM paths stay in bounds

def test_llm_plan_with_write_tool_falls_back(budget):
    client, fake, chat = budget
    chat.replies["Planner"] = [json.dumps({"steps": [{"tool": "create_accommodation", "reason": "cheaper"}]})] * 2
    result = run(client, trip_reference="TRIP-1001", budget_cap=1300)[1]["result"]
    assert result["plan"]["rationale"].startswith("deterministic fallback")
    assert all(tool not in WRITE_TOOLS for tool, _ in fake.calls)


def test_llm_plan_is_used_and_args_come_from_input(budget):
    client, fake, chat = budget
    chat.replies["Planner"] = [json.dumps({"steps": [
        {"tool": "list_expenses", "reason": "recorded lines", "args": {"trip_reference": "TRIP-HACK"}},
        {"tool": "get_itinerary", "reason": "chosen items"}], "rationale": "minimal"})]
    result = run(client, trip_reference="TRIP-1001", budget_cap=1300)[1]["result"]
    # list_activities is a declared dependency of get_itinerary (it categorises the scheduled items)
    assert [s["tool"] for s in result["plan"]["steps"]] == ["list_expenses", "get_itinerary", "list_activities"]
    assert result["plan"]["steps"][2]["reason"].startswith("added automatically")
    assert ("list_expenses", {"trip_reference": "TRIP-1001"}) in fake.calls
    assert sorted(t for t, _ in fake.calls) == ["get_itinerary", "list_activities", "list_expenses"]


def test_llm_review_recommending_a_booking_is_rejected(budget):
    client, _, chat = budget
    bad = json.dumps({"risk": "high", "findings": ["over"], "recommendations": ["Book a cheaper hotel in Paris"]})
    chat.replies["Reviewer"] = [bad, bad]
    review = run(client, trip_reference="TRIP-1001", budget_cap=1300)[1]["result"]["review"]
    assert not any("Book" in r for r in review["recommendations"])
    assert any("Reduce" in r for r in review["recommendations"])


def test_llm_review_is_grounded_in_computed_checks(budget):
    # qwen2.5:7b live: kept 1 of 3 findings and said risk "medium" while over the cap.
    client, _, chat = budget
    llm = {"risk": "medium", "findings": ["$59.50 over cap"], "recommendations": ["Reduce Food by ~$60"]}
    chat.replies["Reviewer"] = [json.dumps(llm)]
    result = run(client, trip_reference="TRIP-1001", budget_cap=1300)[1]["result"]
    review, checks = result["review"], result["work"]["checks"]
    assert review["findings"] == [c["text"] for c in checks]          # every computed check, nothing invented
    assert review["risk"] == "high"                                   # never below the computed risk
    assert review["recommendations"] == ["Reduce Food by ~$60"]       # in scope and matches the $59.50 overspend


def test_llm_reduce_advice_under_cap_is_dropped(budget):
    # qwen2.5:7b live on TRIP-1002 (under cap): "Reduce Activities by ~$27.5".
    client, _, chat = budget
    chat.replies["Reviewer"] = [json.dumps({"risk": "low", "findings": [], "recommendations": [
        "Reduce Activities by ~$27.5"]})]
    review = run(client, trip_reference="TRIP-1001", budget_cap=5000)[1]["result"]["review"]
    assert not any(r.startswith("Reduce") for r in review["recommendations"])


def test_llm_amount_not_in_the_data_is_dropped(budget):
    # qwen2.5:7b live on TRIP-1003 ($210 over): copied the prompt's example "~$60".
    client, _, chat = budget
    chat.replies["Reviewer"] = [json.dumps({"risk": "high", "findings": [], "recommendations": [
        "Reduce Activities by ~$60", "Increase the Transport estimate by $40.00 to match the actual cost."]})]
    review = run(client, trip_reference="TRIP-1003", budget_cap=1500)[1]["result"]["review"]
    assert review["recommendations"] == ["Increase the Transport estimate by $40.00 to match the actual cost."]


# ---------------------------------------------------------------- helpers

@pytest.mark.parametrize("a, b, expected_at_least", [
    ("Museum passes", "Visit the local art museum", 1.0),
    ("Cooking class", "Evening cooking class", 0.5),
])
def test_similarity_catches_fuzzy_duplicates(a, b, expected_at_least):
    assert similar(a, b) >= expected_at_least


def test_similarity_ignores_unrelated_names():
    assert similar("Flight to Paris", "Riverside food tour") < 0.5


def test_workflow_validates_without_server():
    wf = BudgetReconcileWorkflow()
    assert wf.validate_input({"trip_reference": " TRIP-1 ", "budget_cap": 10})["trip_reference"] == "TRIP-1"
    assert Registry().get("budget_reconcile") is not None
