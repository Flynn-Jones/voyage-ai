"""Destination Manager frontend (port 3001).

Server-rendered Flask + HTMX UI over the destination backend API. This
service never opens SQLite and never talks to the database API directly:
all persistence goes through destination-backend on port 5001, matching
the required data path frontend -> backend -> database API -> SQLite.
"""
import os

import requests
from flask import Flask, jsonify, redirect, render_template, request, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "3001"))
BACKEND_SERVICE_URL = os.environ.get("BACKEND_SERVICE_URL", "http://localhost:5001")
REQUEST_TIMEOUT = (3, 10)  # (connect, read) seconds

TEXT_FIELDS = ("city", "country", "description", "travel_style")
NUMERIC_FIELDS = {
    "average_daily_cost": float,
    "recommended_trip_length": int,
}

UNAVAILABLE_MESSAGE = "Destination service is unavailable. Please try again."

_OMIT = object()


class BackendError(Exception):
    """Raised when the backend cannot be reached or returns a server error."""


class BackendNotFound(BackendError):
    """Raised when the backend returns 404 for the requested resource."""


class BackendValidationError(BackendError):
    """Raised when the backend rejects a request as invalid (400)."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _read_error_message(response):
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and "error" in payload:
        return payload["error"]
    return response.text or "invalid request"


def backend_request(method, path, *, json_body=None, params=None):
    """Single exit point to the backend. Raises the typed errors above."""
    try:
        response = requests.request(
            method,
            f"{BACKEND_SERVICE_URL}{path}",
            timeout=REQUEST_TIMEOUT,
            json=json_body,
            params=params,
        )
    except requests.exceptions.RequestException as exc:
        raise BackendError(str(exc)) from exc

    if response.status_code == 404:
        raise BackendNotFound(_read_error_message(response))
    if response.status_code == 400:
        raise BackendValidationError(_read_error_message(response))
    if response.status_code >= 400:
        raise BackendError(f"backend returned {response.status_code} for {path}")

    return response


def fetch_all():
    return backend_request("GET", "/api/destinations").json()


def fetch_one(destination_id):
    return backend_request("GET", f"/api/destinations/{destination_id}").json()


def coerce(raw, caster):
    """Blank -> omit (leave unset). Parses -> typed value. Otherwise -> the
    raw string, forwarded as-is so the backend's own validation produces the
    error message (never a frontend traceback on bad numeric input)."""
    if raw is None or not raw.strip():
        return _OMIT
    stripped = raw.strip()
    try:
        return caster(stripped)
    except (TypeError, ValueError):
        return stripped


def parse_form_payload(form):
    """Convert submitted form fields into the backend's JSON field shape."""
    payload = {}

    for field in TEXT_FIELDS:
        if field in form:
            payload[field] = form.get(field, "").strip()

    for field, caster in NUMERIC_FIELDS.items():
        value = coerce(form.get(field), caster)
        if value is not _OMIT:
            payload[field] = value

    if "categories" in form:
        raw = form.get("categories", "")
        payload["categories"] = [c.strip() for c in raw.split(",") if c.strip()]

    return payload


def matches(row, needle):
    haystack = " ".join(
        [
            row.get("city") or "",
            row.get("country") or "",
            row.get("description") or "",
            row.get("travel_style") or "",
            " ".join(row.get("categories") or []),
        ]
    ).casefold()
    return needle.casefold() in haystack


def apply_search_and_filter(rows, q, country):
    """Pure filtering over an already-fetched list. The backend only
    supports exact-match filters on city/country/travel_style, so free-text
    search and substring matching happen here instead."""
    result = rows
    if country:
        result = [r for r in result if r.get("country") == country]
    if q:
        result = [r for r in result if matches(r, q)]
    return result


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
    )

    @app.route("/")
    def index():
        q = (request.args.get("q") or "").strip()
        country = (request.args.get("country") or "").strip()

        error = None
        rows = []
        countries = []
        try:
            all_rows = fetch_all()
            countries = sorted({r["country"] for r in all_rows if r.get("country")})
            rows = apply_search_and_filter(all_rows, q, country)
        except BackendError:
            error = UNAVAILABLE_MESSAGE

        context = dict(rows=rows, countries=countries, q=q, country=country, error=error)
        if request.headers.get("HX-Request") == "true":
            return render_template("_results.html", **context)
        return render_template("list.html", **context)

    @app.route("/destinations/new", methods=["GET", "POST"])
    def new_destination():
        if request.method == "GET":
            return render_template(
                "form.html", mode="create", destination={}, categories_text="", error=None
            )

        payload = parse_form_payload(request.form)
        try:
            created = backend_request("POST", "/api/destinations", json_body=payload).json()
        except BackendValidationError as exc:
            return (
                render_template(
                    "form.html",
                    mode="create",
                    destination=request.form,
                    categories_text=request.form.get("categories", ""),
                    error=exc.message,
                ),
                422,
            )
        except BackendError:
            return (
                render_template(
                    "form.html",
                    mode="create",
                    destination=request.form,
                    categories_text=request.form.get("categories", ""),
                    error=UNAVAILABLE_MESSAGE,
                ),
                422,
            )
        return redirect(url_for("destination_detail", destination_id=created["destination_id"]))

    @app.route("/destinations/<int:destination_id>")
    def destination_detail(destination_id):
        try:
            destination = fetch_one(destination_id)
        except BackendNotFound:
            return render_template("error.html", message="Destination not found."), 404
        except BackendError:
            return render_template("error.html", message=UNAVAILABLE_MESSAGE), 503
        return render_template("detail.html", destination=destination)

    @app.route("/destinations/<int:destination_id>/edit", methods=["GET", "POST"])
    def edit_destination(destination_id):
        if request.method == "GET":
            try:
                destination = fetch_one(destination_id)
            except BackendNotFound:
                return render_template("error.html", message="Destination not found."), 404
            except BackendError:
                return render_template("error.html", message=UNAVAILABLE_MESSAGE), 503
            categories_text = ", ".join(destination.get("categories") or [])
            return render_template(
                "form.html", mode="edit", destination=destination,
                categories_text=categories_text, error=None,
            )

        payload = parse_form_payload(request.form)
        destination = dict(request.form)
        destination["destination_id"] = destination_id
        categories_text = request.form.get("categories", "")
        try:
            backend_request("PUT", f"/api/destinations/{destination_id}", json_body=payload)
        except BackendNotFound:
            return render_template("error.html", message="Destination not found."), 404
        except BackendValidationError as exc:
            return (
                render_template(
                    "form.html", mode="edit", destination=destination,
                    categories_text=categories_text, error=exc.message,
                ),
                422,
            )
        except BackendError:
            return (
                render_template(
                    "form.html", mode="edit", destination=destination,
                    categories_text=categories_text, error=UNAVAILABLE_MESSAGE,
                ),
                422,
            )
        return redirect(url_for("destination_detail", destination_id=destination_id))

    @app.route("/destinations/<int:destination_id>/delete", methods=["GET", "POST"])
    def delete_destination(destination_id):
        if request.method == "GET":
            try:
                destination = fetch_one(destination_id)
            except BackendNotFound:
                return render_template("error.html", message="Destination not found."), 404
            except BackendError:
                return render_template("error.html", message=UNAVAILABLE_MESSAGE), 503
            return render_template("confirm_delete.html", destination=destination)

        try:
            backend_request("DELETE", f"/api/destinations/{destination_id}")
        except BackendNotFound:
            return render_template("error.html", message="Destination not found."), 404
        except BackendError:
            return render_template("error.html", message=UNAVAILABLE_MESSAGE), 503
        return redirect(url_for("index"))

    @app.route("/health")
    def health():
        return jsonify({"service": "destination-frontend", "status": "ok"})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
