"""RAG collector tests against a stub HTTP server that mimics the shared RAG /health and /answer contract."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from collectors import rag_collector
from core.reporter import Trace

INSUFFICIENT = {
    "status": "insufficient_context", "query": "q", "answer": rag_collector.INSUFFICIENT_ANSWER,
    "citations": [], "confidence_category": "Insufficient",
    "retrieval_summary": {"k": 5, "retrieved_count": 0, "candidate_count": 20, "rejected_count": 20,
                          "retrieval_mode": "hybrid", "top_score": None},
}
GROUNDED = {
    "status": "success", "query": "q", "answer": "Osaka is known for street food and nightlife.",
    "citations": [
        {"chunk_id": "destination_3", "source_id": "destination-db:/destinations", "authority_tier": "tier_1"},
        {"chunk_id": "destination_5", "source_id": "destination-db:/destinations", "authority_tier": "tier_1"},
    ],
    "confidence_category": "High",
    "retrieval_summary": {"k": 5, "retrieved_count": 2, "candidate_count": 20, "rejected_count": 18,
                          "retrieval_mode": "hybrid", "top_score": 0.9},
}


def with_(base, **changes):
    return {**base, **changes}


@pytest.fixture
def stub(monkeypatch):
    state = {"health": (200, {"status": "ok", "service": "rag-server"}), "answers": {}, "posts": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._send(*state["health"])

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["posts"].append(payload)
            self._send(*state["answers"][payload["query"]])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(rag_collector, "RAG_SERVICE_URL", f"http://127.0.0.1:{server.server_port}")
    state["set"] = lambda supported, unsupported: state["answers"].update({
        rag_collector.TASKS[0]["question"]: supported, rag_collector.TASKS[1]["question"]: unsupported})
    state["set"]((200, GROUNDED), (200, INSUFFICIENT))
    yield state
    server.shutdown()


def run_one(index):
    trace = Trace("RAG")
    return rag_collector.run_task(rag_collector.TASKS[index], trace), trace


def adapt(trace):
    return " ".join(m for p, m in trace.steps if p == "ADAPT")


def test_collect_passes_and_traces_in_order(stub):
    ok, text = rag_collector.collect()
    assert ok
    assert text.count("PASS") == 2
    for phase in ("PLAN", "ACT", "OBSERVE", "ADAPT"):
        assert f"[{phase}]" in text
    assert [p["caller"] for p in stub["posts"]] == ["agentic-loop"] * 2 and stub["posts"][0]["k"] == 5
    assert "cannot be answered" in text


@pytest.mark.parametrize("body,fragment", [
    (with_(GROUNDED, citations=[]), "no citations"),
    (with_(GROUNDED, confidence_category="Insufficient"), "invalid confidence"),
    (with_(GROUNDED, confidence_category=None), "invalid confidence"),
    (with_(GROUNDED, citations=[{"chunk_id": "x_1", "source_id": "budget-db:/expenses"}],
           retrieval_summary={"retrieved_count": 1}), "no Destination DB citation"),
    (with_(GROUNDED, retrieval_summary={"retrieved_count": 0}), "inconsistent"),
    (with_(GROUNDED, retrieval_summary=None), "inconsistent"),
    (with_(GROUNDED, answer="Bangkok is known for temples."), "does not mention"),
    (with_(GROUNDED, answer=""), "answer missing"),
    (with_(INSUFFICIENT), "returned insufficient_context"),
])
def test_supported_failures(stub, body, fragment):
    stub["set"]((200, body), (200, INSUFFICIENT))
    ok, trace = run_one(0)
    assert not ok and fragment in adapt(trace)
    assert "FAIL" in adapt(trace)


def test_unsupported_success_is_fabrication_risk(stub):
    stub["set"]((200, GROUNDED), (200, with_(GROUNDED, answer="Olympus Mons City")))
    ok, trace = run_one(1)
    assert not ok and "fabrication_risk" in adapt(trace)


def test_unsupported_correct_passes(stub):
    ok, trace = run_one(1)
    assert ok and "PASS" in adapt(trace)


@pytest.mark.parametrize("body", [
    with_(INSUFFICIENT, citations=[{"chunk_id": "c", "source_id": "destination-db:/d"}]),
    with_(INSUFFICIENT, retrieval_summary={"retrieved_count": 2}),
    with_(INSUFFICIENT, answer="The capital of Mars is Olympus."),
    with_(INSUFFICIENT, confidence_category="High"),
])
def test_unsupported_bad_insufficient_fails(stub, body):
    stub["set"]((200, GROUNDED), (200, body))
    ok, trace = run_one(1)
    assert not ok and "FAIL" in adapt(trace)


@pytest.mark.parametrize("code,error_type", [(503, "llm_unavailable"), (500, "retrieval_failed")])
def test_infrastructure_errors_fail(stub, code, error_type):
    body = {"status": "error", "error_type": error_type, "error": "boom", "answer": None,
            "citations": [], "confidence_category": None}
    stub["set"]((code, body), (code, body))
    ok, trace = run_one(0)
    assert not ok and f"infrastructure:{error_type}" in adapt(trace)
    ok, trace = run_one(1)
    assert not ok and f"infrastructure:{error_type}" in adapt(trace)


@pytest.mark.parametrize("response", [(200, b"<html>no</html>"), (200, [1, 2]), (200, {"status": "weird"}), (500, {"x": 1})])
def test_malformed_fails(stub, response):
    stub["set"](response, response)
    ok, trace = run_one(0)
    assert not ok and "malformed" in adapt(trace)


def test_non_200_with_success_status_is_malformed(stub):
    stub["set"]((202, GROUNDED), (200, INSUFFICIENT))
    ok, trace = run_one(0)
    assert not ok and "malformed" in adapt(trace)


def test_health_unavailable_stops(stub):
    stub["health"] = (503, {"status": "down"})
    ok, text = rag_collector.collect()
    assert not ok and "FAIL unavailable" in text
    assert stub["posts"] == []


def test_connection_refused(monkeypatch):
    monkeypatch.setattr(rag_collector, "RAG_SERVICE_URL", "http://127.0.0.1:9")
    ok, text = rag_collector.collect()
    assert not ok and "FAIL unavailable" in text
