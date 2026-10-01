"""Opt-in live Student 5 evidence for the existing MCP/RAG validation modes."""
import json
import os

import requests


def collect(kind):
    trip = os.environ["AGENTIC_ITINERARY_TRIP"]
    backend = os.getenv("ITINERARY_BACKEND_URL", "http://localhost:5005").rstrip("/")
    shared = os.getenv(f"{kind}_SERVICE_URL", f"http://localhost:{7001 if kind == 'MCP' else 7002}").rstrip("/")
    evidence = {}
    try:
        health = requests.get(f"{shared}/health", timeout=5)
        health.raise_for_status()
        evidence["shared_health"] = health.json()

        def call(path, body):
            response = requests.post(f"{backend}/api/itinerary/{path}", json=body, timeout=150)
            result = response.json()
            return response.status_code, result

        if kind == "MCP":
            code, result = call("mcp/itinerary", {"trip_reference": trip})
            evidence["itinerary"] = result
            invalid_code, invalid = call("mcp/itinerary", {"trip_reference": trip, "day": 0})
            evidence["invalid_input"] = {"http_status": invalid_code, "body": invalid}
            data = result.get("result", {})
            ok = (code == 200 and "get_itinerary" in evidence["shared_health"].get("tools", [])
                  and data.get("trip_reference") == trip and isinstance(data.get("items"), list)
                  and data.get("count", 0) > 0 and data["count"] == len(data["items"])
                  and all(i.get("trip_reference") == trip for i in data["items"])
                  and invalid_code == 400)
        else:
            code, refreshed = call("rag/refresh", {})
            evidence["refresh"] = refreshed
            payload = {"trip_reference": trip, "query": "What is the estimated itinerary cost?"}
            retrieve_code, retrieved = call("rag/retrieve", payload)
            answer_code, answered = call("rag/answer", payload)
            unsupported_code, unsupported = call("rag/answer", dict(payload, query="What is tomorrow's weather?"))
            evidence.update(retrieval=retrieved, answer=answered, unsupported=unsupported)
            ok = (code == 200 and refreshed.get("itinerary_chunk_count", 0) > 0
                  and not refreshed.get("itinerary_source_error") and retrieve_code == 200
                  and bool(retrieved.get("results")) and answer_code == 200
                  and bool(answered.get("citations")) and answered.get("confidence_category") == "High"
                  and unsupported_code == 200 and unsupported.get("confidence_category") == "Insufficient"
                  and unsupported.get("citations") == [])
        return ok, json.dumps(evidence, ensure_ascii=False)
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        evidence["error"] = str(exc)
        return False, json.dumps(evidence, ensure_ascii=False)
