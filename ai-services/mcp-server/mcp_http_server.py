"""HTTP wrapper around the shared MCP tools.

Runs locally on the host (not containerised). Every feature's Dockerised
backend reaches this one process via http://host.docker.internal:<PORT>,
the same pattern already used for Ollama, so all five features hit a single
real shared MCP instance instead of duplicating tool logic per feature.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools import ci_report, get_accommodation_by_destination, list_expenses, project_files

TOOLS = {
    "list_expenses": lambda payload: list_expenses(payload.get("trip_reference")),
    "get_accommodation_by_destination": lambda payload: get_accommodation_by_destination(
        payload.get("destination")
    ),
    "project_files": lambda payload: project_files(payload.get("directory_path", ".")),
    "ci_report": lambda payload: ci_report(payload.get("feature", "budget-service")),
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
            self._send_json(200, {"status": "ok", "service": "mcp-server", "tools": list(TOOLS)})
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

        try:
            result = TOOLS[tool_name](payload)
            status = 200 if "error" not in result else 502
            self._send_json(status, {"status": "success" if status == 200 else "error", "result": result})
        except Exception as exc:
            self._send_json(500, {"status": "error", "error": str(exc)})


def main():
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "7001"))
    server = ThreadingHTTPServer((host, port), MCPHandler)
    print(f"MCP HTTP server running on {host}:{port}")
    print(f"Available tools: {', '.join(TOOLS)}")
    server.serve_forever()


if __name__ == "__main__":
    main()
