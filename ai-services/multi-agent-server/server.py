"""Shared Multi-Agent Server (port 7004, not containerised).

Runs Planner → Worker → Reviewer → Human Review workflows for every feature.
Dockerised feature backends reach it at http://host.docker.internal:7004, the
same pattern as the shared MCP (7001) and RAG (7002) servers.

Every response uses the MCP shim envelope:
    {"status": "success", "result": ...}  or  {"status": "error", "error": ...}
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config
from clients import mcp_client, ollama_client, rag_client
from core.runner import RunError, Runner
from registry import Registry
from store import Store

MAX_BODY_BYTES = 1_000_000
MAX_LIST_LIMIT = 500

ROUTES = [
    ("GET", re.compile(r"^/health$"), "health"),
    ("GET", re.compile(r"^/workflows$"), "list_workflows"),
    ("POST", re.compile(r"^/workflows/(?P<name>[A-Za-z0-9_]+)/runs$"), "start_run"),
    ("GET", re.compile(r"^/runs$"), "list_runs"),
    ("GET", re.compile(r"^/runs/(?P<run_id>[A-Za-z0-9_-]+)$"), "get_run"),
    ("GET", re.compile(r"^/runs/(?P<run_id>[A-Za-z0-9_-]+)/history$"), "get_history"),
    ("POST", re.compile(r"^/runs/(?P<run_id>[A-Za-z0-9_-]+)/review$"), "review_run"),
    ("POST", re.compile(r"^/runs/(?P<run_id>[A-Za-z0-9_-]+)/applied$"), "applied_run"),
]


class App:
    """Route handlers, independent of the HTTP transport so tests can drive them directly."""

    def __init__(self, store: Store, registry: Registry, runner: Runner, probes=None):
        self.store = store
        self.registry = registry
        self.runner = runner
        self.probes = probes or {
            "ollama_reachable": ollama_client.reachable,
            "mcp_reachable": mcp_client.reachable,
            "rag_reachable": rag_client.reachable,
        }

    def health(self, query, body):
        # Probes run in parallel: on Windows a refused localhost connection can take ~2 s each.
        with ThreadPoolExecutor(max_workers=len(self.probes) or 1) as pool:
            futures = {key: pool.submit(probe) for key, probe in self.probes.items()}
            probes = {key: bool(future.result()) for key, future in futures.items()}
        result = {
            "status": "ok",
            "service": config.SERVICE_NAME,
            "port": config.port(),
            **probes,
            "workflows": self.registry.names(),
        }
        if self.registry.errors:
            result["workflow_errors"] = self.registry.errors
        return 200, result

    def list_workflows(self, query, body):
        return 200, self.registry.describe()

    def start_run(self, query, body, name):
        return 201, self.runner.start_run(name, body)

    def list_runs(self, query, body):
        try:
            limit = int(query.get("limit", ["50"])[0])
        except ValueError:
            return 400, "limit must be an integer"
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        workflow = query.get("workflow", [""])[0] or None
        return 200, self.store.list_runs(workflow=workflow, limit=limit)

    def get_run(self, query, body, run_id):
        run = self.store.get_run(run_id)
        return (200, run) if run else (404, f"unknown run: {run_id}")

    def get_history(self, query, body, run_id):
        if self.store.get_run(run_id) is None:
            return 404, f"unknown run: {run_id}"
        return 200, self.store.history(run_id)

    def review_run(self, query, body, run_id):
        return 200, self.runner.review(run_id, body)

    def applied_run(self, query, body, run_id):
        return 200, self.runner.applied(run_id, body)

    def dispatch(self, method: str, raw_path: str, body: dict | None):
        """Return (http_status, envelope)."""
        parsed = urlparse(raw_path)
        query = parse_qs(parsed.query)
        for route_method, pattern, handler_name in ROUTES:
            match = pattern.match(parsed.path)
            if not match or route_method != method:
                continue
            try:
                status, result = getattr(self, handler_name)(query, body or {}, **match.groupdict())
            except RunError as exc:
                error = {"message": str(exc), **(exc.payload or {})} if exc.payload else str(exc)
                return exc.http_status, {"status": "error", "error": error}
            except Exception as exc:
                return 500, {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            if status >= 400:
                return status, {"status": "error", "error": result}
            return status, {"status": "success", "result": result}
        if any(p.match(parsed.path) for _, p, _ in ROUTES):
            return 405, {"status": "error", "error": "method_not_allowed"}
        return 404, {"status": "error", "error": "not_found"}


def make_handler(app: App):
    class MultiAgentHandler(BaseHTTPRequestHandler):
        def _send(self, status_code: int, payload: dict):
            response = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def _read_json(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                raise ValueError("request body too large")
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
            return payload

        def do_GET(self):
            self._send(*app.dispatch("GET", self.path, None))

        def do_POST(self):
            try:
                body = self._read_json()
            except Exception as exc:
                self._send(400, {"status": "error", "error": f"invalid_json: {exc}"})
                return
            self._send(*app.dispatch("POST", self.path, body))

        def log_message(self, fmt, *args):
            print(f"[multi-agent] {self.address_string()} {fmt % args}", file=sys.stderr)

    return MultiAgentHandler


def build_app(store: Store | None = None, registry: Registry | None = None, **runner_kwargs) -> App:
    store = store or Store()
    registry = registry or Registry()
    interrupted = store.fail_interrupted_runs()
    if interrupted:
        print(f"[multi-agent] marked {len(interrupted)} interrupted run(s) as failed")
    return App(store, registry, Runner(store, registry, **runner_kwargs))


def create_server(app: App, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host or config.host(), config.port() if port is None else port), make_handler(app))


def main():
    app = build_app()
    server = create_server(app)
    host, port = server.server_address[:2]
    print(f"Shared Multi-Agent Server on {host}:{port}")
    print(f"Workflows: {', '.join(app.registry.names()) or '(none)'}")
    for module, error in app.registry.errors.items():
        print(f"  skipped workflows/{module}.py: {error}")
    server.serve_forever()


if __name__ == "__main__":
    main()
