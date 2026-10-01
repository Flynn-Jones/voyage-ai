import json
import os
from typing import Any

import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify

BACKEND_SERVICE_URL = os.environ.get(
    "BACKEND_SERVICE_URL",
    os.environ.get("ACCOMMODATION_BACKEND_URL", "http://accomodation-be:5002"),
)
PORT = int(os.environ.get("PORT", "3002"))


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    def backend_request(method: str, path: str, *, params=None, json_body=None):
        url = f"{BACKEND_SERVICE_URL}{path}"
        try:
            response = requests.request(
                method,
                url,
                params=params or {},
                json=json_body,
                timeout=(5, 300),
            )
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Backend request failed: {exc}") from exc

        return response

    def parse_float(value):
        if value is None or value == "":
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def parse_form_payload():
        payload = {
            "name": request.form.get("name", "").strip(),
            "destination_id": request.form.get("destination_id", "").strip() or None,
            "destination_city": request.form.get("destination_city", "").strip() or None,
            "type": request.form.get("type", "").strip().lower() or None,
            "price_per_night": parse_float(request.form.get("price_per_night")),
            "rating": parse_float(request.form.get("rating")),
            "location": request.form.get("location", "").strip() or None,
            "latitude": parse_float(request.form.get("latitude")),
            "longitude": parse_float(request.form.get("longitude")),
            "description": request.form.get("description", "").strip() or None,
        }
        amenities = request.form.get("amenities", "")
        if amenities:
            payload["amenities"] = [item.strip() for item in amenities.split(",") if item.strip()]
        return {k: v for k, v in payload.items() if v is not None}

    def fetch_accommodations(params=None):
        response = backend_request("GET", "/accommodations", params=params or {})
        if response.status_code >= 400:
            payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            raise RuntimeError(payload.get("error") or payload.get("message") or "Unable to load accommodations.")
        return response.json()

    def fetch_accommodation(accommodation_id):
        response = backend_request("GET", f"/accommodations/{accommodation_id}")
        if response.status_code >= 400:
            payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            raise RuntimeError(payload.get("error") or payload.get("message") or "Accommodation not found.")
        return response.json()

    def fetch_destinations():
        try:
            response = backend_request("GET", "/destinations")
            if response.status_code >= 400:
                return []
            payload = response.json()
            return payload if isinstance(payload, list) else payload.get("data", [])
        except (RuntimeError, ValueError, AttributeError):
            return []

    @app.route("/")
    def index():
        filters = {
            "q": request.args.get("q"),
            "destination_city": request.args.get("destination_city"),
            "type": request.args.get("type"),
            "min_price": request.args.get("min_price"),
            "max_price": request.args.get("max_price"),
            "min_rating": request.args.get("min_rating"),
            "sort_by": request.args.get("sort_by"),
        }
        filters = {k: v for k, v in filters.items() if v not in (None, "")}
        destinations = fetch_destinations()
        try:
            result = fetch_accommodations(filters)
        except RuntimeError as exc:
            return render_template("list.html", accommodations=[], filters=filters, destinations=destinations, error=str(exc), htmx_mode=False)

        accommodations = result.get("data", [])
        if request.headers.get("HX-Request") == "true":
            return render_template("_list_table.html", accommodations=accommodations, filters=filters)
        return render_template("list.html", accommodations=accommodations, filters=filters, destinations=destinations, error=None, htmx_mode=False)

    @app.route("/accommodations/new")
    def new_accommodation_form():
        return render_template("form.html", accommodation=None, destinations=fetch_destinations(), mode="create", error=None)

    @app.route("/accommodations", methods=["POST"])
    def create_accommodation():
        payload = parse_form_payload()
        response = backend_request("POST", "/accommodations", json_body=payload)
        if response.status_code >= 400:
            data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            error_message = data.get("error") or data.get("message") or "Validation failed."
            if request.headers.get("HX-Request") == "true":
                return render_template("form.html", accommodation=payload, destinations=fetch_destinations(), mode="create", error=error_message), 422
            return render_template("form.html", accommodation=payload, destinations=fetch_destinations(), mode="create", error=error_message), 422
        if request.headers.get("HX-Request") == "true":
            return redirect(url_for("index"))
        return redirect(url_for("index"))

    @app.route("/accommodations/<int:accommodation_id>")
    def accommodation_detail(accommodation_id):
        try:
            accommodation = fetch_accommodation(accommodation_id)
        except RuntimeError as exc:
            return render_template("detail.html", accommodation=None, error=str(exc)), 404
        return render_template("detail.html", accommodation=accommodation, error=None)

    @app.route("/accommodations/<int:accommodation_id>/edit")
    def edit_accommodation_form(accommodation_id):
        try:
            accommodation = fetch_accommodation(accommodation_id)
        except RuntimeError as exc:
            return render_template("form.html", accommodation=None, destinations=fetch_destinations(), mode="edit", error=str(exc)), 404
        return render_template("form.html", accommodation=accommodation, destinations=fetch_destinations(), mode="edit", error=None)

    @app.route("/accommodations/<int:accommodation_id>", methods=["PATCH", "POST"])
    def update_accommodation(accommodation_id):
        payload = parse_form_payload()
        response = backend_request("PATCH", f"/accommodations/{accommodation_id}", json_body=payload)
        if response.status_code >= 400:
            data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            error_message = data.get("error") or data.get("message") or "Validation failed."
            if request.headers.get("HX-Request") == "true":
                return render_template("form.html", accommodation={**payload, "id": accommodation_id}, destinations=fetch_destinations(), mode="edit", error=error_message), 422
            return render_template("form.html", accommodation={**payload, "id": accommodation_id}, destinations=fetch_destinations(), mode="edit", error=error_message), 422
        return redirect(url_for("index"))

    @app.route("/accommodations/<int:accommodation_id>/delete", methods=["POST"])
    def delete_accommodation(accommodation_id):
        response = backend_request("DELETE", f"/accommodations/{accommodation_id}")
        if response.status_code >= 400:
            payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            error_message = payload.get("error") or payload.get("message") or "Delete failed."
            if request.headers.get("HX-Request") == "true":
                return error_message, 400
            return redirect(url_for("index"))
        return redirect(url_for("index"))

    @app.route("/recommend")
    def recommend_page():
        return render_template("recommend.html", destinations=fetch_destinations(), recommendation=None, error=None)

    @app.route("/recommend", methods=["POST"])
    def recommend():
        payload = {
            "destination_city": request.form.get("destination_city", "").strip(),
            "max_price": parse_float(request.form.get("max_price")),
            "interests": [item.strip().lower() for item in request.form.get("interests", "").split(",") if item.strip()],
        }
        if not payload["destination_city"]:
            return render_template("recommend.html", destinations=fetch_destinations(), recommendation=None, error="Destination city is required."), 400

        response = backend_request("POST", "/ai/recommend", json_body=payload)
        if response.status_code >= 400:
            data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            error_message = data.get("error") or "Recommendation service is unavailable."
            return render_template("recommend.html", destinations=fetch_destinations(), recommendation=None, error=error_message), 503

        data = response.json()
        raw_recommendation = data.get("recommendations") or data.get("recommendation") or []
        if isinstance(raw_recommendation, dict):
            nested_recommendations = raw_recommendation.get("recommendations")
            if isinstance(nested_recommendations, list):
                recommendations = nested_recommendations
            else:
                recommendations = [raw_recommendation]
        elif isinstance(raw_recommendation, list):
            recommendations = raw_recommendation
        else:
            recommendations = []

        if not recommendations and isinstance(data.get("recommendations"), list):
            recommendations = data.get("recommendations")

        recommendations = recommendations[:3]

        primary_recommendation = recommendations[0] if recommendations else {}
        result = {
            "plan": data.get("plan"),
            "act_result_count": data.get("act_result_count"),
            "observation": data.get("observation"),
            "recommendation": primary_recommendation,
            "recommendations": recommendations,
        }
        return render_template("recommend.html", destinations=fetch_destinations(), recommendation=result, error=None)

    ACCOMMODATION_TYPES = ["hotel", "hostel", "ryokan", "apartment", "guesthouse"]
    AMENITY_OPTIONS = ['24-Hour Front Desk', 'Air Conditioning', 'Bar', 'Breakfast Included', 'Gym', 'Onsen', 'Parking', 'Pool', 'Rooftop Terrace', 'WiFi']

    def _mcp_error_message(data):
        """Surface the tool's own error and hint instead of a generic failure line."""
        message = data.get("error")
        inner = data.get("result")
        if not message and isinstance(inner, dict):
            message = inner.get("error")
        if isinstance(inner, dict) and inner.get("hint"):
            message = f"{message or 'MCP tool call failed.'} — {inner['hint']}"
        return message or "MCP tool call failed."

    def render_mcp(**overrides):
        context = {
            "text": "",
            "draft": None,
            "missing": None,
            "created": None,
            "tool_calls": None,
            "ignored_amenities": None,
            "error": None,
            "field_error": None,
            "types": ACCOMMODATION_TYPES,
            "amenity_options": AMENITY_OPTIONS,
        }
        context.update(overrides)
        return render_template("mcp.html", **context)

    def _draft_from_form():
        def number(field):
            raw = request.form.get(field, "").strip()
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        return {
            "name": request.form.get("name", "").strip() or None,
            "destination_city": request.form.get("destination_city", "").strip() or None,
            "destination_id": request.form.get("destination_id", "").strip() or None,
            "accommodation_type": request.form.get("accommodation_type", "").strip() or None,
            "price_per_night": number("price_per_night"),
            "rating": number("rating"),
            "location": request.form.get("location", "").strip() or None,
            "description": request.form.get("description", "").strip() or None,
            "amenities": request.form.getlist("amenities"),
        }

    @app.route("/mcp", methods=["GET", "POST"])
    def mcp_page():
        if request.method == "GET":
            return render_mcp()

        action = request.form.get("action", "draft")
        text = request.form.get("text", "").strip()

        if action == "create":
            draft = _draft_from_form()
            missing = [f for f in ("name", "destination_id", "price_per_night")
                       if draft.get(f) in (None, "")]
            if missing:
                return render_mcp(text=text, draft=draft, missing=missing,
                                  field_error="Fill in " + ", ".join(missing) + " before creating."), 400

            payload = {k: v for k, v in draft.items() if v not in (None, "", [])}
            try:
                response = backend_request("POST", "/accommodation/mcp/create", json_body=payload)
            except RuntimeError as exc:
                return render_mcp(text=text, draft=draft, error=str(exc)), 502

            data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            if response.status_code >= 400:
                return render_mcp(text=text, draft=draft, error=_mcp_error_message(data),
                                  tool_calls=data.get("tool_calls")), response.status_code

            result = data.get("result", {})
            return render_mcp(text=text, created=result.get("created"),
                              tool_calls=result.get("tool_calls"),
                              ignored_amenities=result.get("ignored_amenities"))

        # default: draft from plain text
        if not text:
            return render_mcp(field_error="Describe the accommodation you want to add."), 400

        try:
            response = backend_request("POST", "/accommodation/mcp/draft", json_body={"text": text})
        except RuntimeError as exc:
            return render_mcp(text=text, error=str(exc)), 502

        data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
        if response.status_code >= 400:
            return render_mcp(text=text, error=_mcp_error_message(data)), response.status_code

        result = data.get("result", {})
        return render_mcp(text=text, draft=result.get("draft"), missing=result.get("missing_required"))

    def render_rag(**overrides):
        context = {
            "query": "", "answer": None, "citations": None, "confidence": None,
            "stays": None, "filters": None, "matched_by": None, "records_error": None,
            "raw_result": None, "raw_title": None, "error": None,
        }
        context.update(overrides)
        return render_template("rag.html", **context)

    @app.route("/rag", methods=["GET", "POST"])
    def rag_page():
        if request.method == "GET":
            return render_rag()

        action = request.form.get("action", "ask")
        query = request.form.get("query", "").strip()

        if action in ("ask", "retrieve") and not query:
            return render_rag(query=query, error="A question is required."), 400

        paths = {"ask": "/accommodation/rag/ask",
                 "retrieve": "/accommodation/rag/retrieve",
                 "refresh": "/accommodation/rag/refresh"}
        if action not in paths:
            return render_rag(query=query, error=f"Unknown action: {action}"), 400

        body = {} if action == "refresh" else {"query": query, "k": 5}
        try:
            response = backend_request("POST", paths[action], json_body=body)
        except RuntimeError as exc:
            return render_rag(query=query, error=str(exc)), 502

        data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
        if response.status_code >= 400 or data.get("status") == "error":
            message = data.get("error") or "RAG request failed."
            status = response.status_code if response.status_code >= 400 else 502
            return render_rag(query=query, error=message), status

        if action == "ask":
            result = data.get("result", {})
            return render_rag(
                query=query,
                answer=result.get("answer"),
                citations=result.get("citations") or [],
                confidence=result.get("confidence_category"),
                stays=result.get("accommodations") or [],
                filters=result.get("filters") or {},
                matched_by=result.get("matched_by"),
                records_error=result.get("records_error"),
            )

        titles = {"retrieve": "Retrieved context", "refresh": "Corpus refresh"}
        payload = data.get("results") if action == "retrieve" else data
        return render_rag(query=query, raw_result=json.dumps(payload, indent=2), raw_title=titles[action])

    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
