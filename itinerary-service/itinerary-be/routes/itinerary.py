from flask import Blueprint, jsonify, request

from services import database_api, enrichment
from validation import ValidationError, validate_itinerary_item


itinerary_bp = Blueprint("itinerary", __name__, url_prefix="/api/itinerary")


def _database_error():
    return jsonify({"error": "itinerary database service is unavailable"}), 502


def _not_found():
    return jsonify({"error": "itinerary item not found"}), 404


def _validated_request_body():
    return validate_itinerary_item(request.get_json(silent=True))


@itinerary_bp.get("")
def list_items():
    try:
        return jsonify(enrichment.enrich_items(database_api.get_items()))
    except database_api.DatabaseServiceError:
        return _database_error()


@itinerary_bp.get("/<int:item_id>")
def get_item(item_id):
    try:
        return jsonify(enrichment.enrich_item(database_api.get_item(item_id)))
    except database_api.NotFoundError:
        return _not_found()
    except database_api.DatabaseServiceError:
        return _database_error()


@itinerary_bp.post("")
def create_item():
    try:
        payload = _validated_request_body()
    except ValidationError as error:
        return jsonify({"error": str(error)}), 400

    try:
        return jsonify(database_api.create_item(payload)), 201
    except database_api.UpstreamValidationError as error:
        return jsonify({"error": str(error)}), 400
    except database_api.DatabaseServiceError:
        return _database_error()


@itinerary_bp.put("/<int:item_id>")
def update_item(item_id):
    try:
        payload = _validated_request_body()
    except ValidationError as error:
        return jsonify({"error": str(error)}), 400

    try:
        return jsonify(database_api.update_item(item_id, payload))
    except database_api.NotFoundError:
        return _not_found()
    except database_api.UpstreamValidationError as error:
        return jsonify({"error": str(error)}), 400
    except database_api.DatabaseServiceError:
        return _database_error()


@itinerary_bp.delete("/<int:item_id>")
def delete_item(item_id):
    try:
        database_api.delete_item(item_id)
    except database_api.NotFoundError:
        return _not_found()
    except database_api.DatabaseServiceError:
        return _database_error()
    return "", 204


@itinerary_bp.get("/day/<int:day>")
def list_items_by_day(day):
    if day < 1:
        return jsonify({"error": "day must be a positive integer"}), 400
    try:
        return jsonify(enrichment.enrich_items(database_api.get_items_by_day(day)))
    except database_api.DatabaseServiceError:
        return _database_error()
