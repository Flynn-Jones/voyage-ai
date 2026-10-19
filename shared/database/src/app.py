"""Standalone entry point for the shared database service (port 6000)."""
import os

from flask import Flask

from db_routes import db_api


def create_app():
    app = Flask(__name__)
    app.register_blueprint(db_api)
    return app


if __name__ == "__main__":
    app = create_app()
    # Runs inside its container: it must bind all interfaces for Docker port mapping.
    # Werkzeug debugger only on explicit opt-in (FLASK_DEBUG=1); it allows code execution if reachable.
    app.run(host="0.0.0.0", port=6000, debug=os.environ.get("FLASK_DEBUG") == "1")  # noqa: S104 - container
