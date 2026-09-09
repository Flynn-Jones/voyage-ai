import os

import requests
from flask import Flask, redirect, render_template, request, url_for

BACKEND_SERVICE_URL = os.environ.get("BACKEND_SERVICE_URL", "http://activity-backend:5003")
PORT = int(os.environ.get("PORT", "3004"))


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    def backend_request(method, path, *, params=None, json_body=None):
        url = f"{BACKEND_SERVICE_URL}{path}"
        try:
            return requests.request(method, url, params=params or {}, json=json_body, timeout=(5, 60))
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Backend request failed: {exc}") from exc

    def fetch_activities():
        response = backend_request("GET", "/api/view_activities")
        if response.status_code >= 400:
            payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            raise RuntimeError(payload.get("error") or f"Backend returned {response.status_code}")
        return response.json()

    def fetch_activity(activity_id):
        response = backend_request("GET", f"/api/view_activity/{activity_id}")
        if response.status_code == 404:
            raise LookupError("activity not found")
        if response.status_code >= 400:
            payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            raise RuntimeError(payload.get("error") or f"Backend returned {response.status_code}")
        return response.json()

    def fetch_activity_assignments(activity_id):
        try:
            response = backend_request("GET", f"/api/activities/{activity_id}/assignments")
        except RuntimeError:
            return []
        if response.status_code >= 400:
            return []
        return response.json()

    def fetch_activity_summary(activity_id):
        response = backend_request("POST", "/api/activity/ai-summary", json_body={"activity_id": activity_id})
        if response.status_code >= 400:
            payload = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            raise RuntimeError(payload.get("error") or f"Backend returned {response.status_code}")
        return response.json().get("summary")

    app.config["backend_request"] = backend_request

    @app.route("/")
    def index():
        try:
            activities = fetch_activities()
            error = None
        except RuntimeError as exc:
            activities = []
            error = str(exc)
        return render_template("index.html", activities=activities, error=error)

    @app.route("/activities")
    def activities_list():
        try:
            activities = fetch_activities()
            error = None
        except RuntimeError as exc:
            activities = []
            error = str(exc)
        return render_template("activities_list.html", activities=activities, error=error)

    @app.route("/activities/<int:activity_id>")
    def activity_detail(activity_id):
        try:
            activity = fetch_activity(activity_id)
        except LookupError:
            return render_template("activity_detail.html", activity=None, error="Activity not found."), 404
        except RuntimeError as exc:
            return render_template("activity_detail.html", activity=None, error=str(exc)), 502

        assignments = fetch_activity_assignments(activity_id)
        if assignments:
            activity["assignment_time"] = assignments[0].get("assignment_time")
        return render_template("activity_detail.html", activity=activity, error=None)

    @app.route("/activities/<int:activity_id>/summary")
    def activity_summary(activity_id):
        try:
            summary = fetch_activity_summary(activity_id)
            return render_template("_activity_summary.html", summary=summary, error=None)
        except RuntimeError as exc:
            return render_template("_activity_summary.html", summary=None, error=str(exc))

    @app.route("/ai-mode")
    def ai_mode():
        return render_template("ai_mode.html")

    @app.route("/ai-mode/chat", methods=["POST"])
    def ai_mode_chat():
        message = request.form.get("message", "").strip()
        if not message:
            return "", 204

        try:
            response = backend_request("POST", "/api/activity/ai-chat", json_body={"message": message})
        except RuntimeError as exc:
            return render_template("_chat_exchange.html", message=message, reply=None, error=str(exc))

        if response.status_code >= 400:
            data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            error = data.get("error") or f"Request failed ({response.status_code})"
            return render_template("_chat_exchange.html", message=message, reply=None, error=error)

        reply = response.json().get("reply")
        return render_template("_chat_exchange.html", message=message, reply=reply, error=None)

    def parse_activity_form():
        return {
            "activity_name": request.form.get("activity_name", "").strip(),
            "activity_type": request.form.get("activity_type", "").strip(),
            "activity_cost": request.form.get("activity_cost", "").strip(),
            "duration": request.form.get("duration", "").strip(),
            "assignment_time": request.form.get("assignment_time", "").strip(),
        }

    def build_activity_payload(form_values):
        """Returns (payload, error) — error is a user-facing message, or None."""
        if not form_values["assignment_time"]:
            return None, "Please select a recommended/assigned time."
        try:
            cost = float(form_values["activity_cost"])
        except ValueError:
            return None, "Cost must be a number."
        return {**form_values, "activity_cost": cost}, None

    def is_hx_request():
        return request.headers.get("HX-Request") == "true"

    def render_form(activity, mode, error, status=200):
        # hx-post targets #form-container with hx-swap="outerHTML" — an HX
        # request must get just that fragment back, not a full document
        # nested inside it. A plain/no-JS POST still gets the full page.
        #
        # htmx does not swap non-2xx responses by default (shouldSwap is
        # false for 4xx/5xx) — so an HX request must get 200 even though
        # this is conceptually an error, or the error HTML we render never
        # actually reaches the page. The non-htmx fallback keeps the real
        # status code, since a plain browser POST renders the body
        # regardless of status.
        if is_hx_request():
            return render_template("_activity_form.html", activity=activity, mode=mode, error=error), 200
        return render_template("activity_form.html", activity=activity, mode=mode, error=error), status

    def redirect_to_detail(activity_id):
        if is_hx_request():
            # A raw 302 here would have htmx follow it via XHR and swap the
            # detail page's full document into #form-container. HX-Redirect
            # instead tells htmx to do a real client-side navigation.
            response = app.make_response(("", 200))
            response.headers["HX-Redirect"] = url_for("activity_detail", activity_id=activity_id)
            return response
        return redirect(url_for("activity_detail", activity_id=activity_id))

    @app.route("/activities/register", methods=["GET"])
    def register_activity_form():
        return render_template("activity_form.html", activity=None, mode="create", error=None)

    @app.route("/activities/register", methods=["POST"])
    def create_activity():
        form_values = parse_activity_form()
        payload, error = build_activity_payload(form_values)
        if error:
            return render_form(form_values, "create", error, 400)

        try:
            response = backend_request("POST", "/api/add_activity", json_body=payload)
        except RuntimeError as exc:
            return render_form(form_values, "create", str(exc), 502)

        if response.status_code >= 400:
            data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            error = data.get("error") or f"Backend returned {response.status_code}"
            return render_form(form_values, "create", error, 400)

        created = response.json()
        return redirect_to_detail(created["activity_id"])

    @app.route("/activities/<int:activity_id>/edit", methods=["GET"])
    def edit_activity_form(activity_id):
        try:
            activity = fetch_activity(activity_id)
        except LookupError:
            return render_template("activity_form.html", activity=None, mode="edit", error="Activity not found."), 404
        except RuntimeError as exc:
            return render_template("activity_form.html", activity=None, mode="edit", error=str(exc)), 502

        assignments = fetch_activity_assignments(activity_id)
        if assignments:
            activity["assignment_time"] = assignments[0].get("assignment_time")
        return render_template("activity_form.html", activity=activity, mode="edit", error=None)

    @app.route("/activities/<int:activity_id>/edit", methods=["POST"])
    def update_activity(activity_id):
        form_values = parse_activity_form()
        payload, error = build_activity_payload(form_values)
        activity_for_render = {**form_values, "activity_id": activity_id}
        if error:
            return render_form(activity_for_render, "edit", error, 400)

        try:
            response = backend_request("PATCH", f"/api/edit_activity/{activity_id}", json_body=payload)
        except RuntimeError as exc:
            return render_form(activity_for_render, "edit", str(exc), 502)

        if response.status_code >= 400:
            data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            error = data.get("error") or f"Backend returned {response.status_code}"
            return render_form(activity_for_render, "edit", error, 400)

        return redirect_to_detail(activity_id)

    # hx-delete only — never navigated to directly. Empty body on success (the
    # list card's hx-swap="outerHTML" replaces it with nothing; the detail
    # page's hx-on::after-request redirects to the list on success instead of
    # relying on a swap, since there's nothing sensible to swap into there).
    # Same route serves both callers; response shape doesn't need to know
    # which one is asking.
    @app.route("/activities/<int:activity_id>", methods=["DELETE"])
    def delete_activity(activity_id):
        try:
            response = backend_request("DELETE", f"/api/delete_activity/{activity_id}")
        except RuntimeError as exc:
            return str(exc), 502

        if response.status_code >= 400:
            data = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
            error = data.get("error") or f"Backend returned {response.status_code}"
            return error, response.status_code
        return "", 200

    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
