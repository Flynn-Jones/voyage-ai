"""Standalone entry point for the shared database service (port 6000)."""
from flask import Flask

from db_routes import db_api


def create_app():
    app = Flask(__name__)
    app.register_blueprint(db_api)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=6000, debug=True)
