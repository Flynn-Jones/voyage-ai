"""Preserve the Student 5 evidence pack and record actual CI status, including failures."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def main():
    directory = Path("itinerary-service/reports")
    directory.mkdir(parents=True, exist_ok=True)
    run_id = os.environ.get("GITHUB_RUN_ID", "not-run-locally")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "Flynn-Jones/voyage-ai")
    run_url = f"{server}/{repository}/actions/runs/{run_id}" if run_id != "not-run-locally" else "Pending GitHub Actions execution"
    report = {
        "workflow_name": "student-5",
        "run_id": run_id,
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "run_url": run_url,
        "commit_sha": os.environ.get("GITHUB_SHA", "Pending GitHub Actions execution"),
        "branch": os.environ.get("GITHUB_REF_NAME", "local validation"),
        "generated_timestamp": datetime.now(timezone.utc).isoformat(),
        "validation_status": os.environ.get("VALIDATION_STATUS", "local-only"),
        "validation": "Release 0/1/2 Python and contract tests, Compose, image builds, health, CRUD smoke, "
                      "live POST/PUT persistence and disabled AI/MCP/RAG/Multi-Agent checks; consult status and JUnit results",
        "endpoint_functions": ["POST /api/itinerary (create_item)", "PUT /api/itinerary/<id> (update_item)"],
        "endpoint_evidence": ["post-commit/junit-endpoint-tests.xml", "post-commit/endpoint-tests.txt", "post-commit/http-results.json"],
    }
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (directory / "report.md").write_text(
        "## Student 5 (Itinerary Manager) Workflow Report\n\n"
        + "\n".join(f"- {key}: {report[key]}" for key in ("workflow_name", "run_id", "run_url", "commit_sha", "branch", "validation_status"))
        + "\n- AI/MCP/RAG/Multi-Agent are disabled in the real CI containers; no host AI services or models are required.\n"
        "- Existing enabled integration paths remain covered by isolated tests.\n"
        "- See post-commit/ for live endpoint JUnit, HTTP results and test output; failures remain failures.\n",
        encoding="utf-8",
    )
    (directory / "run-view.md").write_text(run_url + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
