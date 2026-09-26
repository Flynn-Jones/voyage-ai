"""Stdio MCP server exposing activity-service's read-only tools over the Model Context Protocol.

Launched by MCP-aware clients (Claude Desktop, VS Code MCP extension) via
mcp-config.json. The containerised backend instead reaches the same tool logic
over HTTP via mcp_http_server.py, since a stdio server can't be dialled from
inside a Docker container.
"""
from mcp.server.fastmcp import FastMCP

from tools import (
    get_activity,
    get_activity_assignments,
    get_assignment,
    list_activities,
    list_assignments,
)

mcp = FastMCP("VoyageAI Activity MCP")
AVAILABLE_TOOLS = [
    "list_activities",
    "get_activity",
    "get_activity_assignments",
    "list_assignments",
    "get_assignment",
]


# FastMCP builds each tool's schema from the signature and docstring, so the
# docstrings below are what an LLM client actually sees when choosing a tool.
@mcp.tool(name="list_activities")
def list_activities_tool():
    """List every travel activity with its id, name, type, cost, and duration. Read-only."""
    return list_activities()


@mcp.tool(name="get_activity")
def get_activity_tool(activity_id: int):
    """Get one travel activity by its numeric activity_id. Read-only."""
    return get_activity(activity_id)


@mcp.tool(name="get_activity_assignments")
def get_activity_assignments_tool(activity_id: int):
    """Get the scheduled time assignment(s) for one activity by its numeric activity_id. Read-only."""
    return get_activity_assignments(activity_id)


@mcp.tool(name="list_assignments")
def list_assignments_tool():
    """List every activity time assignment (assignment_id, activity_id, assignment_time). Read-only."""
    return list_assignments()


@mcp.tool(name="get_assignment")
def get_assignment_tool(assignment_id: int):
    """Get one time assignment by its numeric assignment_id. Read-only."""
    return get_assignment(assignment_id)


if __name__ == "__main__":
    print("Starting VoyageAI Activity MCP Server...")
    print("Server status: RUNNING")
    print("Available tools:")
    for tool in AVAILABLE_TOOLS:
        print(f"- {tool}")
    mcp.run()
