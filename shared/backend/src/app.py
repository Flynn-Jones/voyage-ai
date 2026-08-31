"""Standalone entry point for the shared access service (port 5000)."""
from flask import Flask

from routes.access_routes import access_api


def create_app():
    app = Flask(__name__)
    app.register_blueprint(access_api)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000)
