import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask
from flask_cors import CORS

from routes.accommodations import bp as accommodations_bp
from routes.ai import bp as ai_bp
from routes.mcp_mode import mcp_mode_bp
from routes.rag_mode import rag_mode_bp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)


def create_app():
    app = Flask(__name__)
    CORS(app)
    app.register_blueprint(accommodations_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(mcp_mode_bp)
    app.register_blueprint(rag_mode_bp)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True, use_reloader=False)
