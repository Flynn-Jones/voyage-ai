"""HTTP wrapper around activity-service's MCP tools.

Runs locally on the host (not containerised). The Dockerised backend reaches
this process via http://host.docker.internal:<PORT>, the same pattern already
used for Ollama and for ai-services/mcp-server/mcp_http_server.py.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools import get_activity, get_activity_assignments, get_assignment, list_activities, list_assignments

TOOLS = {
    "list_activities": lambda payload: list_activities(),
    "get_activity": lambda payload: get_activity(payload.get("activity_id")),
    "get_activity_assignments": lambda payload: get_activity_assignments(payload.get("activity_id")),
    "list_assignments": lambda payload: list_assignments(),
    "get_assignment": lambda payload: get_assignment(payload.get("assignment_id")),
}


class MCPHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict):
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _read_json(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service": "activity-mcp-server", "tools": list(TOOLS)})
            return
        self._send_json(404, {"status": "error", "error": "not_found"})

    def do_POST(self):
        tool_name = self.path.lstrip("/")
        if tool_name not in TOOLS:
            self._send_json(404, {"status": "error", "error": f"unknown tool: {tool_name}"})
            return

        try:
            payload = self._read_json()
        except Exception as exc:
            self._send_json(400, {"status": "error", "error": f"invalid_json: {exc}"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"status": "error", "error": "invalid_json: body must be an object"})
            return

        try:
            result = TOOLS[tool_name](payload)
            status = 200 if "error" not in result else 502
            self._send_json(status, {"status": "success" if status == 200 else "error", "result": result})
        except Exception as exc:
            self._send_json(500, {"status": "error", "error": str(exc)})


def main():
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "7003"))
    server = ThreadingHTTPServer((host, port), MCPHandler)
    print(f"Activity MCP HTTP server running on {host}:{port}")
    print(f"Available tools: {', '.join(TOOLS)}")
    server.serve_forever()


if __name__ == "__main__":
    main()
