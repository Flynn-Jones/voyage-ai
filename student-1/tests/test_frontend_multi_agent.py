"""Offline tests for the frontend /multi-agent pages, the PUBLIC_* navigation
settings and the /compare disabled message. The backend is faked by replacing
fe.requests.request; nothing touches the network."""
import importlib.util
import os
import sys

import pytest
import requests

MODULE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "app.py")


def _load():
    spec = importlib.util.spec_from_file_location("destination_frontend_ma_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_frontend_ma_app"] = module
    spec.loader.exec_module(module)
    return module


fe = _load()
fe.app.testing = True
HTMX = {"HX-Request": "true"}
BASE = "/api/destinations/multi-agent/runs"

ITEM = {"id": 3, "destination_id": 3, "city": "Osaka", "country": "Japan", "average_daily_cost": 125.0,
        "recommended_trip_length": 3, "matched_interests": ["food", "nightlife"], "within_budget": True,
        "estimated_trip_cost": 375.0, "score": 5.0, "evidence_ref": "destination-db:/destinations/3"}
ITEM2 = {**ITEM, "id": 5, "destination_id": 5, "city": "Bangkok", "country": "Thailand",
         "evidence_ref": "destination-db:/destinations/5"}
RUN = {
    "run_id": "run1", "workflow": "destination_recommend", "state": "awaiting_human_review",
    "requested_by": "destination-backend", "created_at": "2026-10-22T01:00:00Z", "error": None,
    "plan": {"steps": [{"tool": "list_destinations", "reason": "Destination: candidates"}],
             "rationale": "deterministic fallback: read every allowed source"},
    "work": {"items": [ITEM, ITEM2], "summary": "10 destination(s) considered, 2 recommended.",
             "evidence": [{"source": "list_destinations", "status": "ok", "detail": "10 of 10 destination(s) considered"}],
             "checks": []},
    "review": {"risk": "low", "findings": [], "recommendations": ["Looks consistent with the interests."]},
    "decision": None, "applied": None,
}
HISTORY = [
    {"seq": 1, "ts": "t1", "step": "created", "agent": None, "state": "running", "detail": {"requested_by": "destination-backend"}},
    {"seq": 2, "ts": "t2", "step": "plan", "agent": "planner", "state": "running", "detail": {"source": "fallback"}},
    {"seq": 3, "ts": "t3", "step": "work", "agent": "worker", "state": "running", "detail": {"tools_called": ["list_destinations"]}},
    {"seq": 4, "ts": "t4", "step": "review", "agent": "reviewer", "state": "running", "detail": {"source": "fallback"}},
]


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def ok(result, status=200):
    return Resp(status, {"status": "success", "result": result})


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.routes = {}
        self.exc = None

    def __call__(self, method, url, json=None, params=None, timeout=None, **kw):
        path = url[len(fe.BACKEND_SERVICE_URL):]
        self.calls.append({"method": method, "path": path, "json": json, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.routes[(method, path)]

    def sent(self, method, path):
        return [c["json"] for c in self.calls if (c["method"], c["path"]) == (method, path)]


@pytest.fixture
def client():
    return fe.app.test_client()


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend()
    fake.routes[("GET", f"{BASE}?limit=10")] = ok([{**RUN}])
    fake.routes[("GET", f"{BASE}/run1")] = ok(RUN)
    fake.routes[("GET", f"{BASE}/run1/history")] = ok(HISTORY)
    monkeypatch.setattr(fe.requests, "request", fake)
    return fake


# ---------------------------------------------------------------- page + planner/worker/reviewer output

def test_page_lists_recent_runs_and_the_start_form(client, backend):
    r = client.get("/multi-agent")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'name="interests"' in html and 'name="max_daily_cost"' in html
    assert "run1" in html and "/multi-agent/runs/run1" in html


def test_run_detail_shows_planner_worker_reviewer_history_and_decision_form(client, backend):
    html = client.get("/multi-agent/runs/run1").get_data(as_text=True)
    assert "1. Planner" in html and "deterministic fallback" in html and "list_destinations" in html
    assert "2. Worker" in html and "Osaka" in html and "destination-db:/destinations/3" in html
    assert "3. Reviewer" in html and "Looks consistent with the interests." in html
    assert "4. Human Review" in html
    for decision in ("approve", "partial", "correct", "reject"):
        assert f'name="decision" value="{decision}"' in html
    assert 'name="approved_ids" value="3"' in html and 'name="correction_5"' in html
    assert "History" in html and "created" in html and "tools_called" in html


def test_decided_run_hides_the_form_and_shows_the_record(client, backend):
    decided = {**RUN, "state": "applied", "decision": {
        "decision": "partial", "reviewer": "flynn", "note": "keep Osaka", "approved_ids": ["3"],
        "rejected_ids": ["5"], "edits": {}}, "applied": {"target": "none (read-only destination_recommend)"}}
    backend.routes[("GET", f"{BASE}/run1")] = ok(decided)
    html = client.get("/multi-agent/runs/run1").get_data(as_text=True)
    assert 'name="decision"' not in html
    assert "partial" in html and "flynn" in html and "keep Osaka" in html and "no destination was changed" in html


# ---------------------------------------------------------------- start

def test_start_parses_the_form_and_redirects_to_the_run(client, backend):
    backend.routes[("POST", BASE)] = ok({"run_id": "run1"}, 201)
    r = client.post("/multi-agent/run", data={
        "interests": " food, nightlife ,", "max_daily_cost": "130", "trip_days": "4", "country": " Japan "})
    assert r.status_code == 302 and r.headers["Location"].endswith("/multi-agent/runs/run1")
    assert backend.sent("POST", BASE) == [
        {"interests": ["food", "nightlife"], "max_daily_cost": 130.0, "trip_days": 4, "country": "Japan"}]
    assert backend.calls[0]["timeout"] == fe.MULTI_AGENT_REQUEST_TIMEOUT


def test_start_htmx_returns_the_run_partial(client, backend):
    backend.routes[("POST", BASE)] = ok({"run_id": "run1"}, 201)
    r = client.post("/multi-agent/run", data={"interests": "food"}, headers=HTMX)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "<html" not in html and "1. Planner" in html


def test_blank_numbers_are_omitted_and_bad_numbers_are_forwarded_for_server_validation(client, backend):
    backend.routes[("POST", BASE)] = Resp(400, {"status": "error", "error": "invalid input: max_daily_cost must be a number"})
    r = client.post("/multi-agent/run", data={"interests": "food", "max_daily_cost": "abc", "trip_days": " "},
                    headers=HTMX)
    assert backend.sent("POST", BASE) == [{"interests": ["food"], "max_daily_cost": "abc"}]
    assert "max_daily_cost must be a number" in r.get_data(as_text=True)


def test_failed_run_error_dict_message_is_displayed(client, backend):
    backend.routes[("POST", BASE)] = Resp(500, {"status": "error", "error": {"message": "run x failed during work", "run_id": "x"}})
    html = client.post("/multi-agent/run", data={"interests": "food"}).get_data(as_text=True)
    assert "run x failed during work" in html


# ---------------------------------------------------------------- human review mapping

def _review_sent(backend, form):
    backend.routes[("POST", f"{BASE}/run1/review")] = ok({"run_id": "run1", "state": "approved"})
    return client_post_review(form), backend.sent("POST", f"{BASE}/run1/review")[0]


def client_post_review(form):
    return fe.app.test_client().post("/multi-agent/runs/run1/review", data=form, headers=HTMX)


def test_approve_sends_no_ids(backend):
    r, sent = _review_sent(backend, {"decision": "approve", "reviewer": " flynn ", "note": " ok ",
                                     "item_id": ["3", "5"], "approved_ids": ["3", "5"]})
    assert sent == {"decision": "approve", "note": "ok", "reviewer": "flynn"}
    assert "Decision recorded" in r.get_data(as_text=True)


def test_partial_sends_ticked_ids_and_edits_only_for_ticked_items(backend):
    _, sent = _review_sent(backend, {"decision": "partial", "item_id": ["3", "5"], "approved_ids": ["3"],
                                     "correction_3": "cheaper please", "correction_5": "ignored"})
    assert sent["approved_ids"] == ["3"]
    assert sent["edits"] == {"3": {"reviewer_correction": "cheaper please"}}
    assert "rejected_ids" not in sent


def test_correct_rejects_unticked_items(backend):
    _, sent = _review_sent(backend, {"decision": "correct", "item_id": ["3", "5"], "approved_ids": ["3"],
                                     "correction_3": "swap budget"})
    assert sent["approved_ids"] == ["3"] and sent["rejected_ids"] == ["5"]
    assert sent["edits"] == {"3": {"reviewer_correction": "swap budget"}}


def test_reject_sends_only_the_decision(backend):
    _, sent = _review_sent(backend, {"decision": "reject", "item_id": ["3"], "approved_ids": ["3"], "note": "no"})
    assert sent == {"decision": "reject", "note": "no", "reviewer": None}


def test_conflict_is_shown_and_the_run_is_reloaded(backend):
    backend.routes[("POST", f"{BASE}/run1/review")] = Resp(409, {"status": "error", "error": "run run1 was already reviewed"})
    html = client_post_review({"decision": "approve"}).get_data(as_text=True)
    assert "already reviewed" in html and "1. Planner" in html


def test_review_without_htmx_renders_the_full_page(backend):
    backend.routes[("POST", f"{BASE}/run1/review")] = ok({"run_id": "run1", "state": "approved"})
    backend.routes[("GET", f"{BASE}?limit=10")] = ok([])
    r = fe.app.test_client().post("/multi-agent/runs/run1/review", data={"decision": "approve"})
    assert r.status_code == 200 and "<html" in r.get_data(as_text=True)


# ---------------------------------------------------------------- disabled / unavailable / timeout

def test_disabled_shows_a_message_and_no_form(client, backend):
    backend.routes[("GET", f"{BASE}?limit=10")] = Resp(503, {"status": "disabled", "error": "Multi-Agent mode is disabled."})
    html = client.get("/multi-agent").get_data(as_text=True)
    assert "Multi-Agent mode is disabled for Destination Manager." in html
    assert 'name="interests"' not in html


@pytest.mark.parametrize("exc,text", [
    (requests.exceptions.ConnectionError("refused"), fe.UNAVAILABLE_MESSAGE),
    (requests.exceptions.Timeout("slow"), "timed out"),
])
def test_unavailable_and_timeout_render_messages_not_tracebacks(client, backend, exc, text):
    backend.exc = exc
    r = client.get("/multi-agent")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and text in html
    assert "refused" not in html and "Traceback" not in html
    html = client.post("/multi-agent/run", data={"interests": "food"}).get_data(as_text=True)
    assert text in html


def test_malformed_backend_reply_is_unavailable(client, backend):
    backend.routes[("GET", f"{BASE}?limit=10")] = Resp(200, None)
    assert fe.UNAVAILABLE_MESSAGE in client.get("/multi-agent").get_data(as_text=True)


def test_run_id_is_percent_encoded_before_it_reaches_the_backend(client, backend):
    backend.routes[("GET", f"{BASE}/x%3Fy")] = Resp(400, {"status": "invalid_request", "error": "run_id is invalid"})
    html = client.get("/multi-agent/runs/x%3Fy").get_data(as_text=True)
    assert "run_id is invalid" in html
    assert [c["path"] for c in backend.calls if "x" in c["path"]] == [f"{BASE}/x%3Fy"]


# ---------------------------------------------------------------- compare disabled + CRUD unaffected

def test_compare_shows_disabled_message_not_unavailable(client, monkeypatch):
    def fake(method, url, **kw):
        if url.endswith("/api/destinations"):
            return Resp(200, [{"destination_id": 1, "city": "Tokyo", "country": "Japan"}])
        return Resp(503, {"status": "disabled", "error": "AI comparison is disabled."})
    monkeypatch.setattr(fe.requests, "request", fake)
    html = client.post("/compare", data={"city_a": "Tokyo", "city_b": "Kyoto"}).get_data(as_text=True)
    assert "AI comparison is disabled." in html and fe.UNAVAILABLE_MESSAGE not in html


def test_other_503s_stay_unavailable(client, monkeypatch):
    def fake(method, url, **kw):
        return Resp(503, {"error": "destination database unavailable"})
    monkeypatch.setattr(fe.requests, "request", fake)
    assert fe.UNAVAILABLE_MESSAGE in client.get("/").get_data(as_text=True)


# ---------------------------------------------------------------- navigation addresses

DEFAULT_NAV = ["http://localhost:3000", "http://localhost:3001", "http://localhost:3002",
               "http://localhost:3003", "http://localhost:3004", "http://localhost:3005"]
NAV_ENV = ["PUBLIC_HOME_URL", "PUBLIC_DESTINATION_URL", "PUBLIC_ACCOMMODATION_URL",
           "PUBLIC_ACTIVITY_URL", "PUBLIC_BUDGET_URL", "PUBLIC_ITINERARY_URL"]


def _nav_hrefs(html):
    block = html.split('voyage-nav__switcher">')[1].split("</div>")[0]
    return [part.split('href="')[1].split('"')[0] for part in block.split("<a ")[1:]]


@pytest.fixture
def plain_backend(monkeypatch):
    monkeypatch.setattr(fe.requests, "request", lambda *a, **k: Resp(200, []))


def test_default_navigation_is_unchanged(client, plain_backend, monkeypatch):
    for name in NAV_ENV:
        monkeypatch.delenv(name, raising=False)
    assert _nav_hrefs(client.get("/").get_data(as_text=True)) == DEFAULT_NAV


def test_public_urls_override_the_navigation(client, plain_backend, monkeypatch):
    for name in NAV_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PUBLIC_HOME_URL", "https://voyage.example.com/")
    monkeypatch.setenv("PUBLIC_BUDGET_URL", "/budget")
    hrefs = _nav_hrefs(client.get("/").get_data(as_text=True))
    assert hrefs[0] == "https://voyage.example.com/" and hrefs[4] == "/budget"
    assert hrefs[1:4] == DEFAULT_NAV[1:4] and hrefs[5] == DEFAULT_NAV[5]


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "//evil.example.com", "ftp://x", "  "])
def test_unsafe_or_blank_public_urls_fall_back_to_defaults(client, plain_backend, monkeypatch, bad):
    monkeypatch.setenv("PUBLIC_HOME_URL", bad)
    assert _nav_hrefs(client.get("/").get_data(as_text=True))[0] == DEFAULT_NAV[0]
