"""Isolated-process HTTP lifecycle with real shared runner, owning backend and temporary DB.

Only Ollama and the MCP transport are replaced: the existing get_itinerary tool still reads the
real temporary database over HTTP. Run in a child process to preserve the tiers' module isolation.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_multi_agent_http_lifecycle_and_lost_ack():
    result = subprocess.run(  # noqa: S603 - fixed Python and this repository test; no shell
        [sys.executable, "-B", str(Path(__file__).resolve()), "--http-check"],
        capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS all four HTTP decisions, durable replay, shared history and audit" in result.stdout


def http_check():
    import importlib
    import tempfile
    import threading

    import requests
    from werkzeug.serving import make_server

    root = Path(__file__).resolve().parents[2]
    servers, threads = [], []

    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def call(method, url, body=None, expected=200):
        response = requests.request(method, url, json=body, timeout=10)
        assert response.status_code == expected, (url, response.status_code, response.text)
        return response.json()

    with tempfile.TemporaryDirectory(prefix="itinerary-multi-agent-") as directory:
        os.environ["DB_FILE"] = str(Path(directory) / "itinerary.sqlite")
        try:
            sys.path.insert(0, str(root / "itinerary-service/itinerary-db"))
            db_app = importlib.import_module("app").create_app(os.environ["DB_FILE"])
            database = serve(make_server("127.0.0.1", 0, db_app, threaded=True))
            os.environ.update(DATABASE_SERVICE_URL=database, ITINERARY_DB_URL=database)

            sys.path.insert(0, str(root / "ai-services/multi-agent-server"))
            from registry import Registry
            from server import build_app, create_server
            from store import Store
            tool_spec = __import__("importlib.util", fromlist=["spec_from_file_location"])
            spec = tool_spec.spec_from_file_location("http_test_tools", root / "ai-services/mcp-server/tools.py")
            tools = tool_spec.module_from_spec(spec)
            spec.loader.exec_module(tools)

            class EvidenceClient:
                def __init__(self, allowed):
                    assert allowed == ["get_itinerary"]

                def call(self, tool, args):
                    assert tool == "get_itinerary"
                    return tools.get_itinerary(**args), "test-http-tool"

            def offline_chat(*args, **kwargs):
                raise ConnectionError("offline model: deterministic fallback under test")

            store = Store(Path(directory) / "runs.sqlite", Path(directory) / "audit.jsonl")
            shared_app = build_app(store, Registry(), chat=offline_chat, mcp_factory=EvidenceClient)
            shared = serve(create_server(shared_app, host="127.0.0.1", port=0))
            os.environ.update(MULTI_AGENT_ENABLED="true", MULTI_AGENT_SERVICE_URL=shared)
            for name in ("app", "validation"):
                sys.modules.pop(name, None)
            sys.path.insert(0, str(root / "itinerary-service/itinerary-be"))
            backend_app = importlib.import_module("app").create_app()
            backend = serve(make_server("127.0.0.1", 0, backend_app, threaded=True))
            from services import database_api, multi_agent_api

            real_call = multi_agent_api.call
            lost = [True]

            def lose_ack(method, path, payload=None):
                if method == "POST" and path.endswith("/applied") and lost[0]:
                    lost[0] = False
                    raise multi_agent_api.UpstreamError("simulated acknowledgement outage")
                return real_call(method, path, payload)
            multi_agent_api.call = lose_ack
            real_database_apply = database_api.apply_multi_agent
            lost_database_reply = [True]

            def lose_database_reply(run_id, payload):
                result = real_database_apply(run_id, payload)
                if payload["trip_reference"] == "HTTP-correct" and lost_database_reply[0]:
                    lost_database_reply[0] = False
                    raise database_api.DatabaseServiceError("simulated lost committed database response")
                return result
            database_api.apply_multi_agent = lose_database_reply

            for decision in ("approve", "partial", "correct", "reject"):
                trip = "HTTP-" + decision
                rows = []
                for start, end in [("09:00", "11:00"), ("10:00", "11:00"), ("10:30", "11:30")]:
                    row = {"trip_reference": trip, "day": 1, "start_time": start, "end_time": end,
                           "activity_id": 1, "destination_id": 1, "estimated_cost": 10, "notes": None}
                    rows.append(call("POST", backend + "/api/itinerary", row, 201))
                other = call("POST", backend + "/api/itinerary", {**rows[0], "trip_reference": trip + "-OTHER"}, 201)
                run = call("POST", backend + "/api/itinerary/multi-agent/run", {"trip_reference": trip, "day": 1}, 201)["result"]
                run_id = run["run_id"]
                path = backend + "/api/itinerary/multi-agent/runs/" + run_id
                assert len(run["work"]["items"]) == 2
                # A run does not write; neither does recording its human decision.
                assert call("GET", database + f"/itinerary-items/{rows[1]['itinerary_item_id']}") == rows[1]
                body = {"decision": decision}
                if decision == "partial":
                    body["approved_ids"] = [run["work"]["items"][1]["id"]]
                if decision == "correct":
                    body["edits"] = {run["work"]["items"][0]["id"]: {"notes": "Human corrected"}}
                call("POST", path + "/review", body)
                assert call("GET", database + f"/itinerary-items/{rows[1]['itinerary_item_id']}") == rows[1]
                call("POST", path + "/review", body, 409)
                if decision == "reject":
                    call("POST", path + "/apply", {}, 409)
                else:
                    if decision == "correct":
                        call("POST", path + "/apply", {}, 502)
                    result = call("POST", path + "/apply", {})["result"]
                    if decision == "approve":
                        assert not result["applied_recorded"] and not result["replayed"]
                        result = call("POST", path + "/apply", {})["result"]
                        assert result["applied_recorded"] and result["replayed"]
                    if decision == "correct":
                        assert result["replayed"]
                    assert result["state"] == "applied"
                    call("POST", path + "/apply", {}, 409)
                assert call("GET", database + f"/itinerary-items/{other['itinerary_item_id']}") == other
                history = call("GET", path + "/history")["result"]
                assert [event["step"] for event in history[:4]] == ["created", "plan", "work", "review"]
                assert any(entry["agent"] == "human" for entry in store.read_audit(run_id))
            print("PASS all four HTTP decisions, durable replay, shared history and audit")
        finally:
            for server in reversed(servers):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=5)


if __name__ == "__main__":
    try:
        http_check()
    except Exception:
        pytest.fail("HTTP lifecycle failed", pytrace=True)
