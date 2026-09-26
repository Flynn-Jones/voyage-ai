"""Tests for activity-service/backend: the CRUD routes (activity_routes.py)
and the AI chat/summary routes, including the Plan/Act/Observe/Adapt loop's
individual steps in services/ai_client.py.

Run from anywhere with: python -m pytest activity-service/backend/tests
"""
from services import ai_client

from conftest import DummyResponse


# ---------------------------------------------------------------------------
# CRUD: add / view / edit / delete
# ---------------------------------------------------------------------------

def test_add_activity_success_201(client):
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


def test_edit_activity_success_200(client):
    created = client.post("/api/add_activity", json={
        "activity_name": "Kayaking", "activity_type": "Adventure",
        "activity_cost": 35, "duration": "1.5 hours",
    }).get_json()

    resp = client.patch(f"/api/edit_activity/{created['activity_id']}", json={"activity_cost": 40})
    assert resp.status_code == 200
    assert resp.get_json()["activity_cost"] == 40.0


def test_edit_activity_not_found_404(client):
    resp = client.patch("/api/edit_activity/999", json={"activity_cost": 40})
    assert resp.status_code == 404


def test_edit_activity_invalid_cost_400(client):
    created = client.post("/api/add_activity", json={
        "activity_name": "Kayaking", "activity_type": "Adventure",
        "activity_cost": 35, "duration": "1.5 hours",
    }).get_json()

    resp = client.patch(f"/api/edit_activity/{created['activity_id']}", json={"activity_cost": "free"})
    assert resp.status_code == 400


def test_delete_activity_success_200(client):
    created = client.post("/api/add_activity", json={
        "activity_name": "Kayaking", "activity_type": "Adventure",
        "activity_cost": 35, "duration": "1.5 hours",
    }).get_json()

    resp = client.delete(f"/api/delete_activity/{created['activity_id']}")
    assert resp.status_code == 200
    assert resp.get_json()["message"] == "deleted"


def test_delete_activity_not_found_404(client):
    resp = client.delete("/api/delete_activity/999")
    assert resp.status_code == 404


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
