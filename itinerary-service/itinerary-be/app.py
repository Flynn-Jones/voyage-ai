import os

from flask import Flask

from routes.health import health_bp
from routes.ai_review import ai_review_bp
from routes.itinerary import itinerary_bp


def create_app():
    app = Flask(__name__)
    app.config["DATABASE_SERVICE_URL"] = os.environ.get(
        "DATABASE_SERVICE_URL",
        "http://localhost:6005",
    )
    app.register_blueprint(health_bp)
    app.register_blueprint(itinerary_bp)
    app.register_blueprint(ai_review_bp)
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5005"))
    app.run(host="0.0.0.0", port=port, debug=False)
