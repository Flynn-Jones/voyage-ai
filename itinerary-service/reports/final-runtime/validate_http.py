"""Final validation evidence harness; exercises the real frontend/Nginx API."""
import json
import time
import uuid
from pathlib import Path

import requests

BASE = "http://localhost:3005"
report = {}


def call(method, path, body=None, expected=200):
    started = time.monotonic()
    response = requests.request(method, BASE + path, json=body, timeout=130)
    assert response.status_code == expected, (path, response.status_code, response.text)
    return response.json() if response.content else None


try:
    for port, path in [(3005, "/"), (5005, "/health"), (6005, "/health"), (7001, "/health"), (7002, "/health")]:
        response = requests.get(f"http://localhost:{port}{path}", timeout=10)
        assert response.status_code == 200
        report[f"health_{port}"] = "PASS"
    for asset in ("/", "/js/app.js", "/css/styles.css"):
        response = requests.get(BASE + asset, timeout=10)
        assert response.status_code == 200 and len(response.content) > 100
        report[asset] = {"status": 200, "content_type": response.headers.get("Content-Type"), "bytes": len(response.content)}
    before = call("GET", "/api/itinerary")
    trip = "FINAL-VALIDATION-" + uuid.uuid4().hex[:8]
    item = dict(trip_reference=trip, day=99, start_time="09:00", end_time="10:00", activity_id=1,
                destination_id=1, estimated_cost=12.5, notes="Temporary runtime validation")
    created = call("POST", "/api/itinerary", item, 201)
    item_path = f"/api/itinerary/{created['itinerary_item_id']}"
    try:
        assert call("GET", item_path)["trip_reference"] == trip
        assert call("PUT", item_path, dict(item, estimated_cost=15))["estimated_cost"] == 15
        assert any(i["itinerary_item_id"] == created["itinerary_item_id"] for i in call("GET", "/api/itinerary/day/99"))
    finally:
        call("DELETE", item_path, expected=204)
    call("GET", item_path, expected=404)
    assert len(call("GET", "/api/itinerary")) == len(before)
    report["crud_and_day_filter_cleanup"] = "PASS"
    report["ai_review"] = call("POST", "/api/itinerary/ai-review", {"day": 1, "prompt": "Review Day 1 briefly in two sentences."})
    assert report["ai_review"]["adapt"]["recommendation"]
    for day in (None, 2):
        payload = {"trip_reference": "TRIP-1001"}
        if day:
            payload["day"] = day
        result = call("POST", "/api/itinerary/mcp/itinerary", payload)
        assert result["result"]["count"] == len(result["result"]["items"]) > 0
        assert all(i["trip_reference"] == "TRIP-1001" and (day is None or i["day"] == day) for i in result["result"]["items"])
        report[f"mcp_day_{day}"] = result
    report["mcp_invalid"] = call("POST", "/api/itinerary/mcp/itinerary", {"trip_reference": "TRIP-1001", "day": 0}, 400)
    report["rag_refresh"] = call("POST", "/api/itinerary/rag/refresh", {})
    assert report["rag_refresh"]["itinerary_chunk_count"] > 0
    payload = {"trip_reference": "TRIP-1001", "query": "What is the estimated itinerary cost?", "k": 1}
    report["rag_retrieve"] = call("POST", "/api/itinerary/rag/retrieve", payload)
    assert all(c["metadata"]["trip_reference"] == "TRIP-1001" for c in report["rag_retrieve"]["results"])
    report["rag_answer"] = call("POST", "/api/itinerary/rag/answer", payload)
    assert report["rag_answer"]["answer_source"] == "llm" and report["rag_answer"]["citations"]
    assert report["rag_answer"]["confidence_category"] == "High"
    report["unsupported"] = call("POST", "/api/itinerary/rag/answer", dict(payload, query="What is tomorrow's weather?"))
    assert report["unsupported"]["answer_source"] == "retrieval_guard"
    assert report["unsupported"]["confidence_category"] == "Insufficient" and report["unsupported"]["citations"] == []
    report["overall"] = "PASS"
except Exception as exc:
    report["overall"] = "FAIL"
    report["error"] = str(exc)
    raise
finally:
    Path(__file__).with_name("http-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
