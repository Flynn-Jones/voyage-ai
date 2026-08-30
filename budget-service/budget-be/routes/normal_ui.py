"""CRUD routes for the Normal UI tab. Calls database_api.py, renders through html_formatters.py."""
from flask import Blueprint, jsonify, request

from services import database_api
from views import html_formatters

normal_ui_bp = Blueprint("normal_ui", __name__)

FILTERABLE_FIELDS = ("category", "destination_id", "status")


@normal_ui_bp.route("/")
def health():
    return jsonify({"service": "budget-be", "status": "running"})


@normal_ui_bp.route("/expenses")
def list_expenses():
    filters = {key: value for key, value in request.args.items() if key in FILTERABLE_FIELDS}
    try:
        expenses = database_api.get_expenses(filters)
    except database_api.DatabaseServiceError:
        return html_formatters.format_error("Budget database is unavailable."), 502
    return html_formatters.format_expense_list(expenses)


@normal_ui_bp.route("/expenses/<int:expense_id>")
def get_expense(expense_id):
    try:
        expense = database_api.get_expense(expense_id)
    except database_api.NotFoundError:
        return html_formatters.format_error("Expense not found."), 404
    except database_api.DatabaseServiceError:
        return html_formatters.format_error("Budget database is unavailable."), 502
    return html_formatters.format_expense(expense)


@normal_ui_bp.route("/expenses", methods=["POST"])
def create_expense():
    payload = request.get_json(silent=True) or {}
    try:
        expense = database_api.create_expense(payload)
    except database_api.ValidationError as exc:
        return html_formatters.format_error(str(exc)), 400
    except database_api.DatabaseServiceError:
        return html_formatters.format_error("Budget database is unavailable."), 502
    return html_formatters.format_expense(expense), 201


@normal_ui_bp.route("/expenses/<int:expense_id>", methods=["PUT"])
def update_expense(expense_id):
    payload = request.get_json(silent=True) or {}
    try:
        expense = database_api.update_expense(expense_id, payload)
    except database_api.NotFoundError:
        return html_formatters.format_error("Expense not found."), 404
    except database_api.ValidationError as exc:
        return html_formatters.format_error(str(exc)), 400
    except database_api.DatabaseServiceError:
        return html_formatters.format_error("Budget database is unavailable."), 502
    return html_formatters.format_expense(expense)


@normal_ui_bp.route("/expenses/<int:expense_id>", methods=["DELETE"])
def delete_expense(expense_id):
    try:
        database_api.delete_expense(expense_id)
    except database_api.NotFoundError:
        return html_formatters.format_error("Expense not found."), 404
    except database_api.DatabaseServiceError:
        return html_formatters.format_error("Budget database is unavailable."), 502
    return "", 204
