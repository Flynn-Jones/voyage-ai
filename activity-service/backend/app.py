"""Standalone entry point for the activity-service backend (port 5003)."""
from flask import Flask

from routes.activity_routes import activity_api


def create_app():
    app = Flask(__name__)
    app.register_blueprint(activity_api)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5003, debug=True)
