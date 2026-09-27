"""HTTP wrapper around the Activity Manager RAG pipeline.

Runs locally on the host (not containerised), like mcp-server/mcp_http_server.py.
The Dockerised backend reaches it via http://host.docker.internal:<RAG_PORT>.

GET /health; POST /refresh, /retrieve, /answer with a JSON object body.
400 for bad input, 500 for a tool error, 200 otherwise.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rag_pipeline

TOOLS = {
    "refresh": lambda body: rag_pipeline.refresh_corpus(caller=body.get("caller", "http")),
    "retrieve": lambda body: rag_pipeline.retrieve_context(
        body.get("query"), body.get("k", 5), caller=body.get("caller", "http"), trace_id=body.get("trace_id")
    ),
    "answer": lambda body: rag_pipeline.answer_question(
        body.get("query"), body.get("k", 5), caller=body.get("caller", "http"), trace_id=body.get("trace_id")
    ),
}


def status_code_for(result: dict) -> int:
    if result.get("status") == "success":
        return 200
    return 400 if result.get("error_type") == "invalid_input" else 500


class RAGHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict):
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _read_json(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {
                "status": "ok", "service": "activity-rag-server", "tools": list(TOOLS),
                "embedding_mode": rag_pipeline.EMBEDDING_MODE, "model": rag_pipeline.OLLAMA_MODEL,
            })
            return
        self._send_json(404, {"status": "error", "error": "not_found"})

    def do_POST(self):
        tool_name = self.path.lstrip("/")
        if tool_name not in TOOLS:
            self._send_json(404, {"status": "error", "error": f"unknown tool: {tool_name}"})
            return

        try:
            body = self._read_json()
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"status": "error", "error": f"invalid_json: {exc}"})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"status": "error", "error": "invalid_json: body must be an object"})
            return

        try:
            result = TOOLS[tool_name](body)
        except Exception as exc:  # the pipeline shouldn't raise; never let the handler 500 without JSON
            result = {"status": "error", "error": str(exc), "error_type": "tool_error"}
        self._send_json(status_code_for(result), result)


def main():
    host = "0.0.0.0"
    port = int(os.getenv("RAG_PORT", "6013"))
    server = ThreadingHTTPServer((host, port), RAGHandler)
    print(f"Activity RAG HTTP server running on {host}:{port}")
    print(f"Available tools: {', '.join(TOOLS)}")
    print(f"Embedding mode: {rag_pipeline.EMBEDDING_MODE}; answer model: {rag_pipeline.OLLAMA_MODEL}")
    server.serve_forever()


if __name__ == "__main__":
    main()
