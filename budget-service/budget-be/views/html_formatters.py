"""Shapes expense JSON into HTML fragments the frontend injects via innerHTML."""
from html import escape

FIELD_LABELS = [
    ("trip_reference", "Trip"),
    ("expense", "Expense"),
    ("category", "Category"),
    ("estimated_cost", "Estimated Cost"),
    ("actual_cost", "Actual Cost"),
    ("destination_id", "Destination ID"),
    ("status", "Status"),
]


def _fmt_cost(value):
    return f"${value:.2f}" if value is not None else "-"


def _row_cells(expense):
    return "".join(
        f"<td>{escape(str(_fmt_cost(expense.get(field)) if 'cost' in field else expense.get(field, '-')))}</td>"
        for field, _label in FIELD_LABELS
    )


def format_expense_list(expenses):
    if not expenses:
        return '<p class="empty">No expenses found.</p>'

    header_cells = "".join(f"<th>{label}</th>" for _field, label in FIELD_LABELS)
    rows = "".join(
        f'<tr data-expense-id="{expense["id"]}"><td>{expense["id"]}</td>{_row_cells(expense)}</tr>'
        for expense in expenses
    )

    return (
        '<table class="expense-table">'
        f"<thead><tr><th>ID</th>{header_cells}</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


def format_expense(expense):
    items = "".join(
        f"<dt>{label}</dt><dd>{escape(str(_fmt_cost(expense.get(field)) if 'cost' in field else expense.get(field, '-')))}</dd>"
        for field, label in FIELD_LABELS
    )
    return (
        f'<dl class="expense-detail" data-expense-id="{expense["id"]}">'
        f"<dt>ID</dt><dd>{expense['id']}</dd>"
        f"{items}"
        "</dl>"
    )


def format_error(message):
    return f'<p class="error">{escape(message)}</p>'
