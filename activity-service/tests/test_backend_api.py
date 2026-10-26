"""Tests for activity-service/backend: the CRUD routes (activity_routes.py)
and the AI chat/summary routes, including the Plan/Act/Observe/Adapt loop's
individual steps in services/ai_client.py.

Run with the rest of the suite, from activity-service/: python -m pytest
"""
import pytest

from services import ai_client

from conftest import DummyResponse


# This file's tests use `client` for the backend tier's test client.
@pytest.fixture
def client(backend_client):
    return backend_client


# ---------------------------------------------------------------------------
# CRUD: add / view / edit / delete
# ---------------------------------------------------------------------------

def test_add_activity_success_201(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "request",
        lambda method, url, json=None, timeout=5: DummyResponse(201, {
            "activity_id": 1, "activity_name": json["activity_name"], "activity_type": json["activity_type"],
            "activity_cost": float(json["activity_cost"]), "duration": json["duration"],
        }),
    )
    resp = client.post("/api/add_activity", json={
        "activity_name": "Kayaking",
        "activity_type": "Adventure",
        "activity_cost": 35,
        "duration": "1.5 hours",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["activity_name"] == "Kayaking"
    assert body["activity_cost"] == 35.0
    assert "activity_id" in body


def test_add_activity_missing_required_field_400(client):
    resp = client.post("/api/add_activity", json={"activity_name": "Kayaking"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_add_activity_invalid_cost_400(client):
    resp = client.post("/api/add_activity", json={
        "activity_name": "Kayaking",
        "activity_type": "Adventure",
        "activity_cost": "free",
        "duration": "1 hour",
    })
    assert resp.status_code == 400


def test_add_activity_database_unavailable_502(client, monkeypatch):
    from services import db_client

    def boom(method, url, json=None, timeout=5):
        raise db_client.requests.exceptions.RequestException("connection refused")

    monkeypatch.setattr(db_client.requests, "request", boom)
    resp = client.post("/api/add_activity", json={
        "activity_name": "Kayaking", "activity_type": "Adventure", "activity_cost": 35, "duration": "1.5 hours",
    })
    assert resp.status_code == 502


def test_view_activities_success(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "get",
        lambda url, timeout=5: DummyResponse(200, [{"activity_id": 1, "activity_name": "X"}]),
    )
    resp = client.get("/api/view_activities")
    assert resp.status_code == 200
    assert resp.get_json() == [{"activity_id": 1, "activity_name": "X"}]


def test_view_activities_database_unavailable_502(client, monkeypatch):
    from services import db_client

    def boom(url, timeout=5):
        raise db_client.requests.exceptions.RequestException("connection refused")

    monkeypatch.setattr(db_client.requests, "get", boom)
    resp = client.get("/api/view_activities")
    assert resp.status_code == 502


FILTER_ACTIVITIES = [
    {"activity_id": 1, "activity_name": "Kayaking", "activity_type": "Adventure",
     "activity_cost": 45.0, "duration": "2 hours"},
    {"activity_id": 2, "activity_name": "Food Walk", "activity_type": "Food",
     "activity_cost": 60.0, "duration": "3 hours"},
    {"activity_id": 3, "activity_name": "Day Spa", "activity_type": "Wellness",
     "activity_cost": 120.0, "duration": "full day"},
    {"activity_id": 4, "activity_name": "Mystery Tour", "activity_type": "Adventure",
     "activity_cost": 30.0, "duration": "it depends"},
]
FILTER_ASSIGNMENTS = [
    {"assignment_id": 1, "activity_id": 1, "assignment_time": "2026-10-02 09:00"},
    {"assignment_id": 2, "activity_id": 2, "assignment_time": "2026-10-05 18:00"},
    {"assignment_id": 3, "activity_id": 3, "assignment_time": "2026-10-09 10:00"},
]


@pytest.fixture
def filter_db(monkeypatch):
    from services import db_client

    def fake_get(url, timeout=5):
        if url.endswith("/assignments"):
            return DummyResponse(200, FILTER_ASSIGNMENTS)
        return DummyResponse(200, FILTER_ACTIVITIES)

    monkeypatch.setattr(db_client.requests, "get", fake_get)


def _names(resp):
    assert resp.status_code == 200, resp.get_json()
    return [a["activity_name"] for a in resp.get_json()]


def test_view_activities_include_dates_adds_assignment_time(client, filter_db):
    resp = client.get("/api/view_activities?include_dates=1")
    by_name = {a["activity_name"]: a["assignment_time"] for a in resp.get_json()}
    assert by_name == {"Kayaking": "2026-10-02 09:00", "Food Walk": "2026-10-05 18:00",
                       "Day Spa": "2026-10-09 10:00", "Mystery Tour": None}


def test_view_activities_filters_by_search_and_type(client, filter_db):
    assert _names(client.get("/api/view_activities?q=walk")) == ["Food Walk"]
    assert _names(client.get("/api/view_activities?type=adventure")) == ["Kayaking", "Mystery Tour"]


def test_view_activities_filters_by_cost_range(client, filter_db):
    assert _names(client.get("/api/view_activities?min_cost=40&max_cost=60")) == ["Kayaking", "Food Walk"]


def test_view_activities_filters_by_duration_keeps_unparseable(client, filter_db):
    # "full day" parses to 8h and is dropped; "it depends" can't be parsed and fails open.
    assert _names(client.get("/api/view_activities?min_duration=2&max_duration=4")) == [
        "Kayaking", "Food Walk", "Mystery Tour",
    ]


def test_view_activities_filters_by_date_range_drops_undated(client, filter_db):
    assert _names(client.get("/api/view_activities?date_from=2026-10-03&date_to=2026-10-09")) == [
        "Food Walk", "Day Spa",
    ]


def test_view_activities_sorts(client, filter_db):
    assert _names(client.get("/api/view_activities?sort=cost_desc")) == [
        "Day Spa", "Food Walk", "Kayaking", "Mystery Tour",
    ]
    assert _names(client.get("/api/view_activities?sort=date_asc"))[-1] == "Mystery Tour"


@pytest.mark.parametrize("query", ["min_cost=cheap", "max_duration=-1", "date_from=02/10/2026", "sort=random"])
def test_view_activities_bad_filter_400(client, filter_db, query):
    resp = client.get(f"/api/view_activities?{query}")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_view_activity_known_id_200(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "get",
        lambda url, timeout=5: DummyResponse(200, {"activity_id": 1, "activity_name": "Kayaking"}),
    )
    resp = client.get("/api/view_activity/1")
    assert resp.status_code == 200
    assert resp.get_json()["activity_name"] == "Kayaking"


def test_view_activity_unknown_id_404(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "get",
        lambda url, timeout=5: DummyResponse(404, {"error": "activity not found"}),
    )
    resp = client.get("/api/view_activity/999")
    assert resp.status_code == 404


def test_edit_activity_success_200(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "request",
        lambda method, url, json=None, timeout=5: DummyResponse(200, {
            "activity_id": 1, "activity_name": "Kayaking", "activity_type": "Adventure",
            "activity_cost": 40.0, "duration": "1.5 hours",
        }),
    )
    resp = client.patch("/api/edit_activity/1", json={"activity_cost": 40})
    assert resp.status_code == 200
    assert resp.get_json()["activity_cost"] == 40.0


def test_edit_activity_not_found_404(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "request",
        lambda method, url, json=None, timeout=5: DummyResponse(404, {"error": "activity not found"}),
    )
    resp = client.patch("/api/edit_activity/999", json={"activity_cost": 40})
    assert resp.status_code == 404


def test_edit_activity_invalid_cost_400(client):
    resp = client.patch("/api/edit_activity/1", json={"activity_cost": "free"})
    assert resp.status_code == 400


def test_edit_activity_database_unavailable_502(client, monkeypatch):
    from services import db_client

    def boom(method, url, json=None, timeout=5):
        raise db_client.requests.exceptions.RequestException("connection refused")

    monkeypatch.setattr(db_client.requests, "request", boom)
    resp = client.patch("/api/edit_activity/1", json={"activity_cost": 40})
    assert resp.status_code == 502


def test_delete_activity_success_200(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "request",
        lambda method, url, json=None, timeout=5: DummyResponse(200, {"message": "deleted"}),
    )
    resp = client.delete("/api/delete_activity/1")
    assert resp.status_code == 200
    assert resp.get_json()["message"] == "deleted"


def test_delete_activity_not_found_404(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "request",
        lambda method, url, json=None, timeout=5: DummyResponse(404, {"error": "activity not found"}),
    )
    resp = client.delete("/api/delete_activity/999")
    assert resp.status_code == 404


def test_delete_activity_database_unavailable_502(client, monkeypatch):
    from services import db_client

    def boom(method, url, json=None, timeout=5):
        raise db_client.requests.exceptions.RequestException("connection refused")

    monkeypatch.setattr(db_client.requests, "request", boom)
    resp = client.delete("/api/delete_activity/1")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# AI summary (single grounded call, per-activity)
# ---------------------------------------------------------------------------

def test_ai_summary_success(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "get",
        lambda url, timeout=5: DummyResponse(200, {"activity_id": 1, "activity_name": "Kayaking"}),
    )
    monkeypatch.setattr(ai_client, "generate_summary", lambda activity: "A fun kayaking trip.")

    resp = client.post("/api/activity/ai-summary", json={"activity_id": 1})
    assert resp.status_code == 200
    assert resp.get_json() == {"summary": "A fun kayaking trip."}


def test_ai_summary_missing_activity_id_400(client):
    resp = client.post("/api/activity/ai-summary", json={})
    assert resp.status_code == 400


def test_ai_summary_activity_not_found_404(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "get",
        lambda url, timeout=5: DummyResponse(404, {"error": "not found"}),
    )
    resp = client.post("/api/activity/ai-summary", json={"activity_id": 999})
    assert resp.status_code == 404


def test_ai_summary_ai_service_unavailable_502(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(
        db_client.requests, "get",
        lambda url, timeout=5: DummyResponse(200, {"activity_id": 1}),
    )

    def boom(activity):
        raise ai_client.AIServiceError("ollama unreachable")

    monkeypatch.setattr(ai_client, "generate_summary", boom)
    resp = client.post("/api/activity/ai-summary", json={"activity_id": 1})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# AI chat: the Plan -> Act -> Observe -> Adapt loop, end to end through the
# route (Plan and the final Adapt LLM call are mocked; Act's real filtering
# and Observe's real classification run unmocked against fake activity data,
# since those are the parts this loop is actually meant to prove).
# ---------------------------------------------------------------------------

def test_ai_chat_missing_message_400(client):
    resp = client.post("/api/activity/ai-chat", json={})
    assert resp.status_code == 400


def test_ai_chat_single_match_grounds_reply_in_real_data(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(db_client, "get_activities", lambda: [
        {"activity_id": 1, "activity_name": "Aquarium Visit", "activity_type": "Family",
         "activity_cost": 22, "duration": "2 hours"},
    ])
    monkeypatch.setattr(
        ai_client, "extract_intent",
        lambda message: ({"max_cost": None, "max_duration_hours": None, "category": None,
                           "keywords": ["aquarium"]}, False),
    )
    monkeypatch.setattr(
        ai_client, "generate_grounded_reply",
        lambda message, candidates: "Aquarium Visit costs $22 for 2 hours.",
    )

    resp = client.post("/api/activity/ai-chat", json={"message": "tell me about the aquarium"})
    assert resp.status_code == 200
    assert resp.get_json() == {"reply": "Aquarium Visit costs $22 for 2 hours."}


def test_ai_chat_zero_matches_asks_for_clarification_not_invented_activity(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(db_client, "get_activities", lambda: [
        {"activity_id": 1, "activity_name": "Aquarium Visit", "activity_type": "Family",
         "activity_cost": 22, "duration": "2 hours"},
    ])
    monkeypatch.setattr(
        ai_client, "extract_intent",
        lambda message: ({"max_cost": 2, "max_duration_hours": None, "category": None, "keywords": []}, False),
    )

    resp = client.post("/api/activity/ai-chat", json={"message": "something under $2"})
    assert resp.status_code == 200
    reply = resp.get_json()["reply"]
    assert "Nothing matched" in reply
    assert "$2" in reply
    assert "Aquarium" not in reply  # must not invent/mention an activity that didn't match


def test_ai_chat_many_matches_asks_to_narrow_instead_of_guessing(client, monkeypatch):
    from services import db_client
    activities = [
        {"activity_id": i, "activity_name": f"Activity {i}", "activity_type": "Test",
         "activity_cost": 10, "duration": "1 hour"}
        for i in range(6)
    ]
    monkeypatch.setattr(db_client, "get_activities", lambda: activities)
    monkeypatch.setattr(ai_client, "extract_intent", lambda message: (dict(ai_client.DEFAULT_INTENT), False))

    resp = client.post("/api/activity/ai-chat", json={"message": "anything"})
    assert resp.status_code == 200
    reply = resp.get_json()["reply"]
    assert "6 activities" in reply
    assert "narrow it down" in reply


def test_ai_chat_intent_extraction_unavailable_502(client, monkeypatch):
    def boom(message):
        raise ai_client.AIServiceError("ollama unreachable")

    monkeypatch.setattr(ai_client, "extract_intent", boom)
    resp = client.post("/api/activity/ai-chat", json={"message": "anything"})
    assert resp.status_code == 502


def test_ai_chat_activity_database_unavailable_502(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(ai_client, "extract_intent", lambda message: (dict(ai_client.DEFAULT_INTENT), False))

    def boom():
        raise db_client.DatabaseServiceError("connection refused")

    monkeypatch.setattr(db_client, "get_activities", boom)
    resp = client.post("/api/activity/ai-chat", json={"message": "anything"})
    assert resp.status_code == 502


def test_ai_chat_grounded_reply_generation_unavailable_502(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(db_client, "get_activities", lambda: [
        {"activity_id": 1, "activity_name": "X", "activity_type": "Y", "activity_cost": 1, "duration": "1 hour"},
    ])
    monkeypatch.setattr(ai_client, "extract_intent", lambda message: (dict(ai_client.DEFAULT_INTENT), False))

    def boom(message, candidates):
        raise ai_client.AIServiceError("ollama unreachable")

    monkeypatch.setattr(ai_client, "generate_grounded_reply", boom)
    resp = client.post("/api/activity/ai-chat", json={"message": "anything"})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# AI_ENABLED gate: both AI routes refuse before touching the database or Ollama
# ---------------------------------------------------------------------------

AI_ROUTES = [
    ("/api/activity/ai-summary", {"activity_id": 1}),
    ("/api/activity/ai-chat", {"message": "anything"}),
]


def _forbid_upstream(monkeypatch):
    """Any database-tier or Ollama call fails the test; returns the call log."""
    from services import db_client
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("upstream called while AI is disabled")

    monkeypatch.setattr(db_client.requests, "get", forbidden)
    monkeypatch.setattr(db_client.requests, "request", forbidden)
    monkeypatch.setattr(ai_client.requests, "post", forbidden)
    for name in ("get_activity", "get_activities"):
        monkeypatch.setattr(db_client, name, forbidden)
    for name in ("generate_summary", "extract_intent", "generate_grounded_reply"):
        monkeypatch.setattr(ai_client, name, forbidden)
    return calls


@pytest.mark.parametrize("path, body", AI_ROUTES)
def test_ai_routes_disabled_is_403_without_upstream_calls(client, monkeypatch, path, body):
    monkeypatch.setattr(ai_client, "AI_ENABLED", False)
    calls = _forbid_upstream(monkeypatch)

    resp = client.post(path, json=body)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "AI mode is disabled."}
    assert calls == []


@pytest.mark.parametrize("path", [p for p, _ in AI_ROUTES])
def test_ai_routes_disabled_refuses_even_an_invalid_body(client, monkeypatch, path):
    monkeypatch.setattr(ai_client, "AI_ENABLED", False)
    _forbid_upstream(monkeypatch)
    assert client.post(path, json={}).status_code == 403


def test_ai_summary_enabled_path_still_works(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(ai_client, "AI_ENABLED", True)
    monkeypatch.setattr(
        db_client.requests, "get",
        lambda url, timeout=5: DummyResponse(200, {"activity_id": 1, "activity_name": "Kayaking"}),
    )
    monkeypatch.setattr(ai_client, "generate_summary", lambda activity: "A fun kayaking trip.")

    resp = client.post("/api/activity/ai-summary", json={"activity_id": 1})
    assert resp.status_code == 200
    assert resp.get_json() == {"summary": "A fun kayaking trip."}


def test_ai_chat_enabled_path_still_works(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(ai_client, "AI_ENABLED", True)
    monkeypatch.setattr(db_client, "get_activities", lambda: [
        {"activity_id": 1, "activity_name": "Aquarium Visit", "activity_type": "Family",
         "activity_cost": 22, "duration": "2 hours"},
    ])
    monkeypatch.setattr(
        ai_client, "extract_intent",
        lambda message: ({"max_cost": None, "max_duration_hours": None, "category": None,
                           "keywords": ["aquarium"]}, False),
    )
    monkeypatch.setattr(ai_client, "generate_grounded_reply", lambda message, candidates: "Aquarium Visit: $22.")

    resp = client.post("/api/activity/ai-chat", json={"message": "tell me about the aquarium"})
    assert resp.status_code == 200
    assert resp.get_json() == {"reply": "Aquarium Visit: $22."}


def test_crud_stays_available_when_ai_is_disabled(client, monkeypatch):
    from services import db_client
    monkeypatch.setattr(ai_client, "AI_ENABLED", False)
    monkeypatch.setattr(
        db_client.requests, "get",
        lambda url, timeout=5: DummyResponse(200, [{"activity_id": 1, "activity_name": "Kayaking"}]),
    )
    resp = client.get("/api/view_activities")
    assert resp.status_code == 200
    assert resp.get_json() == [{"activity_id": 1, "activity_name": "Kayaking"}]


# ---------------------------------------------------------------------------
# ai_client's individual loop steps, unit-tested directly — "genuinely
# separate and inspectable", not just exercised indirectly through the route.
# ---------------------------------------------------------------------------

def test_extract_intent_falls_back_on_unparseable_output(monkeypatch):
    monkeypatch.setattr(ai_client, "_generate", lambda prompt, temperature=None, model=None: "not json at all")
    intent, used_fallback = ai_client.extract_intent("anything")
    assert used_fallback is True
    assert intent == ai_client.DEFAULT_INTENT


def test_extract_intent_parses_real_json_shape(monkeypatch):
    monkeypatch.setattr(
        ai_client, "_generate",
        lambda prompt, temperature=None, model=None: '{"max_cost": 50, "max_duration_hours": 4, "category": null, "keywords": []}',
    )
    intent, used_fallback = ai_client.extract_intent("half-day under $50")
    assert used_fallback is False
    assert intent == {"max_cost": 50, "max_duration_hours": 4, "category": None, "keywords": []}


def test_filter_candidates_respects_max_cost():
    activities = [
        {"activity_name": "Cheap", "activity_cost": 10, "duration": "1 hour"},
        {"activity_name": "Expensive", "activity_cost": 100, "duration": "1 hour"},
    ]
    intent = {"max_cost": 50, "max_duration_hours": None, "category": None, "keywords": []}
    result = ai_client.filter_candidates(activities, intent)
    assert [a["activity_name"] for a in result] == ["Cheap"]


def test_filter_candidates_unparseable_duration_fails_open():
    activities = [{"activity_name": "Mystery", "activity_cost": 10, "duration": "a mysterious while"}]
    intent = {"max_cost": None, "max_duration_hours": 2, "category": None, "keywords": []}
    result = ai_client.filter_candidates(activities, intent)
    assert len(result) == 1  # kept, not dropped, since duration text couldn't be parsed


def test_filter_candidates_matches_keywords_case_insensitively():
    activities = [
        {"activity_name": "AQUARIUM Visit", "activity_type": "Family", "activity_cost": 22, "duration": "2 hours"},
        {"activity_name": "Kart Racing", "activity_type": "Sports", "activity_cost": 40, "duration": "1 hour"},
    ]
    intent = {"max_cost": None, "max_duration_hours": None, "category": None, "keywords": ["aquarium"]}
    result = ai_client.filter_candidates(activities, intent)
    assert [a["activity_name"] for a in result] == ["AQUARIUM Visit"]


def test_classify_candidates_thresholds():
    assert ai_client.classify_candidates([])["status"] == "none"
    assert ai_client.classify_candidates([{}] * 3)["status"] == "single"
    assert ai_client.classify_candidates([{}] * 6)["status"] == "many"


def test_format_no_match_reply_fallback_message_differs_from_constrained():
    fallback_reply = ai_client.format_no_match_reply(ai_client.DEFAULT_INTENT, used_fallback=True)
    constrained_reply = ai_client.format_no_match_reply(
        {"max_cost": 5, "max_duration_hours": None, "category": None, "keywords": []}, used_fallback=False,
    )
    assert "rephrasing" in fallback_reply
    assert "$5" in constrained_reply
    assert fallback_reply != constrained_reply
