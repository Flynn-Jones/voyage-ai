"""Standalone entry point for the destination frontend homepage (port 3001).

Session 1 scope: / (renders the live backend/database chain status) and
/health (frontend's own liveness only, so a backend outage does not make the
frontend look dead). Destination UI/CRUD is Session 2.
"""
import os

import requests
from flask import Flask, jsonify, render_template

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "3001"))
BACKEND_SERVICE_URL = os.environ.get("BACKEND_SERVICE_URL", "http://localhost:5001")


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
    )

    @app.route("/")
    def index():
        try:
            backend_response = requests.get(f"{BACKEND_SERVICE_URL}/health", timeout=5)
            backend_status = backend_response.json()
        except requests.exceptions.RequestException as exc:
            backend_status = {"status": "unreachable", "error": str(exc)}

        return render_template("index.html", backend_status=backend_status)

    @app.route("/health")
    def health():
        return jsonify({"service": "destination-frontend", "status": "ok"})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
