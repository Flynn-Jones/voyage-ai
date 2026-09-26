from flask import Blueprint, jsonify, request

from services import database_api, destination_api

bp = Blueprint("accommodations", __name__)


def _forwarded_params():
    allowed = {
        "q",
        "destination_id",
        "destination_city",
        "type",
        "min_price",
        "max_price",
        "min_rating",
        "amenities",
        "sort_by",
        "limit",
        "offset",
    }
    return {key: value for key, value in request.args.items() if key in allowed}


@bp.get("/health")
def health():
    try:
        response = database_api.get_health()
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Accommodation database is unavailable."}), 502

    try:
        payload = response.json()
    except ValueError:
        payload = {"status": "unknown"}

    return jsonify(payload), response.status_code


@bp.get("/destinations")
def list_destinations():
    try:
        return jsonify(destination_api.list_destinations())
    except destination_api.DestinationNotFoundError:
        return jsonify({"error": "Destinations not found"}), 404
    except destination_api.DestinationUnavailableError:
        return jsonify({"error": "Destination service is unavailable."}), 503
    except destination_api.DestinationServiceError:
        return jsonify({"error": "Destination service is unavailable."}), 502


@bp.get("/accommodations")
def list_accommodations():
    try:
        data = database_api.list_accommodations(_forwarded_params())
    except database_api.NotFoundError:
        return jsonify({"error": "Accommodation not found"}), 404
    except database_api.ValidationError as exc:
        return jsonify({"error": str(exc)}), 422
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Accommodation database is unavailable."}), 502

    return jsonify(data)


@bp.get("/accommodations/<int:accommodation_id>")
def get_accommodation(accommodation_id):
    try:
        data = database_api.get_accommodation(accommodation_id)
    except database_api.NotFoundError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 404
    except database_api.ValidationError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 422
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Accommodation database is unavailable."}), 502

    return jsonify(data)


@bp.post("/accommodations")
def create_accommodation():
    payload = request.get_json(silent=True) or {}
    try:
        data = database_api.create_accommodation(payload)
    except database_api.NotFoundError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 404
    except database_api.ValidationError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 422
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Accommodation database is unavailable."}), 502

    return jsonify(data), 201


@bp.patch("/accommodations/<int:accommodation_id>")
def patch_accommodation(accommodation_id):
    payload = request.get_json(silent=True) or {}
    try:
        data = database_api.update_accommodation(accommodation_id, payload)
    except database_api.NotFoundError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 404
    except database_api.ValidationError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 422
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Accommodation database is unavailable."}), 502

    return jsonify(data)


@bp.put("/accommodations/<int:accommodation_id>")
def put_accommodation(accommodation_id):
    payload = request.get_json(silent=True) or {}
    try:
        data = database_api.replace_accommodation(accommodation_id, payload)
    except database_api.NotFoundError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 404
    except database_api.ValidationError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 422
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Accommodation database is unavailable."}), 502

    return jsonify(data)


@bp.delete("/accommodations/<int:accommodation_id>")
def delete_accommodation(accommodation_id):
    try:
        database_api.delete_accommodation(accommodation_id)
    except database_api.NotFoundError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 404
    except database_api.ValidationError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"error": str(exc)}
        return jsonify(payload), 422
    except database_api.DatabaseServiceError:
        return jsonify({"error": "Accommodation database is unavailable."}), 502

    return "", 204
