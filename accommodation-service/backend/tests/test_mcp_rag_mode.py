import pytest

from app import create_app
from services import llm_client, mcp_api, rag_api


class DummyResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture
def client():
    app = create_app()
    with app.test_client() as test_client:
        yield test_client


# --- MCP mode ---

def test_mcp_by_destination_forwards_to_shared_server(monkeypatch, client):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return DummyResponse(200, {"status": "success", "result": {"destination": "Tokyo", "count": 1}})

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(mcp_api.requests, "post", fake_post)

    response = client.post("/accommodation/mcp/by-destination", json={"destination": "Tokyo"})

    assert response.status_code == 200
    assert captured["url"].endswith("/get_accommodation_by_destination")
    assert captured["json"] == {"destination": "Tokyo"}
    assert response.get_json()["result"]["count"] == 1


def test_mcp_requires_destination(monkeypatch, client):
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    response = client.post("/accommodation/mcp/by-destination", json={})
    assert response.status_code == 400


def test_mcp_returns_403_when_disabled(monkeypatch, client):
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", False)
    response = client.post("/accommodation/mcp/by-destination", json={"destination": "Tokyo"})
    assert response.status_code == 403
    assert response.get_json()["error"] == "MCP mode is disabled."


def test_mcp_returns_403_when_mode_header_off(monkeypatch, client):
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    response = client.post(
        "/accommodation/mcp/by-destination",
        json={"destination": "Tokyo"},
        headers={"X-MCP-Mode": "off"},
    )
    assert response.status_code == 403


def test_mcp_reports_502_when_shared_server_unreachable(monkeypatch, client):
    def fake_post(url, json=None, timeout=None):
        raise mcp_api.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(mcp_api.requests, "post", fake_post)

    response = client.post("/accommodation/mcp/by-destination", json={"destination": "Tokyo"})
    assert response.status_code == 502


# --- RAG mode ---

def test_rag_answer_returns_citations_and_confidence(monkeypatch, client):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return DummyResponse(200, {
            "status": "success",
            "answer": "Two Tokyo stays are logged under $150.",
            "citations": [{"chunk_id": "accommodation_1", "source_id": "accommodation-db:/accommodations",
                           "authority_tier": "tier_1"}],
            "confidence_category": "High",
        })

    monkeypatch.setattr(rag_api, "RAG_ENABLED", True)
    monkeypatch.setattr(rag_api.requests, "post", fake_post)

    response = client.post("/accommodation/rag/answer", json={"query": "Tokyo stays under $150?"})

    assert response.status_code == 200
    assert captured["url"].endswith("/answer")
    assert captured["json"]["caller"] == "accommodation-service"
    payload = response.get_json()
    assert payload["confidence_category"] == "High"
    assert payload["citations"][0]["authority_tier"] == "tier_1"


def test_rag_requires_query(monkeypatch, client):
    monkeypatch.setattr(rag_api, "RAG_ENABLED", True)
    response = client.post("/accommodation/rag/answer", json={})
    assert response.status_code == 400


@pytest.mark.parametrize("path", [
    "/accommodation/rag/refresh",
    "/accommodation/rag/retrieve",
    "/accommodation/rag/answer",
])
def test_rag_returns_403_when_disabled(monkeypatch, client, path):
    monkeypatch.setattr(rag_api, "RAG_ENABLED", False)
    response = client.post(path, json={"query": "anything"})
    assert response.status_code == 403
    assert response.get_json()["error"] == "RAG mode is disabled."


def test_rag_reports_502_when_shared_server_unreachable(monkeypatch, client):
    def fake_post(url, json=None, timeout=None):
        raise rag_api.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(rag_api, "RAG_ENABLED", True)
    monkeypatch.setattr(rag_api.requests, "post", fake_post)

    response = client.post("/accommodation/rag/answer", json={"query": "Tokyo"})
    assert response.status_code == 502


# --- MCP search: filters + natural language ---

def test_search_forwards_structured_filters(monkeypatch, client):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        # a match on the first call, so no filter relaxation is triggered
        return DummyResponse(200, {"status": "success", "result": {"count": 1, "accommodations": [{}]}})

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(mcp_api.requests, "post", fake_post)

    response = client.post("/accommodation/mcp/search", json={
        "destination": "Tokyo", "max_price": 150, "accommodation_type": "hostel", "min_rating": 4.0,
    })

    assert response.status_code == 200
    assert captured["url"].endswith("/search_accommodations")
    assert captured["json"] == {
        "destination": "Tokyo", "max_price": 150, "accommodation_type": "hostel", "min_rating": 4.0,
    }
    assert response.get_json()["result"]["interpreted_from"] == "filters"


def test_search_extracts_filters_from_a_question(monkeypatch, client):
    captured = {}

    def fake_extract(question):
        captured["question"] = question
        return {"destination": "Osaka", "accommodation_type": "hostel", "max_price": 60.0}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return DummyResponse(200, {"status": "success", "result": {"count": 1, "accommodations": []}})

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(llm_client, "extract_search_filters", fake_extract)
    monkeypatch.setattr(mcp_api.requests, "post", fake_post)

    response = client.post("/accommodation/mcp/search", json={"question": "Cheapest hostels in Osaka under $60"})

    assert response.status_code == 200
    assert captured["question"] == "Cheapest hostels in Osaka under $60"
    assert captured["json"] == {"destination": "Osaka", "accommodation_type": "hostel", "max_price": 60.0}
    body = response.get_json()["result"]
    assert body["interpreted_from"] == "question"
    assert body["question"] == "Cheapest hostels in Osaka under $60"


def test_search_reports_502_when_question_cannot_be_interpreted(monkeypatch, client):
    def fake_extract(question):
        raise llm_client.LLMServiceError("model offline")

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(llm_client, "extract_search_filters", fake_extract)

    response = client.post("/accommodation/mcp/search", json={"question": "anything"})
    assert response.status_code == 502
    assert "Could not interpret" in response.get_json()["error"]


def test_search_returns_403_when_disabled(monkeypatch, client):
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", False)
    response = client.post("/accommodation/mcp/search", json={"destination": "Tokyo"})
    assert response.status_code == 403


@pytest.mark.parametrize("raw,expected", [
    ({"accommodation_type": "castle"}, {}),                       # not a real type
    ({"min_rating": 9}, {}),                                      # outside 0-5
    ({"max_price": "not a number"}, {}),                          # unparseable
    ({"sort_by": "cheapest"}, {}),                                # not an allowed sort
    ({"amenities": ["WiFi", "Pool"]}, {"amenities": "WiFi,Pool"}),  # list -> csv
    ({"destination": "  Tokyo  "}, {"destination": "Tokyo"}),     # trimmed
])
def test_filter_sanitiser_drops_unusable_values(raw, expected):
    assert llm_client._sanitise_filters(raw) == expected


def test_search_reports_every_tool_call(monkeypatch, client):
    def fake_post(url, json=None, timeout=None):
        return DummyResponse(200, {"status": "success", "result": {
            "count": 2, "matched_by": "destination_city", "accommodations": [{}, {}]}})

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(mcp_api.requests, "post", fake_post)

    response = client.post("/accommodation/mcp/search", json={"destination": "Tokyo"})

    calls = response.get_json()["result"]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["tool"] == "search_accommodations"
    assert calls[0]["status"] == "success"
    assert calls[0]["result_count"] == 2
    assert calls[0]["arguments"] == {"destination": "Tokyo"}
    assert isinstance(calls[0]["duration_ms"], int)


def test_search_relaxes_filters_until_something_matches(monkeypatch, client):
    seen = []

    def fake_post(url, json=None, timeout=None):
        seen.append(dict(json))
        # only the call with nothing but destination left returns results
        count = 3 if set(json) == {"destination"} else 0
        return DummyResponse(200, {"status": "success", "result": {
            "count": count, "matched_by": "destination_city",
            "accommodations": [{}] * count}})

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(mcp_api.requests, "post", fake_post)

    response = client.post("/accommodation/mcp/search", json={
        "destination": "Tokyo", "amenities": "Onsen", "min_rating": 4.9, "accommodation_type": "ryokan",
    })

    body = response.get_json()["result"]
    assert body["count"] == 3
    assert body["dropped_filters"] == ["amenities", "min_rating", "accommodation_type"]
    assert len(body["tool_calls"]) == 4
    assert body["requested_filters"]["amenities"] == "Onsen"   # what was originally asked for
    assert len(seen) == 4


def test_search_does_not_relax_when_the_first_call_matches(monkeypatch, client):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(dict(json))
        return DummyResponse(200, {"status": "success", "result": {"count": 1, "accommodations": [{}]}})

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(mcp_api.requests, "post", fake_post)

    response = client.post("/accommodation/mcp/search", json={"destination": "Tokyo", "amenities": "Pool"})

    assert len(calls) == 1
    assert response.get_json()["result"]["dropped_filters"] == []


def test_search_never_drops_destination_or_price(monkeypatch, client):
    seen = []

    def fake_post(url, json=None, timeout=None):
        seen.append(dict(json))
        return DummyResponse(200, {"status": "success", "result": {"count": 0, "accommodations": []}})

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(mcp_api.requests, "post", fake_post)

    client.post("/accommodation/mcp/search", json={
        "destination": "Tokyo", "max_price": 50, "min_price": 10, "amenities": "Onsen",
    })

    for payload in seen:
        assert payload["destination"] == "Tokyo"
        assert payload["max_price"] == 50
        assert payload["min_price"] == 10


# --- MCP create-accommodation: draft then create ---

def test_draft_returns_prefilled_fields(monkeypatch, client):
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(llm_client, "draft_accommodation", lambda text: {
        "name": "Sakura House", "destination_city": "Osaka", "destination_id": "dest-osaka",
        "accommodation_type": "hostel", "price_per_night": 45.0, "rating": 4.2,
        "location": None, "description": None, "amenities": ["Pool", "WiFi"],
    })

    response = client.post("/accommodation/mcp/draft", json={"text": "a hostel in Osaka"})

    body = response.get_json()["result"]
    assert response.status_code == 200
    assert body["draft"]["name"] == "Sakura House"
    assert body["draft"]["destination_id"] == "dest-osaka"
    assert body["missing_required"] == []


def test_draft_flags_missing_required_fields(monkeypatch, client):
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(llm_client, "draft_accommodation", lambda text: {
        "name": None, "destination_city": "Bangkok", "destination_id": "dest-bangkok",
        "accommodation_type": "hostel", "price_per_night": None, "rating": None,
        "location": None, "description": None, "amenities": [],
    })

    response = client.post("/accommodation/mcp/draft", json={"text": "a cheap place in Bangkok"})

    assert response.get_json()["result"]["missing_required"] == ["name", "price_per_night"]


def test_draft_requires_text(monkeypatch, client):
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    assert client.post("/accommodation/mcp/draft", json={}).status_code == 400


def test_draft_writes_nothing(monkeypatch, client):
    """The draft step must never reach the MCP server."""
    calls = []
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(mcp_api.requests, "post", lambda *a, **k: calls.append(a) or DummyResponse(200, {}))
    monkeypatch.setattr(llm_client, "draft_accommodation", lambda text: {"name": "X"})

    client.post("/accommodation/mcp/draft", json={"text": "anything"})
    assert calls == []


def test_create_calls_the_write_tool(monkeypatch, client):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return DummyResponse(200, {"status": "success", "result": {
            "id": 42, "created": {"id": 42, "name": "Sakura House"}}})

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    monkeypatch.setattr(mcp_api.requests, "post", fake_post)

    response = client.post("/accommodation/mcp/create", json={
        "name": "Sakura House", "destination_id": "dest-osaka", "price_per_night": 45,
        "amenities": ["WiFi"],
    })

    assert response.status_code == 201
    assert captured["url"].endswith("/create_accommodation")
    assert captured["json"]["name"] == "Sakura House"
    result = response.get_json()["result"]
    assert result["id"] == 42
    assert result["tool_calls"][0]["tool"] == "create_accommodation"
    assert result["tool_calls"][0]["created_id"] == 42


def test_create_rejects_missing_required_fields(monkeypatch, client):
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", True)
    response = client.post("/accommodation/mcp/create", json={"name": "No City"})
    assert response.status_code == 400
    assert "destination_id" in response.get_json()["error"]


@pytest.mark.parametrize("path", ["/accommodation/mcp/draft", "/accommodation/mcp/create"])
def test_create_flow_returns_403_when_disabled(monkeypatch, client, path):
    monkeypatch.setattr(mcp_api, "MCP_ENABLED", False)
    assert client.post(path, json={"text": "x", "name": "x"}).status_code == 403


# --- RAG hybrid ask ---

def test_rag_ask_returns_answer_and_records(monkeypatch, client):
    monkeypatch.setattr(rag_api, "RAG_ENABLED", True)
    monkeypatch.setattr(rag_api, "answer_question", lambda query, k, caller: {
        "answer": "Three Tokyo stays are logged.",
        "citations": [{"chunk_id": "accommodation_1", "source_id": "accommodation-db:/accommodations",
                       "authority_tier": "tier_1"}],
        "confidence_category": "High",
    })
    monkeypatch.setattr(llm_client, "extract_search_filters", lambda q: {"destination": "Tokyo"})
    monkeypatch.setattr(mcp_api, "call_tool", lambda name, payload: {
        "status": "success",
        "result": {"count": 3, "matched_by": "destination_city",
                   "accommodations": [{"id": 1}, {"id": 2}, {"id": 3}]},
    })

    response = client.post("/accommodation/rag/ask", json={"query": "What stays are in Tokyo?"})

    body = response.get_json()["result"]
    assert response.status_code == 200
    assert body["confidence_category"] == "High"
    assert body["citations"][0]["authority_tier"] == "tier_1"
    assert body["count"] == 3
    assert body["filters"] == {"destination": "Tokyo"}
    assert body["records_error"] is None


def test_rag_ask_keeps_the_answer_when_the_records_leg_fails(monkeypatch, client):
    """A record-lookup failure must not lose the grounded answer."""
    monkeypatch.setattr(rag_api, "RAG_ENABLED", True)
    monkeypatch.setattr(rag_api, "answer_question", lambda query, k, caller: {
        "answer": "Grounded answer.", "citations": [], "confidence_category": "Medium",
    })

    def boom(question):
        raise llm_client.LLMServiceError("model offline")

    monkeypatch.setattr(llm_client, "extract_search_filters", boom)

    response = client.post("/accommodation/rag/ask", json={"query": "anything"})

    body = response.get_json()["result"]
    assert response.status_code == 200
    assert body["answer"] == "Grounded answer."
    assert body["count"] == 0
    assert "model offline" in body["records_error"]


def test_rag_ask_requires_a_query(monkeypatch, client):
    monkeypatch.setattr(rag_api, "RAG_ENABLED", True)
    assert client.post("/accommodation/rag/ask", json={}).status_code == 400


def test_rag_ask_returns_403_when_disabled(monkeypatch, client):
    monkeypatch.setattr(rag_api, "RAG_ENABLED", False)
    assert client.post("/accommodation/rag/ask", json={"query": "x"}).status_code == 403
