"""Checks activity-service/mcp-server/ and runs all 5 of its tools against the live activities database.

Unlike mcp_collector, a tool that returns {"error": ...} counts as a failure
here, so OBSERVE can't report success while the database is down.
"""
import importlib.util
import json
import sys
from pathlib import Path

import requests

TOOLS = ["list_activities", "get_activity", "get_activity_assignments", "list_assignments", "get_assignment"]
WRITE_TOOLS = ["add_activity", "edit_activity", "delete_activity"]
MCP_HTTP_HEALTH_URL = "http://localhost:7003/health"


def _load_tools_module(mcp_server_dir: Path):
    # "tools" would collide with other features' MCP modules, hence the alias
    spec = importlib.util.spec_from_file_location("activity_mcp_tools_check", mcp_server_dir / "tools.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["activity_mcp_tools_check"] = module
    spec.loader.exec_module(module)
    return module


def _run_tools(tools) -> dict:
    activities = tools.list_activities()
    first_id = (activities.get("activities") or [{}])[0].get("activity_id", 1)
    assignments = tools.list_assignments()
    first_assignment = (assignments.get("assignments") or [{}])[0].get("assignment_id", 1)
    return {
        "list_activities": activities,
        "get_activity": tools.get_activity(first_id),
        "get_activity_assignments": tools.get_activity_assignments(first_id),
        "list_assignments": assignments,
        "get_assignment": tools.get_assignment(first_assignment),
    }


def _http_server_status() -> str:
    try:
        tools = requests.get(MCP_HTTP_HEALTH_URL, timeout=3).json().get("tools", [])
    except (requests.exceptions.RequestException, ValueError) as exc:
        return f"MCP HTTP server (:7003) unreachable: {exc}"
    return f"MCP HTTP server (:7003) healthy with tools {tools}"


def collect(app_dir: Path, repo_root: Path) -> tuple[bool, str]:
    service_dir = repo_root / "activity-service"
    mcp_server_dir = service_dir / "mcp-server"
    required_paths = [
        mcp_server_dir / "tools.py",
        mcp_server_dir / "server.py",
        mcp_server_dir / "mcp_http_server.py",
        mcp_server_dir / "mcp-config.json",
        service_dir / "prompts" / "mcp" / "implementation" / "mcp_validation_prompt.txt",
        service_dir / "prompts" / "mcp" / "review" / "integration_review_prompt.txt",
    ]
    missing = [str(path.relative_to(repo_root)) for path in required_paths if not path.exists()]
    if missing:
        return False, "Activity MCP evidence incomplete. Missing: " + ", ".join(missing)

    server_text = (mcp_server_dir / "server.py").read_text(encoding="utf-8")
    missing_tools = [tool for tool in TOOLS if f'name="{tool}"' not in server_text]
    write_tools = [tool for tool in WRITE_TOOLS if tool in server_text]
    if missing_tools or write_tools:
        return False, f"server.py tool registry wrong: missing={missing_tools}, write tools present={write_tools}"

    try:
        results = _run_tools(_load_tools_module(mcp_server_dir))
    except Exception as exc:
        return False, f"Activity MCP tool execution raised an error: {exc}"

    failed = {name: result["error"] for name, result in results.items() if "error" in result}
    if failed:
        return False, f"Activity MCP tools returned errors (is database-service on :6003 up?): {failed}"

    sample = json.dumps(results["get_activity"]["activity"])
    return True, (
        "Activity MCP evidence: activity-service/mcp-server/ has tools.py, server.py (FastMCP stdio), "
        f"mcp_http_server.py and mcp-config.json; server.py registers exactly {TOOLS}; no write tools. "
        f"All 5 tools executed against the live database without errors: "
        f"list_activities count={results['list_activities']['count']}, "
        f"list_assignments count={results['list_assignments']['count']}, "
        f"get_activity -> {sample}, "
        f"get_activity_assignments count={results['get_activity_assignments']['count']}, "
        f"get_assignment -> {json.dumps(results['get_assignment']['assignment'])}. "
        f"{_http_server_status()}."
    )
