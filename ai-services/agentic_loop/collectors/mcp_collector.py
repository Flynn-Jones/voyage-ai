"""Scans ai-services/mcp-server/ for the required files, tools, and a successful test call."""
import importlib.util
import sys
from pathlib import Path

REQUIRED_TOOLS = ["list_expenses", "get_accommodation_by_destination", "project_files", "ci_report"]


def _load_tools_module(mcp_server_dir: Path):
    spec = importlib.util.spec_from_file_location("mcp_tools_check", mcp_server_dir / "tools.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["mcp_tools_check"] = module
    spec.loader.exec_module(module)
    return module


def collect(app_dir: Path, repo_root: Path) -> tuple:
    mcp_server_dir = repo_root / "ai-services" / "mcp-server"

    required_paths = [
        mcp_server_dir / "tools.py",
        mcp_server_dir / "server.py",
        mcp_server_dir / "mcp_http_server.py",
        mcp_server_dir / "requirements.txt",
        repo_root / "prompts" / "mcp" / "implementation" / "tool_selection_prompt.txt",
        repo_root / "prompts" / "mcp" / "review" / "integration_review_prompt.txt",
    ]

    missing = [str(path.relative_to(repo_root)) for path in required_paths if not path.exists()]
    if missing:
        return False, "MCP evidence incomplete. Missing: " + ", ".join(missing)

    tools_text = (mcp_server_dir / "tools.py").read_text(encoding="utf-8")
    missing_tools = [tool for tool in REQUIRED_TOOLS if f"def {tool}" not in tools_text]
    if missing_tools:
        return False, "tools.py missing required tool functions: " + ", ".join(missing_tools)

    try:
        tools_module = _load_tools_module(mcp_server_dir)
        tools_module.list_expenses()
        tools_module.get_accommodation_by_destination("Tokyo")
        tools_module.project_files(".")
        tools_module.ci_report("budget-service")
    except Exception as exc:
        return False, f"MCP tool execution raised an error: {exc}"

    return True, (
        "MCP evidence: ai-services/mcp-server/ contains tools.py, server.py, and mcp_http_server.py; "
        f"{len(REQUIRED_TOOLS)} tools defined (list_expenses, get_accommodation_by_destination, "
        "project_files, ci_report); all 4 tools executed without raising; mcp prompts exist."
    )
