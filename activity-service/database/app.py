"""Standalone entry point for the activity-service database service (port 6003)."""
from flask import Flask

from init_db import init_schema
from db_routes import db_api
from seed_mock_data import seed_if_empty


def create_app(seed_demo_data=True):
    init_schema()
    if seed_demo_data:
        seed_if_empty()  # demo records only when the activities table is empty
    app = Flask(__name__)
    app.register_blueprint(db_api)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=6003, debug=True)  # noqa: S104 - binds inside its container; only the published port is reachable
