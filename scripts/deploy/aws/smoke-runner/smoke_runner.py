"""In-VPC smoke checks for the VoyageAI ECS deployment (stdlib only).

Runs as the `smoke-runner` ECS service (desired count 0; smoke-test.sh scales it
to 1 for one pass and back to 0). It reaches every backend by its Service Connect
name - the same hostname:port as docker-compose - so backends stay private.

Output contract read by `agentic_loop --mode cloud-deployment-review`:
    PASS crud:<service> <detail>
    FAIL ai-disabled:<service> <detail>
framed by `SMOKE-START run=<id>` and `SMOKE-DONE run=<id> pass=<n> fail=<n>`.
After the pass the process idles (SMOKE_IDLE_SECONDS) so ECS does not restart it.

Run locally against docker compose (host ports) with:
    python smoke_runner.py --local
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

TIMEOUT = float(os.environ.get("SMOKE_TIMEOUT_SECONDS", "15"))
READY_WAIT = float(os.environ.get("SMOKE_READY_WAIT_SECONDS", "180"))

# Service Connect names (cloud) and host ports (local docker compose).
BASES = {
    "shared": ("http://shared-backend:5000", "http://localhost:5000"),
    "destination": ("http://destination-backend:5001", "http://localhost:5011"),
    "accommodation": ("http://accommodation-backend:5002", "http://localhost:5002"),
    "activity": ("http://activity-backend:5003", "http://localhost:5003"),
    "budget": ("http://budget-backend:5004", "http://localhost:5004"),
    "itinerary": ("http://itinerary-be:5005", "http://localhost:5005"),
}
READY_PATH = {"budget": "/"}  # budget-be has no /health yet

RESULTS = []


def emit(status, kind, name, detail):
    line = f"{status} {kind}:{name} {detail}".rstrip()
    RESULTS.append(status)
    print(line, flush=True)


def call(method, url, body=None):
    """Return (status, text). Never raises for HTTP errors; (0, error) when unreachable."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed http:// service URLs
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # DNS, refused, timeout
        return 0, f"{type(exc).__name__}: {exc}"


def wait_ready(name, base):
    deadline = time.monotonic() + READY_WAIT
    path = READY_PATH.get(name, "/health")
    while True:
        status, text = call("GET", base + path)
        if 200 <= status < 400:
            return True, status
        if time.monotonic() > deadline:
            return False, f"{status} {text[:120]}"
        time.sleep(5)


def find_id(text, keys):
    try:
        data = json.loads(text)
    except ValueError:
        return None
    queue = [data]
    while queue:
        node = queue.pop(0)
        if isinstance(node, dict):
            for key in keys:
                if isinstance(node.get(key), int) and not isinstance(node.get(key), bool):
                    return node[key]
            queue.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            queue.extend(node)
    return None


def crud(name, base, create, read, update, delete, id_keys, method_update="PUT", id_regex=None):
    """create/read/update/delete round-trip; detail names the first failing step."""
    status, text = call("POST", base + create[0], create[1])
    if status not in (200, 201):
        return emit("FAIL", "crud", name, f"create {create[0]} -> {status} {text[:160]}")
    item_id = find_id(text, id_keys)
    if item_id is None and id_regex:
        ids = [int(m) for m in re.findall(id_regex, text)]
        item_id = max(ids) if ids else None
    if item_id is None:
        return emit("FAIL", "crud", name, f"create ok but no id in response: {text[:160]}")
    for step, method, path, body, ok in (
        ("read", "GET", read.format(id=item_id), None, (200,)),
        ("update", method_update, update[0].format(id=item_id), update[1], (200,)),
        ("delete", "DELETE", delete.format(id=item_id), None, (200, 204)),
        ("read-after-delete", "GET", read.format(id=item_id), None, (404,)),
    ):
        status, text = call(method, base + path, body)
        if status not in ok:
            return emit("FAIL", "crud", name, f"{step} {path} -> {status} (expected {ok}) {text[:120]}")
    return emit("PASS", "crud", name, f"create/read/update/delete id={item_id}")


def disabled(name, base, path, body):
    status, text = call("POST", base + path, body)
    if status in (403, 503) and "disabled" in text.lower():
        return emit("PASS", "ai-disabled", name, f"{path} -> {status} disabled")
    return emit("FAIL", "ai-disabled", name, f"{path} -> {status} {text[:140]} (expected 403/503 'disabled')")


def run(local=False):
    run_id = uuid.uuid4().hex[:8]
    tag = f"SMOKE-{run_id}"
    print(f"SMOKE-START run={run_id}", flush=True)
    bases = {name: urls[1 if local else 0] for name, urls in BASES.items()}

    for name, base in bases.items():
        ready, info = wait_ready(name, base)
        if not ready:
            emit("FAIL", "crud", name, f"backend not reachable at {base}: {info}")
            continue

        if name == "shared":  # read-only access API: no create route exists
            statuses = {p: call("GET", base + p)[0] for p in ("/users", "/trips", "/nav")}
            ok = all(s == 200 for s in statuses.values())
            emit("PASS" if ok else "FAIL", "crud", name, f"read-only API (no write routes): {statuses}")
        elif name == "destination":
            crud(name, base, ("/api/destinations", {"city": tag, "country": "Smoketestland"}),
                 "/api/destinations/{id}", ("/api/destinations/{id}", {"description": "updated by smoke test"}),
                 "/api/destinations/{id}", ["id", "destination_id"])
            disabled(name, base, "/api/destinations/mcp-search", {"country": "Japan"})
            disabled(name, base, "/api/destinations/rag-answer", {"question": "What is in Tokyo?"})
            disabled(f"{name}-ai-mode", base, "/api/destinations/ai-compare", {"city_a": "Tokyo", "city_b": "Paris"})
        elif name == "accommodation":
            crud(name, base, ("/accommodations", {"name": tag, "destination_id": 1, "price_per_night": 100}),
                 "/accommodations/{id}", ("/accommodations/{id}", {"price_per_night": 120}),
                 "/accommodations/{id}", ["id", "accommodation_id"], method_update="PATCH")
            disabled(name, base, "/accommodation/mcp/search", {"destination": "Tokyo"})
            disabled(name, base, "/accommodation/rag/answer", {"query": "What stays are in Tokyo?"})
            disabled(f"{name}-ai-mode", base, "/ai/recommend", {"destination": "Tokyo"})
        elif name == "activity":
            crud(name, base, ("/api/add_activity", {"activity_name": tag, "activity_type": "Sightseeing",
                                                    "activity_cost": 10, "duration": "1 hour"}),
                 "/api/view_activity/{id}", ("/api/edit_activity/{id}", {"activity_cost": 12}),
                 "/api/delete_activity/{id}", ["id", "activity_id"], method_update="PATCH")
            disabled(name, base, "/api/activity/mcp/list-activities", {})
            disabled(name, base, "/api/activity/rag/answer", {"query": "food tours", "k": 3})
            disabled(f"{name}-ai-mode", base, "/api/activity/ai-summary", {"activity_id": 1})
        elif name == "budget":
            expense = {"trip_reference": tag, "expense": "Smoke test", "category": "Other",
                       "estimated_cost": 1, "destination_id": 1}
            crud(name, base, ("/expenses", expense), "/expenses/{id}",
                 ("/expenses/{id}", {**expense, "estimated_cost": 2}), "/expenses/{id}",
                 ["id", "expense_id"], id_regex=r'data-expense-id="(\d+)"')
            disabled(name, base, "/budget/mcp/list-expenses", {"trip_reference": tag})
            disabled(name, base, "/budget/rag/answer", {"query": "What did I spend?"})
            disabled(f"{name}-ai-mode", base, "/budget/ai-analyse", {"trip_reference": tag})
        elif name == "itinerary":
            item = {"trip_reference": tag, "day": 1, "start_time": "09:00", "end_time": "10:00",
                    "activity_id": 1, "destination_id": 1, "estimated_cost": 5}
            crud(name, base, ("/api/itinerary", item), "/api/itinerary/{id}",
                 ("/api/itinerary/{id}", {**item, "notes": "updated by smoke test"}), "/api/itinerary/{id}",
                 ["id", "itinerary_item_id", "item_id"])
            disabled(name, base, "/api/itinerary/mcp/itinerary", {"trip_reference": tag, "day": 1})
            disabled(name, base, "/api/itinerary/rag/answer", {"trip_reference": tag, "query": "What is planned?"})
            disabled(f"{name}-ai-mode", base, "/api/itinerary/ai-review",
                     {"trip_reference": tag, "day": 1, "prompt": "Review this itinerary"})

    failed = RESULTS.count("FAIL")
    print(f"SMOKE-DONE run={run_id} pass={RESULTS.count('PASS')} fail={failed}", flush=True)
    return failed


def main(argv) -> int:
    local = "--local" in argv
    failed = run(local=local)
    if local:
        return 1 if failed else 0
    time.sleep(float(os.environ.get("SMOKE_IDLE_SECONDS", "3600")))  # keep the task alive; scaled to 0 after
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
