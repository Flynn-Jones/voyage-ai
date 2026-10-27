import os

from flask import Flask

from routes.health import health_bp
from routes.ai_review import ai_review_bp
from routes.itinerary import itinerary_bp
from routes.release1 import release1_bp
from routes.multi_agent import multi_agent_bp


def _multi_agent_timeout():
    try:
        timeout = int(os.environ.get("MULTI_AGENT_TIMEOUT_SECONDS", "420"))
        return timeout if timeout > 0 else 420
    except ValueError:
        return 420


def create_app():
    app = Flask(__name__)
    app.config["DATABASE_SERVICE_URL"] = os.environ.get(
        "DATABASE_SERVICE_URL",
        "http://localhost:6005",
    )
    app.config.update(
        AI_ENABLED=os.environ.get("AI_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on"),
        # Opt in locally; keep disabled in CI/cloud deployments.
        MULTI_AGENT_ENABLED=os.environ.get("MULTI_AGENT_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"),
        MULTI_AGENT_SERVICE_URL=os.environ.get("MULTI_AGENT_SERVICE_URL", "http://localhost:7004").rstrip("/"),
        MULTI_AGENT_TIMEOUT_SECONDS=_multi_agent_timeout(),
    )
    app.register_blueprint(health_bp)
    app.register_blueprint(itinerary_bp)
    app.register_blueprint(ai_review_bp)
    app.register_blueprint(release1_bp)
    app.register_blueprint(multi_agent_bp)
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5005"))
    app.run(host="0.0.0.0", port=port, debug=False)  # noqa: S104 - container must accept requests from other tiers
