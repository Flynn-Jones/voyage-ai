from app import create_app


class DummyResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._payload


RUN_RESULT = {
    "run_id": "run-abc123",
    "state": "awaiting_human_review",
    "plan": {"steps": [{"tool": "search_accommodations", "reason": "find candidates"}],
             "rationale": "start with the filtered search"},
    "work": {
        "summary": "Tokyo: 1 candidate(s) considered, 1 shortlisted.",
        "items": [{"id": 1, "name": "Shinjuku Granbell Hotel", "price_per_night": 120.0,
                   "rating": 4.3, "matched_interests": ["pool"], "within_budget": True,
                   "score": 6.3, "accommodation_id": 1, "evidence_ref": "accommodation-db:/accommodations/1"}],
        "evidence": [{"source": "search_accommodations", "feature": "Accommodation",
                      "status": "ok", "detail": "1 candidate(s)"}],
    },
    "review": {"risk": "low", "findings": [], "recommendations": []},
}


def test_get_renders_the_run_form(monkeypatch):
    monkeypatch.setattr(
        "app.requests.request",
        lambda method, url, params=None, json=None, timeout=None: DummyResponse(200, []),
    )
    app = create_app()
    with app.test_client() as client:
        response = client.get("/multi-agent")
    assert response.status_code == 200
    assert b'name="destination"' in response.data
    assert b'name="budget_cap"' in response.data


def test_run_success_renders_shortlist(monkeypatch):
    captured = {}

    def fake_request(method, url, params=None, json=None, timeout=None):
        if url.endswith("/destinations"):
            return DummyResponse(200, [])
        if url.endswith("/history"):
            return DummyResponse(200, {"status": "success", "result": []})
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        return DummyResponse(201, {"status": "success", "result": RUN_RESULT})

    monkeypatch.setattr("app.requests.request", fake_request)
    app = create_app()
    with app.test_client() as client:
        response = client.post("/multi-agent", data={
            "action": "run", "destination": "Tokyo", "budget_cap": "150", "interests": "pool",
        })

    assert response.status_code == 200
    assert captured["url"].endswith("/accommodation/multi-agent/run")
    assert captured["json"] == {"destination": "Tokyo", "budget_cap": 150.0, "interests": ["pool"]}
    assert b"Shinjuku Granbell Hotel" in response.data
    assert b"run-abc123" in response.data


def test_run_missing_destination_is_400(monkeypatch):
    monkeypatch.setattr(
        "app.requests.request",
        lambda method, url, params=None, json=None, timeout=None: DummyResponse(200, []),
    )
    app = create_app()
    with app.test_client() as client:
        response = client.post("/multi-agent", data={"action": "run", "budget_cap": "150"})
    assert response.status_code == 400
    assert b"Destination is required" in response.data


def test_run_invalid_budget_cap_is_400(monkeypatch):
    monkeypatch.setattr(
        "app.requests.request",
        lambda method, url, params=None, json=None, timeout=None: DummyResponse(200, []),
    )
    app = create_app()
    with app.test_client() as client:
        response = client.post("/multi-agent", data={
            "action": "run", "destination": "Tokyo", "budget_cap": "not-a-number",
        })
    assert response.status_code == 400
    assert b"Budget cap must be a positive number" in response.data


def test_run_disabled_shows_banner(monkeypatch):
    def fake_request(method, url, params=None, json=None, timeout=None):
        if url.endswith("/destinations"):
            return DummyResponse(200, [])
        return DummyResponse(403, {"status": "error", "error": "Multi-Agent mode is disabled (cloud default)."})

    monkeypatch.setattr("app.requests.request", fake_request)
    app = create_app()
    with app.test_client() as client:
        response = client.post("/multi-agent", data={
            "action": "run", "destination": "Tokyo", "budget_cap": "150",
        })
    assert response.status_code == 403
    assert b"Multi-Agent mode is disabled" in response.data


def test_review_partial_approval_sends_approved_ids(monkeypatch):
    captured = {}

    def fake_request(method, url, params=None, json=None, timeout=None):
        if url.endswith("/destinations"):
            return DummyResponse(200, [])
        if url.endswith("/history"):
            return DummyResponse(200, {"status": "success", "result": [
                {"step": "human_review", "agent": "human", "state": "partially_accepted", "ts": "2026-10-21T00:00:00Z"},
            ]})
        captured["url"] = url
        captured["json"] = json
        return DummyResponse(200, {"status": "success", "result": {
            "run_id": "run-abc123", "decision": "partial", "state": "applied",
            "approved_items": [{"id": 1, "name": "Shinjuku Granbell Hotel"}],
            "rejected_ids": [], "applied_recorded": True,
        }})

    monkeypatch.setattr("app.requests.request", fake_request)
    app = create_app()
    with app.test_client() as client:
        response = client.post("/multi-agent", data={
            "action": "review", "run_id": "run-abc123", "approved_ids": ["1"], "note": "looks good",
        })

    assert response.status_code == 200
    assert captured["url"].endswith("/accommodation/multi-agent/runs/run-abc123/review")
    assert captured["json"]["decision"] == "partial"
    assert captured["json"]["approved_ids"] == ["1"]
    assert b"Decision recorded" in response.data
    assert b"applied" in response.data


def test_review_with_nothing_checked_rejects(monkeypatch):
    captured = {}

    def fake_request(method, url, params=None, json=None, timeout=None):
        if url.endswith("/destinations"):
            return DummyResponse(200, [])
        if url.endswith("/history"):
            return DummyResponse(200, {"status": "success", "result": []})
        captured["json"] = json
        return DummyResponse(200, {"status": "success", "result": {
            "run_id": "run-abc123", "decision": "reject", "state": "rejected",
            "approved_items": [], "rejected_ids": ["1"], "applied_recorded": None,
        }})

    monkeypatch.setattr("app.requests.request", fake_request)
    app = create_app()
    with app.test_client() as client:
        response = client.post("/multi-agent", data={"action": "review", "run_id": "run-abc123"})

    assert response.status_code == 200
    assert captured["json"]["decision"] == "reject"
    assert b"rejected" in response.data
