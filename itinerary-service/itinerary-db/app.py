import os

from flask import Flask, jsonify

from database import initialize_database


def create_app():
    initialize_database()
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"service": "itinerary-db", "status": "running"})

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "6005"))
    app.run(host="0.0.0.0", port=port, debug=False)
