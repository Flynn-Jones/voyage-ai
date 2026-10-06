"""Offline tests for backend POST /api/destinations/rag-answer (shared RAG client)."""
import importlib.util
import os
import sys

import pytest
import requests

BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _load():
    spec = importlib.util.spec_from_file_location("destination_backend_rag_app", os.path.join(BACKEND_DIR, "app.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["destination_backend_rag_app"] = module
    spec.loader.exec_module(module)
    return module


backend_app = _load()
rag_client = backend_app.rag_client
URL = "/api/destinations/rag-answer"

CITATIONS = [
    {"chunk_id": "destination_3", "source_id": "destination-db:/destinations", "authority_tier": "tier_1"},
    {"chunk_id": "destination_5", "source_id": "destination-db:/destinations", "authority_tier": "tier_1"},
]
SUCCESS = {"status": "success", "query": "q", "answer": "Osaka is known for street food.", "citations": CITATIONS,
           "confidence_category": "High", "retrieval_summary": {"retrieved_count": 2}}
INSUFFICIENT = {"status": "insufficient_context", "query": "q", "answer": "Insufficient evidence to answer this question from the current corpus.",
                "citations": [], "confidence_category": "Insufficient", "retrieval_summary": {"retrieved_count": 0}}


class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def client():
    backend_app.app.testing = True
    return backend_app.app.test_client()


@pytest.fixture
def post(monkeypatch):
    calls = []
    state = {"resp": Resp(200, SUCCESS), "exc": None, "calls": calls}

    def fake_post(url, json=None, timeout=None, **kw):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if state["exc"]:
            raise state["exc"]
        return state["resp"]

    monkeypatch.setattr(rag_client.requests, "post", fake_post)
    monkeypatch.setattr(rag_client, "RAG_ENABLED", True)
    return state


def ask(client, question="Which city has street food?"):
    return client.post(URL, json={"question": question})


def test_grounded_success_propagates_answer_citations_confidence(client, post):
    r = ask(client)
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "success"
    assert body["answer"] == SUCCESS["answer"]
    assert body["confidence_category"] == "High"
    assert body["citations"] == CITATIONS
    call = post["calls"][0]
    assert call["url"].endswith("/answer")
    assert call["json"]["query"] == "Which city has street food?"
    assert call["json"]["caller"] == "destination-service" and "k" in call["json"]


def test_insufficient_context(client, post):
    post["resp"] = Resp(200, INSUFFICIENT)
    r = ask(client, "capital of Mars")
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "insufficient_context"
    assert body["citations"] == [] and body["confidence_category"] == "Insufficient"


@pytest.mark.parametrize("body", [{}, {"question": "  "}, {"question": "ab"}, {"question": "x" * 501},
                                  {"question": 7}, {"question": "bad\x00"}, ["q"]])
def test_validation(client, post, body):
    r = client.post(URL, json=body)
    assert r.status_code == 400 and r.get_json()["status"] == "invalid_request"
    assert post["calls"] == []


def test_disabled(client, post, monkeypatch):
    monkeypatch.setattr(rag_client, "RAG_ENABLED", False)
    r = ask(client)
    assert r.status_code == 503 and r.get_json()["status"] == "disabled"
    assert post["calls"] == []


@pytest.mark.parametrize("exc,code,status", [
    (requests.exceptions.Timeout("slow"), 504, "timeout"),
    (requests.exceptions.ConnectionError("refused"), 503, "unavailable"),
])
def test_timeout_and_unavailable(client, post, exc, code, status):
    post["exc"] = exc
    r = ask(client)
    assert r.status_code == code and r.get_json()["status"] == status
    assert "refused" not in r.get_data(as_text=True)


@pytest.mark.parametrize("code,error_type", [(503, "llm_unavailable"), (500, "retrieval_failed")])
def test_infrastructure_error(client, post, code, error_type):
    post["resp"] = Resp(code, {"status": "error", "error_type": error_type, "error": "Ollama unavailable (m): 10.1.1.1",
                                "answer": None, "citations": [], "confidence_category": None})
    r = ask(client)
    body = r.get_json()
    assert r.status_code == 502 and body["status"] == "infrastructure_error" and body["error_type"] == error_type
    assert "10.1.1.1" not in r.get_data(as_text=True)


@pytest.mark.parametrize("resp", [
    Resp(200, None),
    Resp(200, ["x"]),
    Resp(200, {**SUCCESS, "answer": ""}),
    Resp(200, {**SUCCESS, "confidence_category": "Certain"}),
    Resp(200, {**SUCCESS, "citations": "none"}),
    Resp(200, {**INSUFFICIENT, "citations": CITATIONS}),
    Resp(200, {"status": "weird"}),
    Resp(502, SUCCESS),
])
def test_malformed(client, post, resp):
    post["resp"] = resp
    r = ask(client)
    assert r.status_code == 502 and r.get_json()["status"] == "malformed"


def test_default_url_is_shared_rag_port():
    assert rag_client.RAG_SERVICE_URL.endswith(":7002") or "RAG_SERVICE_URL" in os.environ
    assert "6013" not in rag_client.RAG_SERVICE_URL


def _fresh_rag_client(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("RAG_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RAG_ENABLED", value)
    spec = importlib.util.spec_from_file_location(
        "destination_rag_client_env", os.path.join(BACKEND_DIR, "rag_client.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE", "Off", "", "garbage"])
def test_enabled_flag_false_spellings_disable(monkeypatch, value):
    assert _fresh_rag_client(monkeypatch, value).RAG_ENABLED is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", " true "])
def test_enabled_flag_true_spellings_enable(monkeypatch, value):
    assert _fresh_rag_client(monkeypatch, value).RAG_ENABLED is True


def test_enabled_flag_unset_defaults_to_enabled(monkeypatch):
    assert _fresh_rag_client(monkeypatch, None).RAG_ENABLED is True
