#!/usr/bin/env python3
"""Release 0 + Release 1 regression smoke test across all five features (C2).

1. Brings the root compose stack up (docker compose up -d [--build]).
2. Starts the ONE shared MCP server (:7001) and ONE shared RAG server (:7002)
   on the host, unless they are already healthy (then they are reused).
3. Records, from inside each backend container, the MCP_SERVICE_URL and
   RAG_SERVICE_URL it actually calls.
4. Rebuilds the shared RAG corpus (POST :7002/refresh).
5. For each feature (destination, accommodation, activity, budget, itinerary)
   exercises, through that feature's own backend:
     DB CRUD   create -> read -> update -> delete -> read-after-delete (404)
     AI Mode   the feature's Release 0 LLM endpoint
     MCP Mode  the feature's MCP route (-> shared :7001)
     RAG Mode  the feature's RAG route (-> shared :7002)
6. Writes docs/evidence/shared-integration/smoke-results.{json,md} and exits
   non-zero if any gating check failed.

Usage (repo root):
    python scripts/smoke_shared_integration.py [--build] [--no-compose] [--keep-servers]
        [--python PATH]   interpreter for the shared servers (needs mcp, chromadb, requests)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence" / "shared-integration"
MCP_URL = "http://localhost:7001"
RAG_URL = "http://localhost:7002"
OLLAMA_URL = "http://localhost:11434"
LLM_TIMEOUT = 240
FAST_TIMEOUT = 20

BACKENDS = {
    "destination": ("destination-backend", "http://localhost:5011"),
    "accommodation": ("accommodation-backend", "http://localhost:5002"),
    "activity": ("activity-backend", "http://localhost:5003"),
    "budget": ("budget-backend", "http://localhost:5004"),
    "itinerary": ("itinerary-backend", "http://localhost:5005"),
}
# Budget's backend has no /health; its root route is the health check.
HEALTH_PATHS = {"http://localhost:5004": "/"}
RUN_ID = uuid.uuid4().hex[:6]


# ---------------------------------------------------------------- infrastructure

def log(msg):
    print(msg, flush=True)


def healthy(url, timeout=3):
    try:
        return requests.get(url + HEALTH_PATHS.get(url, "/health"), timeout=timeout).status_code == 200
    except requests.exceptions.RequestException:
        return False


def wait_for(url, seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if healthy(url):
            return True
        time.sleep(2)
    return False


def compose_up(build):
    cmd = ["docker", "compose", "-f", str(REPO_ROOT / "docker-compose.yml"), "up", "-d"] + (["--build"] if build else [])
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=REPO_ROOT).returncode


def start_shared_servers(python, log_dir):
    started, status = [], {}
    for name, url, rel in (("mcp-server", MCP_URL, "ai-services/mcp-server/mcp_http_server.py"),
                           ("rag-server", RAG_URL, "ai-services/rag-server/rag_http_server.py")):
        if healthy(url):
            status[name] = f"reused already-running server at {url}"
            continue
        script = REPO_ROOT / rel
        out = open(log_dir / f"{name}.log", "w", encoding="utf-8")
        proc = subprocess.Popen([python, script.name], cwd=script.parent, stdout=out, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONUNBUFFERED": "1"})
        started.append(proc)
        status[name] = f"started pid {proc.pid} ({python} {rel})" if wait_for(url, 60) else f"FAILED to start ({rel})"
    return started, status


def seed_activity_db():
    """Activity's documented, idempotent demo seed (a fresh volume is empty by design)."""
    cmd = ["docker", "compose", "-f", str(REPO_ROOT / "docker-compose.yml"), "exec", "-T", "activity-database",
           "python", "seed_mock_data.py"]
    out = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    return (out.stdout or out.stderr).strip().splitlines()[-1:] or [f"exit {out.returncode}"]


def backend_wiring():
    wiring = {}
    for feature, (service, _) in BACKENDS.items():
        cmd = ["docker", "compose", "-f", str(REPO_ROOT / "docker-compose.yml"), "exec", "-T", service,
               "printenv", "MCP_SERVICE_URL", "RAG_SERVICE_URL", "MCP_ENABLED", "RAG_ENABLED"]
        out = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        values = out.stdout.split()
        keys = ["MCP_SERVICE_URL", "RAG_SERVICE_URL", "MCP_ENABLED", "RAG_ENABLED"]
        wiring[feature] = dict(zip(keys, values)) if out.returncode == 0 else {"error": out.stderr.strip()}
    return wiring


def ollama_models():
    try:
        return [m["name"] for m in requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()["models"]]
    except Exception:
        return []


# ---------------------------------------------------------------- checks

RESULTS = []


def record(feature, mode, step, method, url, ok, status, detail, gating=True):
    RESULTS.append({"feature": feature, "mode": mode, "step": step, "method": method,
                    "url": url, "status": status, "result": "PASS" if ok else ("FAIL" if gating else "SKIP"),
                    "detail": str(detail)[:300], "gating": gating})
    mark = "PASS" if ok else ("FAIL" if gating else "SKIP")
    log(f"  [{mark}] {feature:<13} {mode:<8} {step:<22} {method} {url} -> {status}  {str(detail)[:120]}")


def call(feature, mode, step, method, base, path, expect=(200,), check=None, timeout=FAST_TIMEOUT,
         gating=True, **kwargs):
    url = f"{base}{path}"
    try:
        response = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.exceptions.RequestException as exc:
        record(feature, mode, step, method, path, False, "no response", exc, gating)
        return None
    try:
        body = response.json()
    except ValueError:
        body = response.text
    ok = response.status_code in expect
    detail = ""
    if ok and check:
        ok, detail = check(body)
    elif not ok:
        detail = body
    record(feature, mode, step, method, path, ok, response.status_code, detail or summarise(body), gating)
    return body if ok else None


def summarise(body):
    if isinstance(body, dict):
        for key in ("answer", "reply", "summary", "analysis", "review", "status", "message"):
            if body.get(key):
                return f"{key}={str(body[key])[:100]!r}"
        return "keys=" + ",".join(list(body)[:8])
    if isinstance(body, list):
        return f"list[{len(body)}]"
    return str(body)[:100]


def non_empty(*keys):
    def check(body):
        if not isinstance(body, dict):
            return bool(body), summarise(body)
        found = [k for k in keys if body.get(k)]
        return bool(found), summarise(body) if found else f"none of {keys} in response: {summarise(body)}"
    return check


def rag_ok(body):
    if not isinstance(body, dict):
        return False, summarise(body)
    conf = body.get("confidence_category")
    cites = [c.get("chunk_id") for c in body.get("citations") or [] if isinstance(c, dict)]
    ok = bool(body.get("answer")) and conf in ("High", "Medium", "Low", "Insufficient") and (cites or conf == "Insufficient")
    return ok, f"confidence={conf} citations={cites[:4]} answer={str(body.get('answer'))[:80]!r}"


def check_destination():
    base = BACKENDS["destination"][1]
    name = f"Smoketown-{RUN_ID}"
    created = call("destination", "CRUD", "create", "POST", base, "/api/destinations", expect=(200, 201),
                   json={"city": name, "country": "Testland", "description": "smoke test record",
                         "average_daily_cost": 100, "recommended_trip_length": 3, "travel_style": "Budget",
                         "categories": ["food"]})
    dest_id = (created or {}).get("destination_id") or (created or {}).get("id")
    if dest_id:
        path = f"/api/destinations/{dest_id}"
        call("destination", "CRUD", "read", "GET", base, path,
             check=lambda b: (isinstance(b, dict) and b.get("city") == name, summarise(b)))
        call("destination", "CRUD", "update", "PUT", base, path,
             json={"city": name, "country": "Testland", "description": "smoke test record (updated)",
                   "average_daily_cost": 110, "recommended_trip_length": 4, "travel_style": "Budget",
                   "categories": ["food"]})
        call("destination", "CRUD", "delete", "DELETE", base, path, expect=(200, 204))
        call("destination", "CRUD", "read-after-delete", "GET", base, path, expect=(404,))
    call("destination", "AI", "ai-compare", "POST", base, "/api/destinations/ai-compare", timeout=LLM_TIMEOUT,
         json={"city_a": "Tokyo", "city_b": "Osaka", "preferences": "food"}, check=non_empty("comparison", "answer", "result", "response"))
    call("destination", "MCP", "mcp-search (country)", "POST", base, "/api/destinations/mcp-search",
         json={"country": "Japan"}, timeout=60)
    call("destination", "RAG", "rag-answer", "POST", base, "/api/destinations/rag-answer", timeout=LLM_TIMEOUT,
         json={"question": "Which destination is known for street food and nightlife?"}, check=rag_ok)


def check_accommodation():
    base = BACKENDS["accommodation"][1]
    name = f"Smoke Hostel {RUN_ID}"
    created = call("accommodation", "CRUD", "create", "POST", base, "/accommodations", expect=(200, 201),
                   json={"name": name, "destination_id": "dest-tokyo", "destination_city": "Tokyo",
                         "price_per_night": 42, "type": "hostel", "rating": 4.0})
    acc_id = (created or {}).get("id") or ((created or {}).get("data") or {}).get("id")
    if acc_id:
        path = f"/accommodations/{acc_id}"
        call("accommodation", "CRUD", "read", "GET", base, path)
        call("accommodation", "CRUD", "update", "PATCH", base, path, json={"price_per_night": 45})
        call("accommodation", "CRUD", "delete", "DELETE", base, path, expect=(200, 204))
        call("accommodation", "CRUD", "read-after-delete", "GET", base, path, expect=(404,))
    call("accommodation", "AI", "ai/recommend", "POST", base, "/ai/recommend", timeout=LLM_TIMEOUT,
         json={"destination_city": "Tokyo", "interests": ["food"], "max_price": 200})
    call("accommodation", "MCP", "mcp/by-destination", "POST", base, "/accommodation/mcp/by-destination",
         json={"destination": "Tokyo"}, timeout=60)
    call("accommodation", "RAG", "rag/answer", "POST", base, "/accommodation/rag/answer", timeout=LLM_TIMEOUT,
         json={"query": "What accommodation is available in Tokyo?"}, check=rag_ok)


def check_activity():
    base = BACKENDS["activity"][1]
    name = f"Smoke Walk {RUN_ID}"
    created = call("activity", "CRUD", "create", "POST", base, "/api/add_activity", expect=(200, 201),
                   json={"activity_name": name, "activity_type": "Food", "activity_cost": 12.5, "duration": "1 hour"})
    act_id = (created or {}).get("activity_id") or (created or {}).get("id")
    if act_id:
        call("activity", "CRUD", "read", "GET", base, f"/api/view_activity/{act_id}",
             check=lambda b: (isinstance(b, dict) and b.get("activity_name") == name, summarise(b)))
        call("activity", "CRUD", "update", "PATCH", base, f"/api/edit_activity/{act_id}",
             json={"activity_name": name, "activity_type": "Food", "activity_cost": 13.5, "duration": "1 hour"})
        call("activity", "CRUD", "delete", "DELETE", base, f"/api/delete_activity/{act_id}", expect=(200, 204))
        call("activity", "CRUD", "read-after-delete", "GET", base, f"/api/view_activity/{act_id}", expect=(404,))
    first = call("activity", "CRUD", "list", "GET", base, "/api/view_activities")
    first_id = (first or [{}])[0].get("activity_id") if isinstance(first, list) and first else 1
    call("activity", "AI", "ai-summary", "POST", base, "/api/activity/ai-summary", timeout=LLM_TIMEOUT,
         json={"activity_id": first_id}, check=non_empty("summary", "response", "reply"))
    call("activity", "MCP", "mcp/list-activities", "POST", base, "/api/activity/mcp/list-activities", json={},
         check=lambda b: (b.get("status") == "success" and b["result"]["count"] >= 1,
                          f"tool={b.get('tool')} count={b.get('result', {}).get('count')}"))
    call("activity", "MCP", "mcp/activity", "POST", base, "/api/activity/mcp/activity", json={"activity_id": first_id},
         check=lambda b: (b.get("status") == "success", f"tool={b.get('tool')} {summarise(b.get('result'))}"))
    call("activity", "RAG", "rag/answer (count)", "POST", base, "/api/activity/rag/answer", timeout=LLM_TIMEOUT,
         json={"query": "How many activities are there?"},
         check=lambda b: (b.get("answer_source") == "deterministic" and rag_ok(b)[0], rag_ok(b)[1]))
    call("activity", "RAG", "rag/retrieve", "POST", base, "/api/activity/rag/retrieve", json={"query": "food walk", "k": 3},
         check=lambda b: (b.get("scope") == "activity" and bool(b.get("results"))
                          and all(r["chunk_id"].startswith(("activity_", "assignment_")) for r in b["results"]),
                          f"scope={b.get('scope')} ids={[r['chunk_id'] for r in b.get('results', [])]}"))


def check_budget():
    base = BACKENDS["budget"][1]
    trip = f"SMOKE-{RUN_ID}"
    created = call("budget", "CRUD", "create", "POST", base, "/expenses", expect=(200, 201),
                   json={"trip_reference": trip, "expense": "Smoke ramen", "category": "food",
                         "estimated_cost": 20, "actual_cost": 22, "status": "paid", "destination_id": "dest-tokyo"})
    # budget-be's Normal UI routes render HTML fragments (htmx), not JSON.
    match = re.search(r'data-expense-id="(\d+)"', created) if isinstance(created, str) else None
    exp_id = int(match.group(1)) if match else None
    if exp_id:
        path = f"/expenses/{exp_id}"
        call("budget", "CRUD", "read", "GET", base, path)
        call("budget", "CRUD", "update", "PUT", base, path,
             json={"trip_reference": trip, "expense": "Smoke ramen", "category": "food",
                   "estimated_cost": 20, "actual_cost": 25, "status": "paid", "destination_id": "dest-tokyo"})
    call("budget", "AI", "ai-analyse", "POST", base, "/budget/ai-analyse", timeout=LLM_TIMEOUT,
         json={"trip_reference": trip})
    call("budget", "MCP", "mcp/list-expenses", "POST", base, "/budget/mcp/list-expenses", json={}, timeout=60)
    call("budget", "RAG", "rag/answer", "POST", base, "/budget/rag/answer", timeout=LLM_TIMEOUT,
         json={"query": "budget expenses over estimate"}, check=rag_ok)
    if exp_id:
        call("budget", "CRUD", "delete", "DELETE", base, f"/expenses/{exp_id}", expect=(200, 204))
        call("budget", "CRUD", "read-after-delete", "GET", base, f"/expenses/{exp_id}", expect=(404,))


def check_itinerary():
    base = BACKENDS["itinerary"][1]
    trip = f"SMOKE-{RUN_ID}"
    item = {"trip_reference": trip, "day": 9, "start_time": "09:00", "end_time": "10:00", "activity_id": 1,
            "destination_id": 1, "estimated_cost": 30, "notes": "smoke test"}
    created = call("itinerary", "CRUD", "create", "POST", base, "/api/itinerary", expect=(200, 201), json=item)
    item_id = (created or {}).get("itinerary_item_id") or (created or {}).get("id")
    if item_id:
        path = f"/api/itinerary/{item_id}"
        call("itinerary", "CRUD", "read", "GET", base, path)
        call("itinerary", "CRUD", "update", "PUT", base, path, json={**item, "estimated_cost": 35})
    call("itinerary", "AI", "ai-review", "POST", base, "/api/itinerary/ai-review", timeout=LLM_TIMEOUT,
         json={"prompt": "Review my plan for day 9"})
    call("itinerary", "MCP", "mcp/itinerary", "POST", base, "/api/itinerary/mcp/itinerary",
         json={"trip_reference": trip}, timeout=60,
         check=lambda b: (isinstance(b, dict), summarise(b.get("result") if isinstance(b, dict) else b)))
    call("itinerary", "RAG", "rag/refresh", "POST", base, "/api/itinerary/rag/refresh", json={}, timeout=180)
    call("itinerary", "RAG", "rag/answer", "POST", base, "/api/itinerary/rag/answer", timeout=LLM_TIMEOUT,
         json={"trip_reference": trip, "query": "What activities are planned for this trip?", "k": 5}, check=rag_ok)
    if item_id:
        call("itinerary", "CRUD", "delete", "DELETE", base, f"/api/itinerary/{item_id}", expect=(200, 204))
        call("itinerary", "CRUD", "read-after-delete", "GET", base, f"/api/itinerary/{item_id}", expect=(404,))


# ---------------------------------------------------------------- report

def write_evidence(meta):
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "smoke-results.json").write_text(json.dumps({**meta, "results": RESULTS}, indent=2),
                                                     encoding="utf-8")
    lines = ["# Shared integration smoke run", "",
             f"- Run: `{meta['run_id']}` at {meta['started']} (UTC), {meta['duration_s']} s",
             f"- Git: `{meta['git']}`",
             f"- Shared servers: {meta['shared_servers']}",
             f"- Ollama models: {meta['ollama_models']}",
             f"- RAG refresh: {meta['rag_refresh']}", "",
             "## Backend wiring (read from inside each running container)", "",
             "| Feature | MCP_SERVICE_URL | RAG_SERVICE_URL | MCP_ENABLED | RAG_ENABLED |", "| --- | --- | --- | --- | --- |"]
    for feature, env in meta["wiring"].items():
        lines.append(f"| {feature} | {env.get('MCP_SERVICE_URL', env.get('error'))} | {env.get('RAG_SERVICE_URL')} | "
                     f"{env.get('MCP_ENABLED')} | {env.get('RAG_ENABLED')} |")
    lines += ["", "## Summary", "", "| Feature | DB CRUD | AI Mode | MCP Mode | RAG Mode |", "| --- | --- | --- | --- | --- |"]
    for feature in BACKENDS:
        cells = []
        for mode in ("CRUD", "AI", "MCP", "RAG"):
            rows = [r for r in RESULTS if r["feature"] == feature and r["mode"] == mode]
            passed = sum(r["result"] == "PASS" for r in rows)
            failed = sum(r["result"] == "FAIL" for r in rows)
            cells.append(("PASS" if rows and not failed else "FAIL") + f" ({passed}/{len(rows)})")
        lines.append(f"| {feature} | " + " | ".join(cells) + " |")
    lines += ["", "## Every check", "", "| Feature | Mode | Step | Request | HTTP | Result | Detail |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in RESULTS:
        detail = r["detail"].replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(f"| {r['feature']} | {r['mode']} | {r['step']} | `{r['method']} {r['url']}` | {r['status']} | "
                     f"{r['result']} | {detail} |")
    (EVIDENCE_DIR / "smoke-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--build", action="store_true", help="docker compose up --build")
    parser.add_argument("--no-compose", action="store_true", help="assume the compose stack is already up")
    parser.add_argument("--keep-servers", action="store_true", help="leave shared servers this run started running")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the shared MCP/RAG servers")
    args = parser.parse_args(argv)

    started_at = time.time()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    if not args.no_compose and compose_up(args.build) != 0:
        log("docker compose up failed")
        return 2
    procs, server_status = start_shared_servers(args.python, EVIDENCE_DIR)
    log(f"shared servers: {server_status}")
    for feature, (_, url) in BACKENDS.items():
        if not wait_for(url, 120):
            log(f"WARNING: {feature} backend {url} not healthy")

    try:
        log(f"  activity seed: {seed_activity_db()}")
        wiring = backend_wiring()
        for feature, env in wiring.items():
            log(f"  wiring {feature:<13} {env}")
        refresh = requests.post(f"{RAG_URL}/refresh", json={"caller": "smoke"}, timeout=180).json()
        refresh_summary = {k: refresh.get(k) for k in ("status", "chunk_count", "service_counts",
                                                       "source_status", "vector_store_status")}
        log(f"  rag refresh: {refresh_summary}")
        for check in (check_destination, check_accommodation, check_activity, check_budget, check_itinerary):
            log(f"--- {check.__name__}")
            check()
    finally:
        if not args.keep_servers:
            for proc in procs:
                proc.terminate()

    git = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True).stdout
    meta = {"run_id": RUN_ID, "started": datetime.fromtimestamp(started_at, timezone.utc).isoformat(timespec="seconds"),
            "duration_s": round(time.time() - started_at), "shared_servers": server_status,
            "git": git.stdout.strip() + (" (uncommitted changes)" if dirty.strip() else ""),
            "ollama_models": ollama_models(), "wiring": wiring, "rag_refresh": refresh_summary}
    write_evidence(meta)
    failed = [r for r in RESULTS if r["result"] == "FAIL"]
    log(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed; evidence in {EVIDENCE_DIR.relative_to(REPO_ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
