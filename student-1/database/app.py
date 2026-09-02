"""Standalone entry point for the destination database service (port 6001).

Session 1 scope: / and /health only, proving the container owns SQLite via
the named volume. Destination data routes (/destinations, ...) are Session 2.
"""
import os

from flask import Flask, jsonify

from init_db import get_connection

PORT = int(os.environ.get("PORT", "6001"))


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def root():
        return jsonify({"service": "destination-database", "status": "ok"})

    @app.route("/health")
    def health():
        try:
            conn = get_connection()
            conn.execute("SELECT 1")
            conn.close()
        except Exception as exc:  # sqlite3.Error and friends
            return jsonify(
                {"service": "destination-database", "status": "unhealthy", "database": str(exc)}
            ), 503

        return jsonify({"service": "destination-database", "status": "ok", "database": "ok"})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
