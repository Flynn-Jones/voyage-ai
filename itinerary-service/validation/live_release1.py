"""Opt-in real HTTP validation using temporary data and isolated local processes.

Run from the repository root: python itinerary-service/validation/live_release1.py
Add --ollama to exercise existing AI review and the shared dual-model loop.
The base RAG checks now also require a running local Ollama model.
Does not start Docker, alter development records, or leave server processes running.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from decimal import Decimal

import requests

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama", action="store_true")
    args = parser.parse_args()
    processes, logs, checks, failures = [], [], [], []
    with tempfile.TemporaryDirectory(prefix="voyage-release1-") as scratch:
        db_port, be_port, mcp_port, rag_port, closed_port = [port() for _ in range(5)]
        env = dict(os.environ, PYTHONUTF8="1", DB_FILE=str(Path(scratch) / "itinerary.sqlite"),
                   DATABASE_SERVICE_URL=f"http://127.0.0.1:{db_port}",
                   ITINERARY_DB_URL=f"http://127.0.0.1:{db_port}",
                   MCP_SERVICE_URL=f"http://127.0.0.1:{mcp_port}", MCP_ENABLED="true",
                   RAG_SERVICE_URL=f"http://127.0.0.1:{rag_port}", RAG_ENABLED="true",
                   RAG_DATA_DIR=str(Path(scratch) / "rag"),
                   BUDGET_DB_URL=f"http://127.0.0.1:{closed_port}",
                   ACCOMMODATION_DB_URL=f"http://127.0.0.1:{closed_port}",
                   DESTINATION_SERVICE_URL=f"http://127.0.0.1:{closed_port}",
                   ACTIVITY_SERVICE_URL=f"http://127.0.0.1:{closed_port}",
                   AGENTIC_ITINERARY_TRIP="TRIP-1001", ITINERARY_BACKEND_URL=f"http://127.0.0.1:{be_port}")
        base = env["ITINERARY_BACKEND_URL"]

        def start(script, number):
            log = open(Path(scratch) / f"server-{number}.log", "w+", encoding="utf-8")
            logs.append(log)
            process = subprocess.Popen([sys.executable, str(ROOT / script)], cwd=ROOT,
                                       env=dict(env, PORT=str(number)), stdout=log, stderr=log)
            processes.append(process)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                try:
                    if requests.get(f"http://127.0.0.1:{number}/health", timeout=1).status_code == 200:
                        return process
                except requests.RequestException:
                    pass
                time.sleep(.1)
            raise RuntimeError(f"server {script} did not start")

        def post(path, body, status=200):
            result = requests.post(base + "/api/itinerary/" + path, json=body, timeout=150)
            assert result.status_code == status, (path, result.status_code, result.text)
            return result.json()

        try:
            start("itinerary-service/itinerary-db/app.py", db_port)
            backend = start("itinerary-service/itinerary-be/app.py", be_port)
            mcp = start("ai-services/mcp-server/mcp_http_server.py", mcp_port)
            rag = start("ai-services/rag-server/rag_http_server.py", rag_port)
            checks.append("four real local HTTP services healthy")
            items = requests.get(base + "/api/itinerary", timeout=15).json()
            assert len(items) >= 12
            payload = dict(trip_reference="LIVE-VALIDATION", day=9, start_time="09:00", end_time="10:00",
                           activity_id=1, destination_id=1, estimated_cost=5, notes="temporary")
            created = requests.post(base + "/api/itinerary", json=payload, timeout=5)
            assert created.status_code == 201
            url = base + "/api/itinerary/" + str(created.json()["itinerary_item_id"])
            assert requests.get(url, timeout=5).status_code == 200
            assert requests.put(url, json=dict(payload, estimated_cost=7), timeout=5).status_code == 200
            assert any(i["estimated_cost"] == 7 for i in requests.get(base + "/api/itinerary/day/9", timeout=5).json())
            assert requests.delete(url, timeout=5).status_code == 204
            assert requests.get(url, timeout=5).status_code == 404
            checks.append("Release 0 CRUD and day filtering over HTTP")
            selected = [i for i in items if i["trip_reference"] == "TRIP-1001"]
            result = post("mcp/itinerary", {"trip_reference": "TRIP-1001", "day": 2})
            assert result["result"]["items"] == [{k: v for k, v in i.items() if k not in ("activity", "destination")}
                                                  for i in selected if i["day"] == 2]
            checks.append("backend -> shared MCP -> real itinerary database HTTP path")
            refreshed = post("rag/refresh", {})
            assert refreshed["itinerary_chunk_count"] == len(items) and not refreshed["itinerary_source_error"]
            query = {"trip_reference": "TRIP-1001", "query": "What is the estimated itinerary cost?", "k": 1}
            result = post("rag/answer", query)
            expected = sum(Decimal(str(i["estimated_cost"])) for i in selected)
            assert f"{expected:.2f}" in result["answer"]
            assert len(result["citations"]) == len(selected) and result["confidence_category"] == "High"
            assert result["answer_source"] == "llm" and result["model"] == env.get("OLLAMA_MODEL", "qwen2.5:7b")
            print(json.dumps({"live_itinerary_rag_answer": result}, indent=2))
            assert post("rag/answer", dict(query, query="What is tomorrow's weather?"))["confidence_category"] == "Insufficient"
            checks.append("backend -> shared RAG -> local Ollama: grounded total, citations, confidence, unsupported-question abstention")
            collector_code = ("import sys; sys.path.insert(0, 'ai-services/agentic_loop'); "
                              "from collectors.itinerary_evidence import collect; "
                              "results=[collect(k) for k in ('MCP','RAG')]; "
                              "print(results); assert all(ok for ok, evidence in results)")
            collected = subprocess.run([sys.executable, "-c", collector_code], cwd=ROOT, env=env,
                                       capture_output=True, text=True, timeout=180)
            assert collected.returncode == 0, collected.stdout + collected.stderr
            checks.append("existing shared MCP/RAG collectors: real itinerary evidence passed")
            if args.ollama:
                try:
                    result = post("ai-review", {"day": 1, "prompt": "Review Day 1 briefly."})
                    assert result["adapt"]["recommendation"] and result["adapt"]["advisory_only"]
                    checks.append("Release 0 AI review with real Ollama")
                except (AssertionError, requests.RequestException) as exc:
                    failures.append(f"Ollama AI review: {exc}")
                loop = subprocess.run([sys.executable, "ai-services/agentic_loop/app_main.py"],
                                      input="1\n2\n0\n", cwd=ROOT, env=env, capture_output=True,
                                      text=True, timeout=600)
                print(loop.stdout)
                if (loop.returncode == 0 and "MODEL FAILED" not in loop.stdout
                        and loop.stdout.count("[DONE] Review complete") == 2 and "Review model failed" not in loop.stdout):
                    checks.append("shared agentic loop MCP and RAG implementation/review using real Ollama")
                else:
                    failures.append("shared loop model stages failed; inspect printed output")
            for process in (mcp, rag):
                process.terminate()
                process.wait(timeout=10)
            post("mcp/itinerary", {"trip_reference": "TRIP-1001"}, 502)
            post("rag/answer", query, 502)
            assert requests.get(base + "/api/itinerary", timeout=10).status_code == 200
            checks.append("stopped MCP/RAG return 502; Release 0 reads still work")
            backend.terminate()
            backend.wait(timeout=10)
            env.update(MCP_ENABLED="false", RAG_ENABLED="false")
            start("itinerary-service/itinerary-be/app.py", be_port)
            for route in ("mcp/itinerary", "rag/answer", "rag/retrieve", "rag/refresh"):
                post(route, {}, 403)
            assert requests.get(base + "/api/itinerary", timeout=10).status_code == 200
            checks.append("CI flags: all optional routes 403; Release 0 reads still work")
        except Exception as exc:
            failures.append(f"Validation stopped: {exc}")
            raise
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            for log in logs:
                log.close()
            print(json.dumps({"passed_checks": checks, "count": len(checks), "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
