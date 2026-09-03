"""Standalone entry point for the activity-service backend (port 5003)."""
from flask import Flask
from flask_cors import CORS

from routes.activity_routes import activity_api
from routes.ai_routes import ai_api


def create_app():
    app = Flask(__name__)
    CORS(app)
    app.register_blueprint(activity_api)
    app.register_blueprint(ai_api)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5003, debug=True)
