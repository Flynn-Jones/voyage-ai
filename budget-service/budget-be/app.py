import logging
import os
import sys
from pathlib import Path

from flask import Flask
from flask_cors import CORS


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)


def create_app():
    # Imported here, after BASE_DIR is on sys.path (the app runs as a script or via gunicorn --chdir).
    from routes.ai_mode import ai_mode_bp
    from routes.mcp_mode import mcp_mode_bp
    from routes.multi_agent_mode import multi_agent_mode_bp
    from routes.normal_ui import normal_ui_bp
    from routes.rag_mode import rag_mode_bp

    app = Flask(__name__)
    CORS(app)

    app.register_blueprint(normal_ui_bp)
    app.register_blueprint(ai_mode_bp)
    app.register_blueprint(mcp_mode_bp)
    app.register_blueprint(rag_mode_bp)
    app.register_blueprint(multi_agent_mode_bp)

    return app


app = create_app()


if __name__ == "__main__":
    # Local dev only (containers run gunicorn). Container networking needs all interfaces;
    # the Werkzeug debugger only with FLASK_DEBUG=1 because it allows code execution.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5004")),  # noqa: S104 - container bind
            debug=os.environ.get("FLASK_DEBUG") == "1", use_reloader=False)