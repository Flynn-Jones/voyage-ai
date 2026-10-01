"""Offline tests for the Student 5 additions to shared infrastructure."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ai-services/rag-server"))
import itinerary_context as context
import rag_pipeline as rag

spec = importlib.util.spec_from_file_location("shared_itinerary_tools", ROOT / "ai-services/mcp-server/tools.py")
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


@pytest.fixture
def records():
    return [{"itinerary_item_id": i, "trip_reference": trip, "day": day,
             "start_time": "09:00", "end_time": "10:00", "activity_id": i,
             "destination_id": 1, "estimated_cost": cost, "notes": "Museum"}
            for i, trip, day, cost in [(1, "Trip A", 1, 10), (2, "Trip A", 2, 20), (3, "Trip B", 2, 999)]]


@pytest.fixture
def corpus(monkeypatch, records, tmp_path):
    monkeypatch.setattr(context.requests, "get", Mock(return_value=Mock(json=Mock(return_value=records))))
    chunks = context.load_chunks()
    monkeypatch.setattr(rag, "_last_corpus_chunks", chunks)
    monkeypatch.setattr(rag, "CORPUS_PATH", tmp_path / "corpus.jsonl")
    monkeypatch.setattr(rag, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(rag.requests, "post", Mock(return_value=Mock(
        json=Mock(return_value={"response": "The estimated itinerary cost is 30.00."}))))
    return chunks


def test_mcp_exact_trip_day(monkeypatch, records):
    get = Mock(return_value=Mock(json=Mock(return_value=records)))
    monkeypatch.setattr(tools.requests, "get", get)
    result = tools.get_itinerary("Trip A", 2)
    assert result["count"] == 1 and result["items"][0]["itinerary_item_id"] == 2
    assert result["read_only"] is True
    assert get.call_args.args[0].endswith("/itinerary-items")


@pytest.mark.parametrize("trip,day", [(None, None), ("", None), ("A", False), ("A", -1), ("A", "2")])
def test_mcp_invalid(trip, day):
    assert "error" in tools.get_itinerary(trip, day)


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError(), ValueError()])
def test_mcp_unavailable(monkeypatch, failure):
    monkeypatch.setattr(tools.requests, "get", Mock(side_effect=failure))
    assert "error" in tools.get_itinerary("Trip A")


def test_mcp_bad_records(monkeypatch):
    monkeypatch.setattr(tools.requests, "get", Mock(return_value=Mock(json=Mock(return_value={"data": []}))))
    assert "error" in tools.get_itinerary("Trip A")


def test_itinerary_metadata(corpus):
    assert corpus[1]["metadata"] == {"source_type": "itinerary_db", "trip_reference": "Trip A", "day": 2}
    assert corpus[1]["source_id"] == context.SOURCE


def test_scoped_retrieval(corpus):
    result = rag.retrieve_context("What is planned on Day 2 of Trip A?", scope="itinerary", trip_reference="Trip A")
    assert [r["chunk_id"] for r in result["results"]] == ["itinerary_2"]


def test_total_not_top_k_subtotal(corpus):
    result = rag.answer_question("What is the estimated itinerary cost for Trip A?", k=1, scope="itinerary", trip_reference="Trip A")
    assert "30.00" in result["answer"] and "999" not in result["answer"]
    assert {c["chunk_id"] for c in result["citations"]} == {"itinerary_1", "itinerary_2"}
    assert result["confidence_category"] == "High"
    assert result["retrieval_summary"]["retrieved_count"] == 2
    prompt = rag.requests.post.call_args.kwargs["json"]["prompt"]
    assert "30.00 across 2 item(s)" in prompt
    assert '"itinerary_item_id": 1' in prompt and '"itinerary_item_id": 2' in prompt
    assert "Trip B" not in prompt and "999" not in prompt
    assert result["answer_source"] == "llm"


@pytest.mark.parametrize("query,trip,day", [
    ("What is tomorrow's weather?", "Trip A", None),
    ("What activities are planned?", "Missing", None),
    ("What activities are planned?", "Trip A", 99),
    ("What is planned on Day 2?", "Trip A", 1),
    ("What is planned for Trip B?", "Trip A", None),
    ("What are hotel costs?", "Trip A", None),
    ("What is planned for TRIP-999?", "Trip A", None),
])
def test_insufficient(corpus, query, trip, day):
    result = rag.answer_question(query, scope="itinerary", trip_reference=trip, day=day)
    assert result["confidence_category"] == "Insufficient" and result["citations"] == []
    rag.requests.post.assert_not_called()


@pytest.mark.parametrize("kwargs", [{"query": ""}, {"k": True}, {"k": 0}, {"k": 21}, {"trip_reference": ""}, {"day": False}])
def test_rag_invalid(corpus, kwargs):
    args = dict(query="What activities are planned?", scope="itinerary", trip_reference="Trip A")
    args.update(kwargs)
    assert rag.answer_question(**args)["status"] == "error"


def test_refresh_includes_itinerary(monkeypatch, corpus):
    monkeypatch.setattr(rag, "load_budget_chunks", lambda: [])
    monkeypatch.setattr(rag, "load_accommodation_chunks", lambda: [])
    monkeypatch.setattr(rag, "load_doc_chunks", lambda: [])
    monkeypatch.setattr(rag, "load_repository_chunks", lambda: [])
    monkeypatch.setattr(rag, "reset_collection", Mock(side_effect=RuntimeError("offline vector store")))
    result = rag.refresh_corpus()
    assert result["itinerary_chunk_count"] == 3
    assert result["vector_store_status"] == "degraded"
    assert all(c.get("indexed_at") for c in rag.read_corpus())


def test_refresh_missing_source_not_indexed_as_evidence(monkeypatch, corpus):
    for name in ("load_budget_chunks", "load_accommodation_chunks", "load_doc_chunks", "load_repository_chunks"):
        monkeypatch.setattr(rag, name, lambda: [])
    monkeypatch.setattr(context, "load_chunks", Mock(side_effect=requests.ConnectionError()))
    monkeypatch.setattr(rag, "reset_collection", Mock(side_effect=RuntimeError()))
    result = rag.refresh_corpus()
    assert result["itinerary_source_error"] and result["itinerary_chunk_count"] == 0
    assert rag.read_corpus() == []


def test_legacy_model_failure_is_error(monkeypatch, corpus):
    monkeypatch.setattr(rag, "retrieve_context", lambda **kw: {"status": "success", "results": corpus})
    monkeypatch.setattr(rag, "generate_with_ollama", lambda *a: "Ollama unavailable: refused")
    assert rag.answer_question("budget cost")["status"] == "error"


def test_persisted_snapshot(monkeypatch, corpus):
    rag.write_corpus(corpus)
    monkeypatch.setattr(rag, "_last_corpus_chunks", [])
    assert rag.answer_question("What activities are planned?", scope="itinerary", trip_reference="Trip A")["confidence_category"] == "High"


def test_scoped_answer_uses_model_and_server_metadata(monkeypatch, corpus):
    model_text = "A museum visit is planned."
    post = Mock(return_value=Mock(json=Mock(return_value={"response": model_text,
                "citations": [{"chunk_id": "invented"}], "confidence_category": "Low"})))
    monkeypatch.setattr(rag.requests, "post", post)
    result = rag.answer_question("What is planned on Day 2?", scope="itinerary", trip_reference="Trip A")
    assert result["answer"] == model_text and result["answer_source"] == "llm"
    assert result["confidence_category"] == "High"
    assert result["citations"] == [{"chunk_id": "itinerary_2", "source_id": context.SOURCE, "authority_tier": "tier_1"}]
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == rag.OLLAMA_MODEL
    assert post.call_args.args == (rag.OLLAMA_GENERATE_URL,)
    assert '"itinerary_item_id": 2' in payload["prompt"]
    assert '"itinerary_item_id": 1' not in payload["prompt"]
    assert "Trip B" not in payload["prompt"]
    assert "ONLY" in payload["prompt"] and "outside knowledge" in payload["prompt"]
    assert "Insufficient evidence" in payload["prompt"]


def test_scope_trip_with_short_name(monkeypatch, corpus):
    corpus[0]["metadata"]["trip_reference"] = "T"
    result = rag.answer_question("What activities are planned?", scope="itinerary", trip_reference="T")
    assert result["confidence_category"] == "High"


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError(), requests.HTTPError(), ValueError()])
def test_scoped_model_failure_is_error(monkeypatch, corpus, failure):
    monkeypatch.setattr(rag.requests, "post", Mock(side_effect=failure))
    result = rag.answer_question("What activities are planned?", scope="itinerary", trip_reference="Trip A")
    assert result["status"] == "error" and result["error_type"] == "llm_unavailable"
    assert "answer" not in result and "citations" not in result


@pytest.mark.parametrize("body", [[], {}, {"response": " "}, {"response": 42}, {"error": "model missing"}])
def test_scoped_invalid_model_response(monkeypatch, corpus, body):
    monkeypatch.setattr(rag.requests, "post", Mock(return_value=Mock(json=Mock(return_value=body))))
    result = rag.answer_question("What activities are planned?", scope="itinerary", trip_reference="Trip A")
    assert result["status"] == "error" and "answer" not in result


def test_model_abstention(monkeypatch, corpus):
    monkeypatch.setattr(rag.requests, "post", Mock(return_value=Mock(json=Mock(return_value={"response": "Insufficient evidence."}))))
    result = rag.answer_question("What activities are planned?", scope="itinerary", trip_reference="Trip A")
    assert result["confidence_category"] == "Insufficient" and result["citations"] == []
    assert result["answer_source"] == "llm"


def test_invalid_cost_does_not_call_model(corpus):
    corpus[0]["record"]["estimated_cost"] = -1
    result = rag.answer_question("What is the estimated cost?", scope="itinerary", trip_reference="Trip A")
    assert result["confidence_category"] == "Insufficient"
    rag.requests.post.assert_not_called()


def test_unscoped_budget_generation_unchanged(monkeypatch, corpus):
    monkeypatch.setattr(rag, "retrieve_context", lambda **kw: {"status": "success", "results": corpus[:1]})
    legacy = Mock(return_value="Budget answer")
    scoped = Mock(side_effect=AssertionError("must not use scoped generation"))
    monkeypatch.setattr(rag, "generate_with_ollama", legacy)
    monkeypatch.setattr(rag, "generate_itinerary_answer", scoped)
    result = rag.answer_question("budget expenses")
    assert result["status"] == "success" and result["answer"] == "Budget answer"
    legacy.assert_called_once()
    scoped.assert_not_called()
