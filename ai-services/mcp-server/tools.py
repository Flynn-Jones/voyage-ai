"""Tool implementations for the shared VoyageAI MCP server.

Runs locally (not containerised) and reaches feature data over the same
microservices-net HTTP ports each backend already uses, so it stays a genuine
single shared instance rather than logic duplicated per feature.
"""
import json
import os
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent  # ai-services/mcp-server -> ai-services -> repo root

BUDGET_DB_URL = os.environ.get("BUDGET_DB_URL", "http://localhost:6004")
ACCOMMODATION_DB_URL = os.environ.get("ACCOMMODATION_DB_URL", "http://localhost:6002")

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "chroma"}


def list_expenses(trip_reference: str = None):
    """Return budget expenses, optionally filtered to one trip."""
    params = {"trip_reference": trip_reference} if trip_reference else {}
    try:
        response = requests.get(f"{BUDGET_DB_URL}/expenses", params=params, timeout=5)
        response.raise_for_status()
        expenses = response.json()
    except requests.exceptions.RequestException as exc:
        return {"error": f"budget-db unavailable: {exc}"}

    return {"trip_reference": trip_reference, "count": len(expenses), "expenses": expenses}


def get_accommodation_by_destination(destination: str):
    """Return accommodation reference records matching a destination name."""
    destination = (destination or "").strip()
    if not destination:
        return {"error": "destination is required"}

    try:
        response = requests.get(
            f"{ACCOMMODATION_DB_URL}/accommodations", params={"q": destination}, timeout=5
        )
        if response.status_code == 404:
            return {"destination": destination, "count": 0, "accommodations": []}
        response.raise_for_status()
        data = response.json().get("data", [])
    except requests.exceptions.RequestException as exc:
        return {"error": f"accommodation-db unavailable: {exc}"}

    return {"destination": destination, "count": len(data), "accommodations": data}


def project_files(directory_path: str = "."):
    """List files/folders in a repository directory (relative to repo root)."""
    path = (REPO_ROOT / directory_path).resolve()

    try:
        path.relative_to(REPO_ROOT)
    except ValueError:
        return {"error": "directory_path must stay within the repository"}

    if not path.exists() or not path.is_dir():
        return {"error": f"Directory not found: {directory_path}"}

    items = sorted(item.name for item in path.iterdir() if item.name not in IGNORED_DIRS)
    return {"directory": directory_path, "items": items}


def ci_report(feature: str = "budget-service"):
    """Read the most recent local CI evidence report for a feature, if present."""
    report_path = REPO_ROOT / feature / "reports" / "report.json"
    if not report_path.exists():
        return {
            "error": "Report not found",
            "path": str(report_path.relative_to(REPO_ROOT)),
            "hint": f"Run the {feature} GitHub Actions workflow (workflow_dispatch) to generate report.json",
        }

    with report_path.open("r", encoding="utf-8") as file:
        return json.load(file)


if __name__ == "__main__":
    print(json.dumps(list_expenses(), indent=2))
    print(json.dumps(get_accommodation_by_destination("Tokyo"), indent=2))
    print(json.dumps(project_files("."), indent=2))
    print(json.dumps(ci_report("budget-service"), indent=2))
