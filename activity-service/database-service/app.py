"""Standalone entry point for the activity-service database service (port 6003)."""
from flask import Flask

from init_db import init_schema
from db_routes import db_api


def create_app():
    init_schema()
    app = Flask(__name__)
    app.register_blueprint(db_api)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=6003, debug=True)
