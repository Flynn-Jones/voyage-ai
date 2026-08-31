import os
from flask import Flask
from routes.accommodations import bp as accommodations_bp
from routes.health import bp as health_bp


def create_app():
    app = Flask(__name__)
    app.register_blueprint(health_bp)
    app.register_blueprint(accommodations_bp)
    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 6002))
    app.run(host="0.0.0.0", port=port, debug=True)
