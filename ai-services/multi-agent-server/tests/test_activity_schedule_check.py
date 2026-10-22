"""activity_schedule_check workflow (Student 3 / Activity). Ollama and MCP are faked; the fake data
mirrors activity database records so overlap and free-slot logic is exercised end to end."""
import json

import pytest

from clients.mcp_client import MCPClient
from clients.ollama_client import LLMError
from tests.conftest import Client
from workflows.activity_schedule_check import _REVIEW_CONTEXT, ActivityScheduleCheckWorkflow, duration_minutes

ACTIVITIES = [
    {"activity_id": 1, "activity_name": "Harbour Kayaking", "activity_type": "Adventure",
     "activity_cost": 35.0, "duration": "2 hours"},
    {"activity_id": 2, "activity_name": "Museum Tour", "activity_type": "Culture",
     "activity_cost": 20.0, "duration": "90 minutes"},
    {"activity_id": 3, "activity_name": "Night Market", "activity_type": "Food",
     "activity_cost": 15.0, "duration": "1 hour"},
    {"activity_id": 4, "activity_name": "Mystery Walk", "activity_type": "Culture",
     "activity_cost": 10.0, "duration": "a while"},
]
ASSIGNMENTS = [
    {"assignment_id": 1, "activity_id": 1, "assignment_time": "2026-10-06T09:00"},  # 09:00-11:00
    {"assignment_id": 2, "activity_id": 2, "assignment_time": "2026-10-06T10:30"},  # 10:30-12:00, clashes with 1
    {"assignment_id": 3, "activity_id": 3, "assignment_time": "2026-10-06T18:00"},  # 18:00-19:00
    {"assignment_id": 4, "activity_id": 4, "assignment_time": "2026-10-07T09:00"},  # unreadable duration
]
WRITE_TOOLS = {"create_activity", "update_activity", "delete_activity"}

GOOD_PLAN = {
    "objective": "Check 2026-10-06 for overlapping activities.",
    "steps": [{"agent": "Worker", "action": "Read activities", "tool": "list_activities"},
              {"agent": "Worker", "action": "Read assignments", "tool": "list_assignments"},
              {"agent": "Reviewer", "action": "Check output"},
              {"agent": "Human", "action": "Decide"}],
    "evidence_required": ["MCP:list_activities", "MCP:list_assignments"],
    "human_approval": "Human accepts or rejects the clash report.",
}


class ActivityFakeMCP:
    """Answers the two read-only tools; `down` makes a tool fail like a stopped database."""

    def __init__(self):
        self.calls = []
        self.down = set()

    def sdk(self, tool, args):
        self.calls.append((tool, args))
        if tool in self.down:
            return {"error": "activity database unavailable: connection refused"}
        if tool == "list_activities":
            return {"count": len(ACTIVITIES), "activities": ACTIVITIES}
        if tool == "list_assignments":
            return {"count": len(ASSIGNMENTS), "assignments": ASSIGNMENTS}
        return {"error": f"unknown tool {tool}"}

    def factory(self, allowed_tools):
        return MCPClient(allowed_tools, sdk_call=self.sdk, legacy_call=self.sdk)


class WorkerAwareChat:
    """Wraps FakeChat so the Worker's narration call can be queued too (it defaults to failing,
    which makes the workflow use its deterministic narration)."""

    def __init__(self, chat):
        self.chat = chat
        self.worker_replies = []

    def __call__(self, system_prompt, user_prompt, json_mode=True):
        if "Worker Agent" in system_prompt:
            self.chat.calls.append((system_prompt, user_prompt))
            if self.worker_replies:
                return self.worker_replies.pop(0)
            raise LLMError("down")
        return self.chat(system_prompt, user_prompt, json_mode)


@pytest.fixture
def activity(make_app, chat):
    """Client + fake MCP + chat. Planner and Reviewer LLM calls fail so the deterministic fallbacks
    run (individual tests queue LLM replies when they test the LLM path)."""
    fake = ActivityFakeMCP()
    app = make_app()
    app.runner.mcp_factory = fake.factory
    wrapped = WorkerAwareChat(chat)
    app.runner.chat = app.runner.planner._chat = app.runner.reviewer._chat = wrapped
    chat.queue("Planner", LLMError("down"), LLMError("down"))
    chat.queue("Reviewer", LLMError("down"), LLMError("down"))
    return Client(app), fake, chat, wrapped


def start(client, **input_):
    return client.post("/workflows/activity_schedule_check/runs", {"input": input_, "requested_by": "pytest"})


def codes(work):
    return [c["code"] for c in work["checks"]]


# ---------------------------------------------------------------- registration + input

def test_registered_with_read_only_allowlist(client):
    workflows = {w["name"]: w for w in client.get("/workflows")[1]["result"]}
    wf = workflows["activity_schedule_check"]
    assert wf["allowed_tools"] == ["list_activities", "list_assignments"]
    assert not WRITE_TOOLS & set(wf["allowed_tools"])
    assert wf["uses_rag"] is False
    assert "activity_schedule_check" not in client.app.registry.errors


@pytest.mark.parametrize("bad", [
    {},
    {"request": "Check my day."},
    {"date": "06/10/2026"},
    {"date": "2026-02-30"},
    {"date": "2026-10-06", "slot_hours": 0},
    {"date": "2026-10-06", "slot_hours": True},
    {"date": "2026-10-06", "request": "x" * 301},
])
def test_invalid_input_is_400_and_creates_no_run(activity, bad):
    client, fake, _, _ = activity
    status, body = start(client, **bad)
    assert status == 400 and "invalid input" in body["error"]
    assert fake.calls == []
    assert client.get("/runs")[1]["result"] == []


@pytest.mark.parametrize("text,minutes", [
    ("2 hours", 120), ("90 minutes", 90), ("1.5 hours", 90), ("1h 30m", 90), ("45 min", 45),
    ("half-day", 240), ("Full day", 480), ("3", 180), ("a while", None), ("", None), (None, None),
])
def test_duration_minutes(text, minutes):
    assert duration_minutes(text) == minutes


# ---------------------------------------------------------------- the day check (deterministic fallbacks)

def test_day_check_full_lifecycle(activity):
    client, fake, _, _ = activity
    status, body = start(client, date="2026-10-06")
    assert status == 201, body
    run = body["result"]
    assert run["state"] == "awaiting_human_review"
    assert [(t, a) for t, a in fake.calls] == [("list_activities", {}), ("list_assignments", {})]
    assert [s.get("tool") for s in run["plan"]["steps"] if s["agent"] == "Worker"] == \
        ["list_activities", "list_assignments"]

    work = run["work"]
    # One run: the day's activities, the clashing pair, and a clash-free 2h slot.
    assert [s["name"] for s in work["scheduled"]] == ["Harbour Kayaking", "Museum Tour", "Night Market"]
    assert [(i["kind"], i["id"]) for i in work["items"]] == [("overlap", "1-2"), ("free_slot", "slot-1")]
    assert work["items"][0]["overlap_minutes"] == 30
    assert work["summary"] == ("3 activities scheduled on 2026-10-06; 1 overlapping pair(s); "
                               "1 proposed 2h slot(s) that clash with nothing.")
    assert work["worker_output_source"] == "fallback"
    assert work["worker_output"].startswith("Output:")
    assert codes(work) == ["clash"]
    assert run["review"]["risk"] == "medium"
    assert run["review"]["recommendation"] == "Accept"

    status, body = client.post(f"/runs/{run['run_id']}/review",
                               {"decision": "approve", "reviewer": "tester", "note": "clash confirmed"})
    assert status == 200 and body["result"]["state"] == "approved"


def test_free_slots_never_clash(activity):
    client, _, _, _ = activity
    work = start(client, date="2026-10-06", slot_hours=2)[1]["result"]["work"]
    slots = [i for i in work["items"] if i["kind"] == "free_slot"]
    assert [(i["id"], i["start"], i["end"]) for i in slots] == [("slot-1", "12:00", "14:00")]
    assert slots[0]["proposal"].startswith("Proposed:")
    assert "proposal_conflict" not in codes(work)


def test_unreadable_duration_blocks_rest_of_day_and_is_flagged(activity):
    client, _, _, _ = activity
    run = start(client, date="2026-10-07", slot_hours=1)[1]["result"]
    assert [(i["start"], i["end"]) for i in run["work"]["items"]] == [("08:00", "09:00")]
    assert "unknown_duration" in codes(run["work"])
    assert run["review"]["recommendation"] == "Partially Accept"


def test_scheduled_activities_are_only_that_day(activity):
    client, _, _, _ = activity
    work = start(client, date="2026-10-07")[1]["result"]["work"]
    assert work["scheduled"] == [{"assignment_id": 4, "activity_id": 4, "name": "Mystery Walk",
                                  "time": "2026-10-07T09:00", "duration": "a while"}]
    assert work["totals"]["scheduled_on_day"] == 1 and work["totals"]["activities"] == 4


def test_empty_day_is_reported_not_invented(activity):
    client, _, _, _ = activity
    run = start(client, date="2026-12-25")[1]["result"]
    assert run["work"]["scheduled"] == []
    # an empty day is one free gap, so one slot is proposed at its start
    assert [(i["kind"], i["start"], i["end"]) for i in run["work"]["items"]] == [("free_slot", "08:00", "10:00")]
    assert codes(run["work"]) == ["no_records"]
    assert run["review"]["risk"] == "low"


def test_database_down_is_high_risk_reject(activity):
    client, fake, _, _ = activity
    fake.down = {"list_activities", "list_assignments"}
    run = start(client, date="2026-10-06")[1]["result"]
    assert run["state"] == "awaiting_human_review"
    assert run["work"]["items"] == []  # no free slots are guessed without the schedule
    assert "No activities found" in run["work"]["worker_output"]
    assert codes(run["work"]).count("no_data") == 2
    assert run["review"]["risk"] == "high" and run["review"]["recommendation"] == "Reject"
    assert run["review"]["retest"]


# ---------------------------------------------------------------- LLM paths

def test_prompt_files_are_sent_to_the_agents(activity):
    client, _, chat, _ = activity
    start(client, date="2026-10-06")
    prompts = [user for _, user in chat.calls]
    assert any("Planner Agent for VoyageAI" in p for p in prompts)
    assert any("Reviewer Agent for VoyageAI" in p for p in prompts)
    assert any("Worker Agent for VoyageAI" in system for system, _ in chat.calls)


def test_valid_llm_plan_is_used(activity):
    client, _, chat, _ = activity
    chat.replies["Planner"] = [GOOD_PLAN]
    run = start(client, date="2026-10-06")[1]["result"]
    assert run["plan"]["objective"] == GOOD_PLAN["objective"]
    audit = client.app.store.read_audit(run["run_id"])
    assert next(e for e in audit if e["step"] == "plan")["source"] == "llm"


@pytest.mark.parametrize("bad_plan", [
    {**GOOD_PLAN, "steps": GOOD_PLAN["steps"][:2] + [{"agent": "Worker", "action": "Delete clashing activity"},
                                                     {"agent": "Human", "action": "Decide"}]},
    {**GOOD_PLAN, "steps": [s for s in GOOD_PLAN["steps"] if s.get("tool") != "list_assignments"]},
    {**GOOD_PLAN, "steps": GOOD_PLAN["steps"][:3]},
    {**GOOD_PLAN, "steps": [{**GOOD_PLAN["steps"][0], "tool": "create_activity"}] + GOOD_PLAN["steps"][1:]},
])
def test_unsafe_or_incomplete_llm_plan_falls_back(activity, bad_plan):
    client, _, chat, _ = activity
    chat.replies["Planner"] = [bad_plan, bad_plan]
    run = start(client, date="2026-10-06")[1]["result"]
    audit = client.app.store.read_audit(run["run_id"])
    assert next(e for e in audit if e["step"] == "plan")["source"] == "fallback"


def test_worker_citing_unknown_id_is_caught_and_review_cannot_downgrade(activity):
    client, _, chat, wrapped = activity
    wrapped.worker_replies = [
        "Output:\nHarbour Kayaking (#1) clashes with Sunset Cruise (#99) [MCP:list_activities]\n"
        "Evidence Used:\n[MCP:list_assignments]\nLimitations:\nNone"
    ]
    chat.replies["Reviewer"] = [{"risk": "low", "findings": ["all good"], "recommendations": ["Book #99"],
                                 "correction": "", "retest": "", "recommendation": "Accept"}]
    run = start(client, date="2026-10-06")[1]["result"]
    assert run["work"]["worker_output_source"] == "llm"
    assert "unsupported_claim" in codes(run["work"])
    review = run["review"]
    assert review["risk"] == "medium"
    assert review["recommendation"] == "Partially Accept"
    assert review["findings"] == [c["text"] for c in run["work"]["checks"]]
    assert all("#99" not in rec for rec in review["recommendations"])
    assert review["correction"]


def test_worker_denying_existing_evidence_and_echoed_decisions_are_caught(activity):
    client, _, chat, wrapped = activity
    wrapped.worker_replies = ["Output:\nNo activities found.\nEvidence Used:\nNone\nLimitations:\nNone"]
    chat.replies["Reviewer"] = [{"risk": "low", "findings": [], "recommendations": ["Accept", "Partially Accept",
                                                                                   "Reject"],
                                 "correction": "", "retest": "", "recommendation": "Accept"}]
    run = start(client, date="2026-10-06")[1]["result"]
    assert "unsupported_claim" in codes(run["work"])
    assert run["review"]["recommendation"] == "Partially Accept"
    assert not {"Accept", "Partially Accept", "Reject"} & set(run["review"]["recommendations"])


def test_validate_review_rejects_unknown_recommendation():
    _REVIEW_CONTEXT.work = None  # an earlier run in this thread may have left its work here
    wf = ActivityScheduleCheckWorkflow()
    with pytest.raises(ValueError):
        wf.validate_review({"risk": "low", "findings": [], "recommendations": [], "recommendation": "Approve"})
    ok = wf.validate_review({"risk": "low", "findings": [], "recommendations": [], "recommendation": "Reject"})
    assert ok["correction"] == "" and ok["retest"] == ""


def test_work_output_is_json_serialisable(activity):
    client, _, _, _ = activity
    run = start(client, date="2026-10-06")[1]["result"]
    json.dumps(run["work"])
