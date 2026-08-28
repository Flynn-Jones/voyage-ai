"""Standalone entry point for the shared frontend homepage (port 3000)."""
import os

import requests
from flask import Flask, render_template

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCESS_SERVICE_URL = os.environ.get("ACCESS_SERVICE_URL", "http://localhost:5000")


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "css"),
    )

    @app.route("/")
    def index():
        nav = requests.get(f"{ACCESS_SERVICE_URL}/nav", timeout=5).json()
        return render_template("index.html", features=nav["features"])

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=3000)
