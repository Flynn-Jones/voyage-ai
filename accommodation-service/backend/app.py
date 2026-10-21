from src.app import app, create_app  # noqa: F401 - create_app is re-exported for tests to import

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True, use_reloader=False)  # noqa: S104 - container bind
