"""HTTP wrapper around the shared RAG pipeline.

Runs locally on the host (not containerised) so every feature's Dockerised
backend can reach the one real RAG instance via host.docker.internal, the
same connection pattern already used for Ollama.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rag_pipeline import answer_question, refresh_corpus, retrieve_context


def http_status_for(result: dict) -> int:
    """success and insufficient_context are valid outcomes; infrastructure failures are not."""
    status = result.get("status")
    if status in ("success", "insufficient_context"):
        return 200
    if result.get("error_type") == "llm_unavailable":
        return 503
    return 500


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
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service": "rag-server"})
            return
        self._send_json(404, {"status": "error", "error": "not_found"})

    def do_POST(self):
        try:
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
        except Exception as exc:
            self._send_json(400, {"status": "error", "error": f"invalid_json: {exc}"})
            return

        try:
            if self.path in ("/retrieve", "/answer") and payload.get("scope") == "itinerary":
                from itinerary_context import validate
                try:
                    validate(payload.get("query"), payload.get("k", 5),
                             payload.get("trip_reference"), payload.get("day"))
                except ValueError as exc:
                    self._send_json(400, {"status": "error", "error": str(exc)})
                    return
            if self.path == "/refresh":
                caller = (payload.get("caller") or "system").strip() or "system"
                result = refresh_corpus(caller=caller)
                self._send_json(http_status_for(result), result)
                return

            if self.path == "/retrieve":
                query = (payload.get("query") or "").strip()
                if not query:
                    self._send_json(400, {"status": "error", "error": "query is required"})
                    return
                result = retrieve_context(
                    query=query, k=payload.get("k", 5) if payload.get("scope") else int(payload.get("k", 5)),
                    caller=(payload.get("caller") or "system").strip(), scope=payload.get("scope"),
                    trip_reference=payload.get("trip_reference"), day=payload.get("day")
                )
                self._send_json(http_status_for(result), result)
                return

            if self.path == "/answer":
                query = (payload.get("query") or "").strip()
                if not query:
                    self._send_json(400, {"status": "error", "error": "query is required"})
                    return
                result = answer_question(
                    query=query, k=payload.get("k", 5) if payload.get("scope") else int(payload.get("k", 5)),
                    caller=(payload.get("caller") or "system").strip(), scope=payload.get("scope"),
                    trip_reference=payload.get("trip_reference"), day=payload.get("day")
                )
                self._send_json(http_status_for(result), result)
                return

            self._send_json(404, {"status": "error", "error": "not_found"})
        except Exception as exc:
            self._send_json(500, {"status": "error", "error": str(exc)})


def main():
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "7002"))
    server = ThreadingHTTPServer((host, port), RAGHandler)
    print(f"RAG HTTP server running on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
